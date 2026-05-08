# Plan: “Restore-only login” (Pro-gated Supabase auth)

## Summary
Ubah produk menjadi **guest-first** untuk Free tier, dan **Supabase login hanya untuk Pro** (“restore-only login”). Setelah user login via Supabase Magic Link, frontend wajib melakukan **claim Pro** ke backend dengan `POST /api/v1/session/restore` (authenticated), lalu validasi status via `GET /api/v1/session/me`. Backend pada request **authenticated** menghitung Pro **account-only** (tanpa fallback session/ip), sehingga autentikasi tidak pernah “kebetulan unlock” Pro.

## Key Changes (Backend Behavior)
### 1) Policy: Account-only saat authenticated
- **Ubah `GET /api/v1/session/me`:**
  - Jika ada Supabase JWT valid:
    - Lookup `User` by `supabase_user_id`
    - Lookup `UsersQuota` hanya by `user_id`
    - **Jangan fallback** ke `session_id/ip_hash` untuk menentukan `is_pro/pro_verified/needs_restore`
    - Return:
      - `is_authenticated=true`, `supabase_user_id`, `email`
      - `is_pro` = *account_is_pro* (berdasarkan quota user)
      - `needs_restore=true` jika user ada tapi belum punya entitlement pro yang verified (atau quota tidak ditemukan)
  - Jika guest (tanpa JWT):
    - Tetap behavior sekarang: quota by `session_id` lalu fallback `ip_hash`.
- (Optional but recommended for clarity) Tambahkan field baru ke response:
  - `account_is_pro`, `session_is_pro` agar UI/debug lebih eksplisit, tapi **tidak wajib** jika policy account-only sudah diterapkan untuk authenticated.

### 2) Claim Pro via `POST /api/v1/session/restore` (authenticated)
- Jadikan `POST /api/v1/session/restore` sebagai **satu-satunya cara “login → jadi Pro”**:
  - Input: Supabase JWT + `email` (yang dipakai bayar).
  - Behavior:
    - Validasi entitlement Pro berdasarkan email (User/Order/UsersQuota existing pro).
    - Link `users.supabase_user_id = JWT.sub` (unik; konflik → `409`).
    - Pastikan ada `UsersQuota` untuk `user_id` dan set `is_pro=true`, `pro_verified=true`, `pro_source="restore"`, expiry, reset/keep credit sesuai policy.
- Ini membuat “Pro ownership” pindah dari session/email menjadi **account-owned**.

### 3) Checkout + Webhook linking (best effort)
- `POST /api/v1/checkout/lemonsqueezy`:
  - Jika authenticated, include `supabase_user_id` dalam `checkout_data.custom`.
  - Jika guest, jangan include (biarkan null/absent).
- `POST /api/v1/webhooks/lemonsqueezy`:
  - Tetap aktifkan Pro seperti sekarang berbasis `email/ip_hash/session_id`.
  - Jika webhook punya `custom_data.supabase_user_id`, link ke `users.supabase_user_id` (unik; konflik → log + lanjut).
  - **Tidak menjadikan login otomatis**; user tetap perlu magic link untuk “claim” lintas device bila sebelumnya beli sebagai guest.

### 4) Endpoint yang harus “account-only” saat authenticated
- `POST /api/v1/optimize`:
  - Jika Supabase JWT valid: gunakan quota by `user_id` saja untuk tier/credits (jangan fallback session/ip).
  - Jika guest: gunakan session/ip seperti sekarang.
- (Opsional keamanan) `GET /api/v1/status/{job_id}`:
  - Jika authenticated: pastikan job milik quota user (`job.user_quota_id` terkait `user_id`).
  - Jika guest: tetap akses by job_id (kompatibilitas) atau batasi by session (pilih salah satu dan dokumentasikan).

## Frontend Flow (Decision-complete)
### A) Tidak ada “Sign in”
- Header hanya:
  - `Upgrade to Pro`
  - Link di paywall: `Already purchased? Restore Pro`

### B) Restore Pro
1. User masukkan email → frontend kirim Magic Link Supabase (restore_intent=true).
2. Di `/auth/callback` setelah dapat Supabase session:
   - Call `POST /api/v1/session/restore` dengan:
     - `Authorization: Bearer <supabase_access_token>`
     - body `{ "email": "<email pembeli>" }`
     - header `X-Session-Id` (untuk continuity cookie/session non-auth)
   - Lalu call `GET /api/v1/session/me` dengan `Authorization` + `X-Session-Id`.
   - Jika `is_pro && pro_verified && !needs_restore`: accept login, tampilkan Pro UI.
   - Else: `supabase.auth.signOut()` + tampilkan “No Pro plan found”.

### C) Post-purchase onboarding (guest checkout)
- Setelah webhook activate pro (guest), UI tampilkan CTA:
  - “Claim Pro (magic link)” → menjalankan flow restore di atas.
- Tanpa claim, user tetap bisa Pro di device itu (session-based), tapi tidak lintas device.

## Test Plan
- Backend unit/integration:
  - `session/me` **authenticated** tidak pernah mengambil pro dari session/ip (account-only).
  - `session/restore` authenticated:
    - sukses link `supabase_user_id` dan membuat/upgrade `UsersQuota` by `user_id`
    - konflik `supabase_user_id` → `409`
  - `optimize` authenticated pakai `user_id` quota saja.
  - Webhook + `custom_data.supabase_user_id` melakukan linking idempotent.
- Frontend e2e (minimal manual):
  - Guest user beli → pro aktif di device (session).
  - Restore magic link → setelah callback, `session/restore` + `session/me` menunjukkan pro account.
  - Login pakai email yang tidak pernah bayar → auto signOut.

## Assumptions
- Supabase magic link dikirim **oleh frontend** (backend tidak mengirim email).
- Satu email pembelian = satu entitlement pro yang dapat di-claim ke satu `supabase_user_id` (konflik ditangani via 409).
- Saat authenticated, “Pro” didefinisikan sebagai **account-owned entitlement**; session/ip pro hanya untuk guest mode.
