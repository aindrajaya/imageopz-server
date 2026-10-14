//go:build integration

package httpapi

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"os"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
)

// Needs TEST_DATABASE_URL (libpq URL) pointing at a database migrated with `alembic upgrade head`.
func integrationSetup(t *testing.T) (*Handler, *fakeEnq, func(id string) int) {
	t.Helper()
	url := os.Getenv("TEST_DATABASE_URL")
	if url == "" {
		t.Skip("TEST_DATABASE_URL not set")
	}
	ctx := context.Background()
	store, err := OpenPG(ctx, url)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(store.Close)
	conn, err := pgx.Connect(ctx, url)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = conn.Close(ctx) })
	count := func(id string) int {
		var n int
		if err := conn.QueryRow(ctx, "SELECT count(*) FROM payments.webhook_deliveries WHERE object_id = $1", id).Scan(&n); err != nil {
			t.Fatal(err)
		}
		return n
	}
	enq := &fakeEnq{}
	return &Handler{Secret: secret, Store: store, Enqueuer: enq, Log: slog.New(slog.NewTextHandler(os.Stderr, nil))}, enq, count
}

func uniqueBody(event string) (string, string) {
	id := fmt.Sprintf("it-%d", time.Now().UnixNano())
	return id, fmt.Sprintf(`{"meta":{"event_name":%q},"data":{"type":"orders","id":%q}}`, event, id)
}

func TestIntegrationThreeDeliveriesOneRowOneEnqueue(t *testing.T) {
	h, enq, count := integrationSetup(t)
	id, body := uniqueBody("order_created")
	codes := []string{}
	for i := 0; i < 3; i++ {
		codes = append(codes, signed(h, body).Body.String())
	}
	if count(id) != 1 || enq.calls != 1 {
		t.Fatalf("rows=%d enqueues=%d (responses %v)", count(id), enq.calls, codes)
	}
	// The row records the deterministic task name.
	if codes[0] != "{\"status\":\"accepted\"}\n" || codes[1] != "{\"status\":\"duplicate\"}\n" || codes[2] != codes[1] {
		t.Fatalf("responses: %v", codes)
	}
}

func TestIntegrationEnqueueFailureLeavesNoRow(t *testing.T) {
	h, enq, count := integrationSetup(t)
	id, body := uniqueBody("order_created")
	enq.err = errors.New("tasks down")
	if w := signed(h, body); w.Code != 503 || count(id) != 0 {
		t.Fatalf("code=%d rows=%d", w.Code, count(id))
	}
	enq.err = nil
	if w := signed(h, body); w.Code != 200 || count(id) != 1 {
		t.Fatalf("retry code=%d rows=%d", w.Code, count(id))
	}
}

func TestIntegrationConcurrentDeliveriesOneEnqueue(t *testing.T) {
	h, enq, count := integrationSetup(t)
	id, body := uniqueBody("order_created")
	done := make(chan int, 8)
	for i := 0; i < 8; i++ {
		go func() { done <- signed(h, body).Code }()
	}
	for i := 0; i < 8; i++ {
		if c := <-done; c != 200 {
			t.Fatalf("code %d", c)
		}
	}
	if count(id) != 1 || enq.calls != 1 {
		t.Fatalf("rows=%d enqueues=%d", count(id), enq.calls)
	}
}
