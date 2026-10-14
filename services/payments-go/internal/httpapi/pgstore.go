package httpapi

import (
	"context"
	"fmt"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/aindrajaya/imageopz-server/services/payments-go/internal/dedupe"
)

// PGStore is the pgx-backed Store. It never creates or alters schema (Alembic owns it, ADR-003).
type PGStore struct{ pool *pgxpool.Pool }

// OpenPG connects with the simple protocol: the Supabase transaction pooler does not support prepared
// statements (Supabase docs, "Transaction mode limitations"). sslmode comes from the URL.
func OpenPG(ctx context.Context, url string) (*PGStore, error) {
	cfg, err := pgxpool.ParseConfig(url)
	if err != nil {
		return nil, fmt.Errorf("invalid DATABASE_URL")
	}
	cfg.ConnConfig.DefaultQueryExecMode = pgx.QueryExecModeSimpleProtocol
	cfg.MaxConns = 8
	pool, err := pgxpool.NewWithConfig(ctx, cfg)
	if err != nil {
		return nil, err
	}
	s := &PGStore{pool: pool}
	if _, err := pool.Exec(ctx, "SELECT 1 FROM payments.webhook_deliveries LIMIT 0"); err != nil {
		pool.Close()
		return nil, fmt.Errorf("payments.webhook_deliveries is not available (run alembic upgrade head): %w", err)
	}
	return s, nil
}

func (s *PGStore) Close()                         { s.pool.Close() }
func (s *PGStore) Ping(ctx context.Context) error { return s.pool.Ping(ctx) }

func (s *PGStore) Begin(ctx context.Context) (Tx, error) {
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return nil, err
	}
	return pgTx{tx}, nil
}

type pgTx struct{ tx pgx.Tx }

func (t pgTx) Claim(ctx context.Context, k dedupe.Key, taskName string) (bool, error) {
	var id int64
	err := t.tx.QueryRow(ctx,
		`INSERT INTO payments.webhook_deliveries (provider, event_name, object_type, object_id, task_name)
		 VALUES ($1, $2, $3, $4, NULLIF($5, ''))
		 ON CONFLICT (provider, event_name, object_type, object_id) DO NOTHING
		 RETURNING id`,
		k.Provider, k.EventName, k.ObjectType, k.ObjectID, taskName).Scan(&id)
	if err == pgx.ErrNoRows {
		return false, nil
	}
	return err == nil, err
}

func (t pgTx) Commit(ctx context.Context) error   { return t.tx.Commit(ctx) }
func (t pgTx) Rollback(ctx context.Context) error { return t.tx.Rollback(ctx) }
