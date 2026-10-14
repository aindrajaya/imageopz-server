package dedupe

import (
	"strings"
	"testing"
)

func TestExtract(t *testing.T) {
	long := strings.Repeat("x", 129)
	tests := []struct {
		name   string
		body   string
		want   Key
		reason string
	}{
		{"order_created", `{"meta":{"event_name":"order_created"},"data":{"type":"orders","id":"42"}}`,
			Key{"lemonsqueezy", "order_created", "orders", "42"}, ""},
		{"colon form normalised", `{"meta":{"event_name":"order:created"},"data":{"type":"orders","id":"42"}}`,
			Key{"lemonsqueezy", "order_created", "orders", "42"}, ""},
		{"numeric id", `{"meta":{"event_name":"order_refunded"},"data":{"type":"orders","id":42}}`,
			Key{"lemonsqueezy", "order_refunded", "orders", "42"}, ""},
		{"missing type falls back", `{"meta":{"event_name":"order_created"},"data":{"id":"7"}}`,
			Key{"lemonsqueezy", "order_created", "unknown", "7"}, ""},
		{"missing event name", `{"meta":{},"data":{"type":"orders","id":"1"}}`, Key{}, "missing_event_name"},
		{"missing meta", `{"data":{"type":"orders","id":"1"}}`, Key{}, "missing_event_name"},
		{"missing id", `{"meta":{"event_name":"order_created"},"data":{"type":"orders"}}`, Key{}, "missing_object_id"},
		{"null id", `{"meta":{"event_name":"order_created"},"data":{"id":null}}`, Key{}, "missing_object_id"},
		{"object id", `{"meta":{"event_name":"order_created"},"data":{"id":{}}}`, Key{}, "missing_object_id"},
		{"invalid json", `{nope`, Key{}, "invalid_json"},
		{"not an object", `[1]`, Key{}, "invalid_json"},
		{"wrong field type", `{"meta":[],"data":1}`, Key{}, "invalid_json"},
		{"NUL in id", `{"meta":{"event_name":"order_created"},"data":{"id":"a\u0000b"}}`, Key{}, "field_invalid"},
		{"NUL in event", `{"meta":{"event_name":"order\u0000"},"data":{"id":"1"}}`, Key{}, "field_invalid"},
		{"id too long", `{"meta":{"event_name":"order_created"},"data":{"id":"` + long + `"}}`, Key{}, "field_too_long"},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			got, reason, ok := Extract([]byte(tc.body))
			if ok != (tc.reason == "") || reason != tc.reason || got != tc.want {
				t.Fatalf("got (%+v, %q, %v), want (%+v, %q)", got, reason, ok, tc.want, tc.reason)
			}
		})
	}
}

func TestHashIsDeterministicAndFieldSensitive(t *testing.T) {
	a := Key{"lemonsqueezy", "order_created", "orders", "42"}
	h := a.Hash()
	if h != a.Hash() || len(h) != 64 {
		t.Fatal("hash must be stable 64 hex chars")
	}
	b := Key{"lemonsqueezy", "order_created", "orders", "43"}
	c := Key{"lemonsqueezy", "order_createdorders", "", "42"}
	if a.Hash() == b.Hash() || a.Hash() == c.Hash() {
		t.Fatal("distinct keys must hash differently")
	}
}
