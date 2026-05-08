# Plan: Supabase Auth JWT (optional) untuk ImageOpz Backend — revisi (Unique Index `supabase_user_id`)

## Summary
Implement Supabase JWT verification (JWKS + PyJWT) sebagai identitas opsional; guest flow (`session_id/ip_hash`) tetap berjalan. Tambahkan mapping `users.supabase_user_id` (unique + indexed) agar lookup user setara cepat dengan PK dan aman dari duplikasi. Integrasikan purchase linking (LemonSqueezy) agar login Supabase bisa memulihkan Pro lintas device.

## Key Changes
### 1) Database (wajib)
- Tambah kolom `users.supabase_user_id` (nullable).
- Tambah **Unique Index/Constraint** untuk `users.supabase_user_id`:
  - SQLite: `CREATE UNIQUE INDEX IF NOT EXISTS idx_users_supabase_user_id ON users(supabase_user_id) WHERE supabase_user_id IS NOT NULL;`
  - (Jika SQLite versi lama yang tidak support partial index, fallback: unique constraint + pastikan `NULL` allowed dan tidak diisi string kosong.)
- Tambah non-unique index biasa opsional jika perlu (unique index biasanya sudah cukup untuk lookup).

### 2) Settings/env
- `SUPABASE_JWKS_URL`, `SUPABASE_JWT_ISSUER`, `SUPABASE_JWT_AUDIENCE`, `SUPABASE_JWKS_CACHE_TTL_SECONDS`.

### 3) Auth layer (kode)
- Buat verifier Supabase JWT berbasis JWKS dengan cache TTL.
- FastAPI dependency `get_optional_supabase_identity()`:
  - No header → return None (guest).
  - Header ada tapi invalid → 401.

### 4) Integrasi endpoint
- `POST /api/v1/optimize`: jika authenticated, upsert `User` by `supabase_user_id` (O(1) via unique index), lalu quota berbasis `user_id`; jika guest, pakai session/ip seperti sekarang.
- `GET /api/v1/session/me`: expose `is_authenticated`, `supabase_user_id`, dan quota state sesuai identity.
- `POST /api/v1/checkout/lemonsqueezy`: jika authenticated, kirim `supabase_user_id` di `custom_data` agar webhook bisa link akurat.
- `POST /api/v1/webhooks/lemonsqueezy`: jika ada `custom_data.supabase_user_id`, set `users.supabase_user_id` (idempotent; handle conflict jika sudah dipakai user lain).
- `POST /api/v1/session/restore`: bila authenticated, bind Pro ke `user_id` (bukan hanya `session_id`) agar restore lintas device otomatis setelah login.

## Test Plan
- Test migrasi SQLite: kolom + unique index dibuat, serta insert dua user dengan `supabase_user_id` sama harus gagal.
- Test auth verifier: token valid/invalid (issuer/aud/exp).
- Test integrate optimize/session/me/webhook/restore dengan `supabase_user_id` memastikan lookup cepat dan tidak duplikatif.

## Assumptions
- `supabase_user_id` disimpan sebagai string dari JWT `sub`.
- Guest flow tetap enabled (optional auth).
- Pada konflik unique (supabase_user_id sudah terikat ke user lain), backend return 409 + pesan eksplisit agar bisa ditangani di frontend/support.
