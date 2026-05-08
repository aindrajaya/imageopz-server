Berikut adalah **PRD Server (Backend) v3** untuk ImageOpz yang menggabungkan:

- PRD backend v1 (MVP: optimize, queue/worker, quota Free)
- PRD backend v2 (LemonSqueezy checkout + webhook, Pro credits cap, session endpoints)
- Tambahan “Auth JWT with Supabase” (verifikasi JWT offline via JWKS, dan fallback via Supabase Auth server bila diperlukan)
- Pendekatan **Supabase Magic Link** (email dikirim dari frontend)
- Plan **“Pro-gated Supabase only / restore-only login”** (login Supabase tidak pernah “kebetulan unlock Pro”)
- Perubahan terbaru: **checkout intents + reconcile**, pembatasan ketat identity guest via `session_id`, dan **ip_hash tidak dipakai untuk grant Pro** lintas browser/incognito.

Dokumen ini adalah **source of truth** untuk tim backend+frontend mengenai alur identitas, kuota, pembayaran, serta aturan ketat membedakan Guest vs Pro.

---

# PRD Server (Backend): ImageOpz MVP v3

## 0) Tujuan & Non-Goals

### Tujuan
- Guest-first untuk Free tier (tanpa login).
- Pro activation via LemonSqueezy webhook berjalan otomatis untuk device yang memulai checkout.
- Supabase login hanya dipakai untuk **claim/restore Pro lintas device** (Pro-gated auth).
- Kuota Free ketat (3 credits) dan Pro dibatasi credits (default 100) agar bisa top-up/repurchase.
- Identitas guest yang ketat: **`session_id` adalah identity utama**, `ip_hash` hanya untuk UX “activation pending”.

### Non-goals (v3)
- Tidak membangun sistem subscription renewal penuh / seat management.
- Tidak membangun login/password di backend (Supabase handled by frontend).
- Tidak memaksa semua user untuk login (guest tetap supported).

---

## 1) Arsitektur & Infrastruktur

Backend tetap ringan untuk VPS kecil:

- API: FastAPI + Uvicorn
- Queue: Redis (BLPOP/list)
- Worker: 1 proses/loop (concurrency = 1) untuk RAM kecil
- Storage: Cloudflare R2 (S3-compatible)
- DB: SQLite WAL (`journal_mode=WAL`) untuk polling/status yang sering

---

## 2) Identitas: Guest vs Account (Supabase)

### 2.1 Guest identity (default)
Guest identity **ditentukan ketat oleh `session_id`**:
- Header `X-Session-Id` (wajib dikirim frontend secara konsisten), atau
- Cookie `imageopz_sid` (diset backend sebagai fallback)

Catatan:
- Backend **selalu mengembalikan** `session_id` pada `GET /api/v1/session/me` agar frontend bisa menyamakan.
- `ip_hash` masih dihitung server-side untuk:
  - mitigasi abuse free-tier (opsional),
  - UX activation pending (checkout baru dibuat; webhook belum masuk),
  - best-effort matching webhook bila provider tidak membawa `session_id`.
- **ip_hash tidak boleh menjadi identity durable untuk Pro** (agar incognito/browser lain di jaringan yang sama tidak ikut Pro).

### 2.2 Supabase Account identity (optional, Pro-gated)
Jika request membawa `Authorization: Bearer <supabase_access_token>`:
- Backend memverifikasi token dan mengekstrak:
  - `sub` → `supabase_user_id`
  - `email` (bila ada)

Kebijakan inti (restore-only login / Pro-gated):
- Saat request **authenticated**, status Pro **account-only**:
  - lookup `users.supabase_user_id == sub`
  - lookup quota by `user_id`
  - **tanpa fallback** ke session_id/ip_hash untuk menentukan Pro.

Tujuan: login tidak pernah “kebetulan unlock Pro” karena session/cookie yang tertempel.

---

## 3) Model Kuota (Free vs Pro) & Ketatnya “Guest vs Pro”

### 3.1 Free tier (Guest)
Konfigurasi default:
- `FREE_TIER_MAX_CREDITS=3`
- `FREE_TIER_MAX_BULK_IMAGES=3`
- `FREE_TIER_MAX_FILE_SIZE=10MB`
- `FREE_TIER_EXPIRY_DAYS=7`

Aturan ketat:
- Guest dianggap Free **kecuali** `users_quota` untuk `session_id` tersebut memiliki Pro aktif.
- Free credits disimpan di `users_quota.credits_used`.

### 3.2 Pro tier (Paid)
Konfigurasi default:
- `PRO_TIER_MAX_CREDITS=100` (bukan unlimited)
- `PRO_TIER_MAX_BULK_IMAGES=30`
- `PRO_TIER_MAX_FILE_SIZE=50MB`
- `PRO_TIER_EXPIRY_DAYS=40`

Aturan ketat:
- Pro aktif jika `is_pro=true` dan `pro_expiry_date > now`.
- Pro credits disimpan di `users_quota.pro_credits_used`.
- Webhook purchase mereset `pro_credits_used=0`.

### 3.3 Pembeda Guest vs Pro yang ketat
- Guest vs Pro **bukan** ditentukan oleh ada/tidaknya Supabase login.
- Guest bisa Pro (session-based) setelah webhook, tetapi Pro tersebut **tidak lintas device** sampai user claim (restore) ke account.
- Account Pro hanya aktif jika `users_quota` untuk `user_id` account memiliki Pro active.

Field yang harus dipakai frontend dari `GET /api/v1/session/me`:
- `is_authenticated` (Supabase JWT valid)
- `is_pro`
- `pro_source` (`account | session | webhook_session | restore | unknown | none`)
- `needs_restore` (untuk flow claim/restore)
- `activation_pending` (untuk tunggu webhook)

---

## 4) Database Model (SQLite)

### 4.1 `users`
- `id` (PK)
- `email` (unique)
- `supabase_user_id` (nullable) + **unique index** agar lookup cepat dan tidak duplikatif

### 4.2 `users_quota`
- `id` (PK)
- `user_id` (nullable)
- `session_id` (nullable)
- `ip_hash` (nullable)
- `credits_used`
- `is_pro`, `pro_expiry_date`
- `pro_credits_used`
- `pro_verified` (boolean)
- `pro_source` (string)

### 4.3 `orders`
Audit & idempotency untuk payment provider:
- `provider`, `external_id` (unique)
- `status`, `email`, `raw_payload_json`, `user_id`

### 4.4 `checkout_intents`
Short-lived “join key” checkout → webhook:
- `provider`, `checkout_id` (unique)
- `email`, `session_id`, `ip_hash`
- `status` (`created|paid|expired`), `expires_at`, `paid_at`, `order_external_id`

Tujuan:
- UX activation pending (tampilkan “Activating Pro…”)
- Rekonsiliasi setelah redirect checkout (`/session/reconcile`)
- Mengurangi ketergantungan ip_hash untuk grant Pro

---

## 5) Payment LemonSqueezy

### 5.1 Create checkout (server-side)
`POST /api/v1/checkout/lemonsqueezy`
- Backend memanggil LemonSqueezy API (API key tidak pernah ke browser).
- Backend menyisipkan `checkout_data.custom`:
  - `email`
  - `session_id` (durable guest identity)
  - `ip_hash` (server-calculated)
  - `supabase_user_id` **hanya jika authenticated** (harus string; jika null jangan dikirim)
- Backend persist `checkout_intent` status `created` (TTL default 15 menit).

Output:
- `checkout_url` untuk redirect.
- `session_id` yang digunakan backend (frontend harus menyamakan).

### 5.2 Webhook
`POST /api/v1/webhooks/lemonsqueezy`
- Validasi signature HMAC.
- Persist ke `orders` (idempotent).
- Untuk paid event (`order_created`):
  - Temukan/buat `users(email)`
  - Aktifkan/extend Pro di `users_quota` dengan prioritas:
    1) `user_id` (email user record)
    2) `session_id` (custom_data.session_id)
    3) `ip_hash` last-resort **hanya** untuk window intent (tidak grant Pro lintas browser)
  - Set:
    - `is_pro=true`, `pro_expiry_date=now+PRO_TIER_EXPIRY_DAYS`
    - `pro_verified=true`
    - `pro_source="webhook_session"` jika session_id ada
    - `pro_credits_used=0`
  - Update `checkout_intent` status `paid` untuk session_id tersebut.

---

## 6) Supabase Auth (JWT) + Magic Link

### 6.1 Magic link: frontend-only
- Frontend mengirim magic link via Supabase SDK.
- Backend tidak mengirim email.

### 6.2 Verifikasi JWT di backend
Backend mendukung dua strategi:

1) Offline verify via JWKS (RS256):
   - env: `SUPABASE_JWKS_URL`, `SUPABASE_JWT_ISSUER`, `SUPABASE_JWT_AUDIENCE`
   - JWKS cached dengan TTL (`SUPABASE_JWKS_CACHE_TTL_SECONDS`)

2) Fallback verify via Supabase Auth server (legacy HS256):
   - env: `SUPABASE_AUTH_URL` harus berisi base `.../auth/v1`
   - env: `SUPABASE_ANON_KEY`
   - Backend memanggil `GET {SUPABASE_AUTH_URL}/user` untuk validasi token.

---

## 7) Session API & Flow “Pay → Auto Pro Session → Claim Pro (Account)”

### 7.1 `GET /api/v1/session/me`
- Guest:
  - lookup quota by `session_id` saja (no ip_hash fallback untuk Pro).
  - isi `activation_pending=true` jika ada intent `created` yang belum paid (TTL).
- Authenticated:
  - account-only: lookup by `supabase_user_id` → `user_id` → quota.
  - `needs_restore=true` jika account belum memiliki entitlement Pro.

### 7.2 `POST /api/v1/session/reconcile` (guest-friendly)
Dipanggil frontend setelah redirect dari checkout:
- “pull latest” state dari server untuk session ini.
- Jika webhook sudah mengaktifkan Pro untuk session_id yang sama, `session/me` akan mengembalikan Pro.
- Jika belum, `activation_pending=true` dan frontend retry.

### 7.3 `POST /api/v1/session/restore` (claim/restore Pro)
Tujuan:
- Mengubah “Pro ownership” dari session/email menjadi **account-owned**.

Perilaku:
- Jika authenticated:
  - Validasi Pro entitlement berdasarkan `email` pembelian.
  - Link `users.supabase_user_id = JWT.sub` (unik; konflik → 409).
  - Pastikan `users_quota` by `user_id` adalah Pro active; set `pro_verified=true`, `pro_source="restore"`.
- Jika guest:
  - Bind session_id ke entitlement by email untuk recovery device yang sama (opsional UX).

### 7.4 “User membayar → otomatis login” (definisi v3)
Backend **tidak otomatis login Supabase** setelah bayar (karena magic link dilakukan frontend).
Yang otomatis terjadi adalah:
- setelah webhook paid, session yang memulai checkout menjadi **Pro session** (tanpa login) → user bisa langsung optimize tanpa limit 3.

Setelah itu, user bisa “Claim Pro” (magic link) untuk:
- Pro lintas device
- Pro-gated access (account-only)

---

## 8) Optimize & Status

### 8.1 `POST /api/v1/optimize`
- Authenticated: quota account-only (by `user_id`).
- Guest:
  - quota by `session_id` saja.
  - jika checkout intent masih `created` dan user mengirim >3 image, backend dapat membalas retryable 429 `ACTIVATION_PENDING`.

### 8.2 `GET /api/v1/status/{job_id}`
- Authenticated:
  - job hanya bisa diakses jika milik account.
- Guest:
  - akses by job_id (kompatibilitas).

---

## 9) Error Semantics (untuk frontend)

### Activation pending (retryable)
HTTP 429 dengan:
- `error="ACTIVATION_PENDING"`
- `details.retry_after_ms`

Frontend:
- tampilkan “Activating Pro…” dan retry sampai timeout.

---

## 10) Requirement Frontend (wajib)

- Frontend wajib punya `session_id` stabil (UUID) dan selalu kirim `X-Session-Id` untuk:
  - `/session/me`, `/session/reconcile`, `/optimize`, `/checkout/lemonsqueezy`, `/session/restore`
- Jangan generate `session_id` baru setelah redirect checkout.
- Untuk claim Pro:
  - Kirim `Authorization: Bearer <supabase_access_token>` (bukan anon key, bukan refresh token)

---

## 11) Security & Privacy Notes
- Jangan pernah gunakan `ip_hash` untuk mengaktifkan Pro lintas browser/incognito.
- `pro_source` dipakai UI:
  - `session/webhook_session`: “Claim recommended”
  - `account/restore`: normal Pro UI
