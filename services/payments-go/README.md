# payments-go - LemonSqueezy webhook ingress

Verifies the `X-Signature` HMAC, dedupes in Postgres (`payments.webhook_deliveries`), and enqueues the original
payload for the Python API (`POST /internal/payments/events`, contract in `docs/engineering/tasks/T-004-payments-activation.md`).
Python stays the only writer of users, users_quota and orders (ADR-001). Schema is owned by Alembic revision 0003;
this service never creates or alters schema and refuses to start if the table is missing.

```mermaid
sequenceDiagram
    participant LS as LemonSqueezy
    participant G as payments-go
    participant DB as Postgres
    participant Q as Cloud Tasks
    participant API as FastAPI /internal/payments/events
    LS->>G: POST /webhooks/lemonsqueezy (raw body + X-Signature)
    G->>G: HMAC-SHA256 over raw bytes (401 on failure)
    G->>G: dedupe key (lemonsqueezy, event, type, id) or 200 ignored
    G->>DB: BEGIN; INSERT ... ON CONFLICT DO NOTHING RETURNING
    alt no row returned
        G-->>LS: 200 duplicate
    else row inserted
        G->>Q: CreateTask (name = queue/tasks/sha256(key), OIDC)
        alt enqueue fails
            G->>DB: ROLLBACK
            G-->>LS: 503 (sender retries)
        else ok or ALREADY_EXISTS
            G->>DB: COMMIT
            G-->>LS: 200 accepted
        end
    end
    Q->>API: POST original body (OIDC token); API is idempotent per order
```

A commit failure after a successful enqueue answers 503; the sender's retry enqueues again (Cloud Tasks
`ALREADY_EXISTS`, or a duplicate the API's idempotency absorbs).

## Configuration (environment only)
| Variable | Required | Notes |
|---|---|---|
| `PORT` | no | default 8080 |
| `DATABASE_URL` | yes | libpq URL; sslmode from the URL. Supabase pooler (6543) works: simple protocol, no prepared statements |
| `LEMONSQUEEZY_SIGNING_SECRET` | yes | missing -> refuses to start (and every request is refused) |
| `ENQUEUE_MODE` | yes | `cloudtasks` or `direct`; unset or unknown -> refuses to start |
| `TASKS_TARGET_URL` | yes | API base URL; `/internal/payments/events` is appended |
| `TASKS_QUEUE` | cloudtasks | `projects/P/locations/L/queues/Q` |
| `TASKS_SA_EMAIL`, `TASKS_AUDIENCE` | cloudtasks | OIDC service account and audience (must match the API's `INTERNAL_AUTH_*`) |
| `INTERNAL_SHARED_SECRET` | direct | sent as `X-Internal-Token`; local/dev only |

`DATABASE_URL` must connect as the table-owner role (the one Alembic uses): the table has RLS enabled with no policies, so a non-owner role sees nothing and every insert would fail.

Endpoints: `POST /webhooks/lemonsqueezy` (1 MiB limit -> 413), `GET /healthz` (no DB), `GET /readyz` (DB ping). SIGTERM drains gracefully.
Secrets, payloads and customer emails are never logged.

## Run and test
```sh
alembic upgrade head   # from the repo root, against the same DATABASE_URL
ENQUEUE_MODE=direct TASKS_TARGET_URL=http://localhost:8000 INTERNAL_SHARED_SECRET=... \
  DATABASE_URL=postgres://... LEMONSQUEEZY_SIGNING_SECRET=... go run ./cmd/ingress

go vet ./... && golangci-lint run ./... && go test -race ./...
TEST_DATABASE_URL=postgres://...migrated-db... go test -race -tags integration ./internal/httpapi/
docker build -t payments-go .   # CI builds it; not buildable on machines without Docker
```
