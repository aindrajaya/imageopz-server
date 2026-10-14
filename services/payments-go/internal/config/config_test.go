package config

import (
	"strings"
	"testing"
)

func env(m map[string]string) func(string) string { return func(k string) string { return m[k] } }

func base(extra map[string]string) map[string]string {
	m := map[string]string{
		"DATABASE_URL": "postgres://u@h/db", "LEMONSQUEEZY_SIGNING_SECRET": "s", "TASKS_TARGET_URL": "https://api.example/",
	}
	for k, v := range extra {
		m[k] = v
	}
	return m
}

func TestLoad(t *testing.T) {
	ct := map[string]string{"ENQUEUE_MODE": "cloudtasks", "TASKS_QUEUE": "projects/p/locations/l/queues/q",
		"TASKS_SA_EMAIL": "sa@p.iam.gserviceaccount.com", "TASKS_AUDIENCE": "https://api.example"}
	tests := []struct {
		name    string
		env     map[string]string
		wantErr string
	}{
		{"cloudtasks ok", base(ct), ""},
		{"cloudtasks needs https", base(map[string]string{"ENQUEUE_MODE": "cloudtasks", "TASKS_TARGET_URL": "http://api.example", "TASKS_QUEUE": "projects/p/locations/l/queues/q", "TASKS_SA_EMAIL": "a", "TASKS_AUDIENCE": "b"}), "must be https"},
		{"direct ok", base(map[string]string{"ENQUEUE_MODE": "direct", "INTERNAL_SHARED_SECRET": "x"}), ""},
		{"mode unset", base(nil), "ENQUEUE_MODE is required"},
		{"mode unknown", base(map[string]string{"ENQUEUE_MODE": "pubsub"}), "unknown ENQUEUE_MODE"},
		{"direct without shared secret", base(map[string]string{"ENQUEUE_MODE": "direct"}), "INTERNAL_SHARED_SECRET"},
		{"cloudtasks without queue", base(map[string]string{"ENQUEUE_MODE": "cloudtasks", "TASKS_SA_EMAIL": "a", "TASKS_AUDIENCE": "b"}), "TASKS_QUEUE"},
		{"bad queue path", base(map[string]string{"ENQUEUE_MODE": "cloudtasks", "TASKS_QUEUE": "q", "TASKS_SA_EMAIL": "a", "TASKS_AUDIENCE": "b"}), "TASKS_QUEUE must"},
		{"no signing secret", func() map[string]string { m := base(ct); delete(m, "LEMONSQUEEZY_SIGNING_SECRET"); return m }(), "LEMONSQUEEZY_SIGNING_SECRET"},
		{"no database url", func() map[string]string { m := base(ct); delete(m, "DATABASE_URL"); return m }(), "DATABASE_URL"},
		{"no target url", func() map[string]string { m := base(ct); delete(m, "TASKS_TARGET_URL"); return m }(), "TASKS_TARGET_URL"},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			c, err := Load(env(tc.env))
			if tc.wantErr == "" {
				if err != nil {
					t.Fatalf("unexpected error: %v", err)
				}
				if c.Port != "8080" || strings.HasSuffix(c.TasksTargetURL, "/") {
					t.Fatalf("defaults/normalisation wrong: %+v", c)
				}
				return
			}
			if err == nil || !strings.Contains(err.Error(), tc.wantErr) {
				t.Fatalf("got %v, want error containing %q", err, tc.wantErr)
			}
		})
	}
}

func TestErrorsNeverContainSecretValues(t *testing.T) {
	_, err := Load(env(map[string]string{"LEMONSQUEEZY_SIGNING_SECRET": "topsecret", "DATABASE_URL": "postgres://u:pw@h/db", "ENQUEUE_MODE": "bogus"}))
	if err == nil || strings.Contains(err.Error(), "topsecret") || strings.Contains(err.Error(), "pw") {
		t.Fatalf("error leaks secrets: %v", err)
	}
}
