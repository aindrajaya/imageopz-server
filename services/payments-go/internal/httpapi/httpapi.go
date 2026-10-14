// Package httpapi is the webhook ingress: verify, dedupe, enqueue.
package httpapi

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"net/http"
	"time"

	"github.com/jackc/pgx/v5"

	"github.com/aindrajaya/imageopz-server/services/payments-go/internal/dedupe"
	"github.com/aindrajaya/imageopz-server/services/payments-go/internal/enqueue"
	"github.com/aindrajaya/imageopz-server/services/payments-go/internal/signature"
)

const (
	MaxBodyBytes   = 1 << 20
	requestTimeout = 8 * time.Second // below the 10s shutdown grace
)

// Store opens one database transaction per delivery.
type Store interface {
	Begin(ctx context.Context) (Tx, error)
	Ping(ctx context.Context) error
}

type Tx interface {
	// Claim inserts the delivery row; claimed=false means the key already exists (duplicate).
	Claim(ctx context.Context, k dedupe.Key, taskName string) (claimed bool, err error)
	Commit(ctx context.Context) error
	Rollback(ctx context.Context) error
}

type Handler struct {
	Secret   string
	Store    Store
	Enqueuer enqueue.Enqueuer
	Log      *slog.Logger
}

func (h *Handler) Routes() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("POST /webhooks/lemonsqueezy", h.webhook)
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, _ *http.Request) { write(w, 200, "status", "ok") })
	mux.HandleFunc("GET /readyz", h.readyz)
	return mux
}

func (h *Handler) readyz(w http.ResponseWriter, r *http.Request) {
	ctx, cancel := context.WithTimeout(r.Context(), 3*time.Second)
	defer cancel()
	if err := h.Store.Ping(ctx); err != nil {
		h.Log.Error("readiness check failed", "error", err)
		write(w, 503, "status", "unavailable")
		return
	}
	write(w, 200, "status", "ok")
}

func (h *Handler) webhook(w http.ResponseWriter, r *http.Request) {
	body, err := io.ReadAll(http.MaxBytesReader(w, r.Body, MaxBodyBytes))
	if err != nil {
		var tooBig *http.MaxBytesError
		if errors.As(err, &tooBig) {
			write(w, 413, "error", "payload too large")
			return
		}
		write(w, 400, "error", "unreadable body")
		return
	}
	if err := signature.Verify(h.Secret, body, r.Header.Get("X-Signature")); err != nil {
		h.Log.Warn("webhook refused", "reason", err.Error())
		write(w, 401, "error", "unauthorized")
		return
	}
	key, reason, ok := dedupe.Extract(body)
	if !ok {
		h.Log.Warn("webhook ignored", "reason", reason)
		write(w, 200, "status", "ignored")
		return
	}

	ctx, cancel := context.WithTimeout(r.Context(), requestTimeout)
	defer cancel()
	status, err := h.deliver(ctx, key, body)
	if err != nil {
		h.Log.Error("webhook delivery failed", "event_name", key.EventName, "error", err)
		write(w, 503, "error", "temporarily unavailable")
		return
	}
	write(w, 200, "status", status)
}

// deliver runs claim -> enqueue -> commit in ONE transaction. A failed enqueue rolls the claim back, so the
// sender's retry is treated as new. A failed commit after a successful enqueue leaves a task without a row; the
// retry enqueues it again (Cloud Tasks ALREADY_EXISTS, or a duplicate the API's idempotency absorbs).
func (h *Handler) deliver(ctx context.Context, key dedupe.Key, body []byte) (string, error) {
	tx, err := h.Store.Begin(ctx)
	if err != nil {
		return "", err
	}
	committed := false
	defer func() {
		if !committed {
			// Fresh context: the request context may be the reason we are here.
			rctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer cancel()
			if rbErr := tx.Rollback(rctx); rbErr != nil && !errors.Is(rbErr, pgx.ErrTxClosed) {
				h.Log.Error("rollback failed", "error", rbErr)
			}
		}
	}()
	claimed, err := tx.Claim(ctx, key, h.Enqueuer.TaskName(key))
	if err != nil {
		return "", err
	}
	if !claimed {
		return "duplicate", nil
	}
	if err := h.Enqueuer.Enqueue(ctx, key, body); err != nil {
		return "", err
	}
	if err := tx.Commit(ctx); err != nil {
		return "", err
	}
	committed = true
	return "accepted", nil
}

func write(w http.ResponseWriter, code int, k, v string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	_ = json.NewEncoder(w).Encode(map[string]string{k: v})
}
