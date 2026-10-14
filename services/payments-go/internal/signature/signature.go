// Package signature verifies LemonSqueezy webhook signatures.
package signature

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"errors"
)

var (
	// ErrNoSecret means the signing secret is not configured; every request is refused (fail closed).
	ErrNoSecret = errors.New("signing secret not configured")
	// ErrInvalid covers a missing, malformed or mismatching X-Signature.
	ErrInvalid = errors.New("invalid signature")
)

// Verify checks header (hex HMAC-SHA256) against the exact raw request body.
func Verify(secret string, body []byte, header string) error {
	if secret == "" {
		return ErrNoSecret
	}
	got, err := hex.DecodeString(header)
	if err != nil || len(got) == 0 {
		return ErrInvalid
	}
	mac := hmac.New(sha256.New, []byte(secret))
	mac.Write(body)
	if !hmac.Equal(got, mac.Sum(nil)) {
		return ErrInvalid
	}
	return nil
}

// Sign returns the hex signature LemonSqueezy would send; used by tests and the local e2e script.
func Sign(secret string, body []byte) string {
	mac := hmac.New(sha256.New, []byte(secret))
	mac.Write(body)
	return hex.EncodeToString(mac.Sum(nil))
}
