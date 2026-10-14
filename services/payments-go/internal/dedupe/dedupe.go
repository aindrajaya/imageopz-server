// Package dedupe extracts the idempotency key of a LemonSqueezy event (ADR-004).
package dedupe

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"strings"
)

const Provider = "lemonsqueezy"

// Column widths of payments.webhook_deliveries; longer values are treated as unextractable
// (an insert error would otherwise be retried by the sender forever).
const (
	maxEventName  = 64
	maxObjectType = 64
	maxObjectID   = 128
)

// Key is the unique (provider, event_name, object_type, object_id) tuple.
type Key struct {
	Provider   string
	EventName  string
	ObjectType string
	ObjectID   string
}

// Hash is the hex sha256 of the key, used for deterministic task names.
func (k Key) Hash() string {
	sum := sha256.Sum256([]byte(strings.Join([]string{k.Provider, k.EventName, k.ObjectType, k.ObjectID}, "\x00")))
	return hex.EncodeToString(sum[:])
}

type payload struct {
	Meta struct {
		EventName string `json:"event_name"`
	} `json:"meta"`
	Data struct {
		Type string          `json:"type"`
		ID   json.RawMessage `json:"id"`
	} `json:"data"`
}

// Extract returns the key, or ok=false with a reason code when the event has none.
// "order:created" is canonicalised to "order_created"; a missing data.type becomes "unknown".
func Extract(body []byte) (key Key, reason string, ok bool) {
	var p payload
	if err := json.Unmarshal(body, &p); err != nil {
		return Key{}, "invalid_json", false
	}
	event := strings.TrimSpace(p.Meta.EventName)
	if event == "order:created" {
		event = "order_created"
	}
	if event == "" {
		return Key{}, "missing_event_name", false
	}
	id := idString(p.Data.ID)
	if id == "" {
		return Key{}, "missing_object_id", false
	}
	typ := strings.TrimSpace(p.Data.Type)
	if typ == "" {
		typ = "unknown"
	}
	if strings.ContainsRune(event+typ+id, 0) {
		return Key{}, "field_invalid", false // Postgres text cannot store NUL
	}
	if len(event) > maxEventName || len(typ) > maxObjectType || len(id) > maxObjectID {
		return Key{}, "field_too_long", false
	}
	return Key{Provider: Provider, EventName: event, ObjectType: typ, ObjectID: id}, "", true
}

// idString accepts a JSON string or number (LemonSqueezy sends strings).
func idString(raw json.RawMessage) string {
	if len(raw) == 0 {
		return ""
	}
	var s string
	if json.Unmarshal(raw, &s) == nil {
		return strings.TrimSpace(s)
	}
	var n json.Number
	if json.Unmarshal(raw, &n) == nil {
		return n.String()
	}
	return ""
}
