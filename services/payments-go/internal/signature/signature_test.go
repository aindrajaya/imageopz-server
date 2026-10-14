package signature

import (
	"errors"
	"testing"
)

func TestVerify(t *testing.T) {
	body := []byte(`{"a": 1,  "b":"x"}`)
	good := Sign("s3cret", body)
	tests := []struct {
		name   string
		secret string
		body   []byte
		header string
		want   error
	}{
		{"valid", "s3cret", body, good, nil},
		{"invalid", "s3cret", body, "deadbeef", ErrInvalid},
		{"missing header", "s3cret", body, "", ErrInvalid},
		{"not hex", "s3cret", body, "zz", ErrInvalid},
		{"missing secret", "", body, good, ErrNoSecret},
		{"missing secret and header", "", body, "", ErrNoSecret},
		{"wrong secret", "other", body, good, ErrInvalid},
		{"re-serialised body differs", "s3cret", []byte(`{"a":1,"b":"x"}`), good, ErrInvalid},
		{"trailing newline differs", "s3cret", append(append([]byte{}, body...), '\n'), good, ErrInvalid},
		{"uppercase hex accepted", "s3cret", body, upper(good), nil},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			if err := Verify(tc.secret, tc.body, tc.header); !errors.Is(err, tc.want) {
				t.Fatalf("got %v, want %v", err, tc.want)
			}
		})
	}
}

func upper(s string) string {
	b := []byte(s)
	for i, c := range b {
		if c >= 'a' && c <= 'f' {
			b[i] = c - 32
		}
	}
	return string(b)
}
