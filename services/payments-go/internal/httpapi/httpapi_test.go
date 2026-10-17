package httpapi

import (
	"bytes"
	"context"
	"errors"
	"log/slog"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"

	"github.com/aindrajaya/imageopz-server/services/payments-go/internal/dedupe"
	"github.com/aindrajaya/imageopz-server/services/payments-go/internal/signature"
)

const secret = "whsec"

// memStore models the delivery table with real transaction semantics: rows become visible on Commit only.
type memStore struct {
	rows      map[dedupe.Key]string
	beginErr  error
	pingErr   error
	commitErr error
	rollbacks int
	begins    int
}

type memTx struct {
	s       *memStore
	pending map[dedupe.Key]string
}

func newStore() *memStore { return &memStore{rows: map[dedupe.Key]string{}} }

func (s *memStore) Ping(context.Context) error { return s.pingErr }
func (s *memStore) Begin(context.Context) (Tx, error) {
	s.begins++
	if s.beginErr != nil {
		return nil, s.beginErr
	}
	return &memTx{s: s, pending: map[dedupe.Key]string{}}, nil
}
func (t *memTx) Claim(_ context.Context, k dedupe.Key, name string) (bool, error) {
	if _, ok := t.s.rows[k]; ok {
		return false, nil
	}
	t.pending[k] = name
	return true, nil
}
func (t *memTx) Commit(context.Context) error {
	if t.s.commitErr != nil {
		return t.s.commitErr
	}
	for k, v := range t.pending {
		t.s.rows[k] = v
	}
	return nil
}
func (t *memTx) Rollback(context.Context) error { t.s.rollbacks++; t.pending = nil; return nil }

type fakeEnq struct {
	mu     sync.Mutex
	calls  int
	bodies [][]byte
	err    error
}

func (f *fakeEnq) TaskName(k dedupe.Key) string { return "q/tasks/" + k.Hash() }
func (f *fakeEnq) Enqueue(_ context.Context, _ dedupe.Key, b []byte) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.calls++
	f.bodies = append(f.bodies, b)
	return f.err
}

func setup() (*Handler, *memStore, *fakeEnq, *bytes.Buffer) {
	s, e, logs := newStore(), &fakeEnq{}, &bytes.Buffer{}
	h := &Handler{Secret: secret, Store: s, Enqueuer: e, Log: slog.New(slog.NewTextHandler(logs, nil))}
	return h, s, e, logs
}

func post(h *Handler, body []byte, sig string) *httptest.ResponseRecorder {
	r := httptest.NewRequest("POST", "/webhooks/lemonsqueezy", bytes.NewReader(body))
	if sig != "" {
		r.Header.Set("X-Signature", sig)
	}
	w := httptest.NewRecorder()
	h.Routes().ServeHTTP(w, r)
	return w
}

func signed(h *Handler, body string) *httptest.ResponseRecorder {
	return post(h, []byte(body), signature.Sign(secret, []byte(body)))
}

const created = `{"meta":{"event_name":"order_created"},"data":{"type":"orders","id":"42","attributes":{"user_email":"buyer@example.com"}}}`

func TestAcceptedThenDuplicate(t *testing.T) {
	h, s, e, _ := setup()
	w := signed(h, created)
	if w.Code != 200 || !strings.Contains(w.Body.String(), `"accepted"`) || len(s.rows) != 1 || e.calls != 1 {
		t.Fatalf("first: %d %s rows=%d enq=%d", w.Code, w.Body, len(s.rows), e.calls)
	}
	if string(e.bodies[0]) != created {
		t.Fatal("enqueued body must be the original raw payload")
	}
	for i := 0; i < 2; i++ {
		w = signed(h, created)
		if w.Code != 200 || !strings.Contains(w.Body.String(), `"duplicate"`) {
			t.Fatalf("replay: %d %s", w.Code, w.Body)
		}
	}
	if len(s.rows) != 1 || e.calls != 1 {
		t.Fatalf("replays must not add rows or enqueue: rows=%d enq=%d", len(s.rows), e.calls)
	}
}

func TestAliasEventSharesDedupeKey(t *testing.T) {
	h, s, e, _ := setup()
	signed(h, created)
	w := signed(h, strings.Replace(created, "order_created", "order:created", 1))
	if !strings.Contains(w.Body.String(), `"duplicate"`) || len(s.rows) != 1 || e.calls != 1 {
		t.Fatalf("alias must dedupe: %s rows=%d enq=%d", w.Body, len(s.rows), e.calls)
	}
}

func TestUnauthorized(t *testing.T) {
	tests := []struct{ name, sig, secret string }{
		{"missing", "", secret}, {"bad", "abcd", secret}, {"empty server secret", "", ""},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			h, s, e, _ := setup()
			h.Secret = tc.secret
			sig := tc.sig
			if tc.secret == "" {
				sig = signature.Sign("", []byte(created)) // a signature made with an empty key must still be refused
			}
			w := post(h, []byte(created), sig)
			if w.Code != 401 || len(s.rows) != 0 || e.calls != 0 || s.begins != 0 {
				t.Fatalf("got %d rows=%d enq=%d begins=%d", w.Code, len(s.rows), e.calls, s.begins)
			}
		})
	}
}

func TestIgnored(t *testing.T) {
	bodies := map[string]string{
		"no event name": `{"meta":{},"data":{"type":"orders","id":"1"}}`,
		"no id":         `{"meta":{"event_name":"order_created"},"data":{"type":"orders"}}`,
		"not json":      `not json`,
		"array":         `[]`,
	}
	for name, b := range bodies {
		t.Run(name, func(t *testing.T) {
			h, s, e, logs := setup()
			w := signed(h, b)
			if w.Code != 200 || !strings.Contains(w.Body.String(), `"ignored"`) || len(s.rows) != 0 || e.calls != 0 || s.begins != 0 {
				t.Fatalf("got %d %s rows=%d enq=%d begins=%d", w.Code, w.Body, len(s.rows), e.calls, s.begins)
			}
			if !strings.Contains(logs.String(), "level=WARN") {
				t.Fatalf("ignored must log a warning: %s", logs)
			}
		})
	}
}

func TestEnqueueFailureRollsBackAndRetrySucceeds(t *testing.T) {
	h, s, e, logs := setup()
	e.err = errors.New("tasks api down: secret-ish-detail")
	w := signed(h, created)
	if w.Code != 503 || strings.Contains(w.Body.String(), "secret-ish") || len(s.rows) != 0 || s.rollbacks != 1 {
		t.Fatalf("got %d %s rows=%d rollbacks=%d", w.Code, w.Body, len(s.rows), s.rollbacks)
	}
	e.err = nil // sender retries
	w = signed(h, created)
	if w.Code != 200 || !strings.Contains(w.Body.String(), `"accepted"`) || len(s.rows) != 1 {
		t.Fatalf("retry: %d %s rows=%d", w.Code, w.Body, len(s.rows))
	}
	if strings.Contains(logs.String(), "buyer@example.com") {
		t.Fatal("logs must not contain customer email")
	}
}

func TestStoreFailures(t *testing.T) {
	h, s, e, _ := setup()
	s.beginErr = errors.New("db down")
	if w := signed(h, created); w.Code != 503 || e.calls != 0 {
		t.Fatalf("begin failure: %d enq=%d", w.Code, e.calls)
	}
	s.beginErr, s.commitErr = nil, errors.New("commit failed")
	w := signed(h, created)
	if w.Code != 503 || len(s.rows) != 0 || e.calls != 1 {
		t.Fatalf("commit failure after enqueue: %d rows=%d enq=%d", w.Code, len(s.rows), e.calls)
	}
}

func TestBodyLimit(t *testing.T) {
	h, s, e, _ := setup()
	big := bytes.Repeat([]byte("a"), MaxBodyBytes+1)
	w := post(h, big, signature.Sign(secret, big))
	if w.Code != 413 || len(s.rows) != 0 || e.calls != 0 {
		t.Fatalf("got %d", w.Code)
	}
	atLimit := bytes.Repeat([]byte("a"), MaxBodyBytes)
	if w := post(h, atLimit, signature.Sign(secret, atLimit)); w.Code != 200 { // valid signature, unextractable -> ignored
		t.Fatalf("at limit: %d", w.Code)
	}
}

func TestHealthAndReady(t *testing.T) {
	h, s, _, _ := setup()
	get := func(path string) int {
		w := httptest.NewRecorder()
		h.Routes().ServeHTTP(w, httptest.NewRequest("GET", path, nil))
		return w.Code
	}
	s.pingErr = errors.New("db down")
	if get("/health") != 200 || s.begins != 0 {
		t.Fatal("health must not touch the DB")
	}
	if get("/ready") != 503 {
		t.Fatal("ready must fail when the DB is down")
	}
	s.pingErr = nil
	if get("/ready") != 200 {
		t.Fatal("ready must pass when the DB is up")
	}
	w := httptest.NewRecorder()
	h.Routes().ServeHTTP(w, httptest.NewRequest("GET", "/webhooks/lemonsqueezy", nil))
	if w.Code != 405 {
		t.Fatalf("GET webhook: %d", w.Code)
	}
	// Cloud Run reserves some paths ending in "z" (known issues, "Reserved URL paths"): the old names stay unserved.
	if get("/healthz") != 404 || get("/readyz") != 404 {
		t.Fatal("healthz and readyz must not be served")
	}
}
