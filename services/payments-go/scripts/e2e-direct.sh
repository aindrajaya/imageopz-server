#!/usr/bin/env bash
# Local end-to-end: signed order_created -> Go ingress (direct mode) -> FastAPI internal endpoint -> Pro activated once.
# Needs: a throwaway Postgres (docs/engineering/runbooks/local-postgres.md), the Python venv active, `go`, `psql`.
# Usage: PGPORT=5433 services/payments-go/scripts/e2e-direct.sh   (run from the repo root; creates and drops DB payments_e2e)
set -euo pipefail
PGPORT="${PGPORT:-5433}"; DB=payments_e2e
PSQL="psql -h localhost -p $PGPORT -U postgres -tA"
WH_SECRET="e2e-$(openssl rand -hex 8)"; INTERNAL="e2e-$(openssl rand -hex 8)"
SID="e2e-session-$(openssl rand -hex 4)"; ORDER="e2e-$(date +%s)"
TMP="$(mktemp -d)"; PIDS=()
cleanup() { for p in "${PIDS[@]:-}"; do kill "$p" 2>/dev/null || true; done; $PSQL -d postgres -c "DROP DATABASE IF EXISTS $DB WITH (FORCE)" >/dev/null 2>&1 || true; rm -rf "$TMP"; }
trap cleanup EXIT
$PSQL -d postgres -c "DROP DATABASE IF EXISTS $DB WITH (FORCE)" -c "CREATE DATABASE $DB" >/dev/null

export DATABASE_URL="postgresql+psycopg://postgres:dev@localhost:$PGPORT/$DB"
alembic upgrade head >/dev/null 2>&1

LEMONSQUEEZY_SECRET="$WH_SECRET" INTERNAL_AUTH_MODE=shared_secret INTERNAL_AUTH_SHARED_SECRET="$INTERNAL" \
  ENVIRONMENT=development SECRET_KEY="dev-$INTERNAL" python -m uvicorn app.main:app --port 8765 >"$TMP/api.log" 2>&1 &
PIDS+=($!)
(cd services/payments-go && go build -o "$TMP/ingress" ./cmd/ingress)
PORT=8766 DATABASE_URL="postgres://postgres:dev@localhost:$PGPORT/$DB?sslmode=disable" LEMONSQUEEZY_SIGNING_SECRET="$WH_SECRET" \
  ENQUEUE_MODE=direct TASKS_TARGET_URL=http://localhost:8765 INTERNAL_SHARED_SECRET="$INTERNAL" "$TMP/ingress" >"$TMP/ingress.log" 2>&1 &
PIDS+=($!)
for _ in $(seq 60); do curl -s -o /dev/null localhost:8765/ && curl -sf localhost:8766/ready >/dev/null && break; sleep 0.5; done

BODY=$(printf '{"meta":{"event_name":"order_created","custom_data":{"session_id":"%s"}},"data":{"type":"orders","id":"%s","attributes":{"user_email":"e2e@example.com","total":1900}}}' "$SID" "$ORDER")
SIG=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "$WH_SECRET" -hex | sed 's/^.* //')
deliver() { curl -s -X POST localhost:8766/webhooks/lemonsqueezy -H "X-Signature: $SIG" -H 'Content-Type: application/json' --data-binary "$BODY"; echo; }
state() { $PSQL -d $DB -c "SELECT (SELECT count(*) FROM payments.webhook_deliveries), (SELECT count(*) FROM processed_payment_events), (SELECT count(*) FROM orders), (SELECT coalesce(string_agg(is_pro::text||'/'||coalesce(pro_credits_used,0)::text||'/'||coalesce(pro_expiry_date::text,''),','),'') FROM users_quota WHERE session_id='$SID')"; }

echo "first:  $(deliver)"; S1=$(state); echo "state:  $S1 (deliveries|events|orders|is_pro/credits/expiry)"
echo "replay: $(deliver)"; S2=$(state); echo "state:  $S2"
echo "ingress replay: $(deliver)"
echo "api direct replay (bypassing the ingress dedupe): $(curl -s -X POST localhost:8765/internal/payments/events -H "X-Internal-Token: $INTERNAL" -H 'Content-Type: application/json' --data-binary "$BODY")"
S2=$(state)
echo "api me: $(curl -s localhost:8765/api/v1/session/me -H "X-Session-Id: $SID")"
[ "$S1" = "$S2" ] && [[ "$S1" == 1\|1\|1\|true/* ]] && echo "E2E PASS: one activation, replay changed nothing" || { echo "E2E FAIL"; tail -20 "$TMP/api.log" "$TMP/ingress.log"; exit 1; }
