Berikut adalah **PRD Server (Backend) v2** yang menggabungkan PRD v1.4 dengan implementasi terbaru: LemonSqueezy checkout + webhook, kuota Pro berbasis credits, sesi berbasis cookie/header, dan **Supabase Auth (JWT) dengan pendekatan “restore-only login” (Pro-gated auth)**.

---

# PRD Server (Backend): ImageOpz MVP v2

## 1) Arsitektur & Infrastruktur Dasar
Backend tetap ringan untuk VPS 1 vCPU / 1GB RAM dengan pemisahan proses:

- **API Gateway:** FastAPI + Uvicorn (HTTP only)
- **Queue:** Redis (list/BLPOP)
- **Worker:** 1 thread loop (concurrency = 1) memproses job secara sekuensial
- **Storage:** Cloudflare R2 (S3-compatible) untuk publik URL hasil
- **Database:** SQLite WAL (`journal_mode=WAL`) untuk menghindari lock pada polling/status

## 2) Prinsip Identitas (Guest vs Supabase Account)
Backend mendukung **dua mode identitas**:

### A) Guest identity (default)
- Berdasarkan **`session_id`** (header `X-Session-Id` atau cookie `imageopz_sid`) dan fallback **`ip_hash`**.
- Guest dipakai untuk Free tier dan juga untuk Pro “sementara” setelah purchase sebelum user melakukan “claim” ke account.

### B) Supabase account identity (JWT)
- Jika request membawa `Authorization: Bearer <supabase_access_token>` dan token valid:
  - Backend mengenali user via claim `sub` (Supabase user id / UUID).
- **Kebijakan v2 (restore-only login / Pro-gated auth):**
  - Saat request **authenticated**, backend menghitung status Pro **account-only** (berdasarkan `user_id`/`supabase_user_id`).
  - Backend **tidak** melakukan fallback ke pro session/ip untuk menyimpulkan user authenticated adalah Pro.

Tujuan kebijakan ini: autentikasi tidak pernah “secara kebetulan” meng-unlock Pro hanya karena cookie/session tertempel.

## 3) Model Kuota (Free vs Pro)
### Free tier (guest)
- Maksimal **3 credits** total (`FREE_TIER_MAX_CREDITS=3`)
- Maksimal **3 gambar per request** (`FREE_TIER_MAX_BULK_IMAGES=3`)
- Maksimal **10MB per file** (`FREE_TIER_MAX_FILE_SIZE=10MB`)
- Expiry default **7 hari** untuk hasil (`FREE_TIER_EXPIRY_DAYS=7`)

### Pro tier (paid)
- Pro aktif jika `is_pro=true` dan `pro_expiry_date` masih valid
- Maksimal **30 gambar per request** (`PRO_TIER_MAX_BULK_IMAGES=30`)
- Maksimal **50MB per file** (`PRO_TIER_MAX_FILE_SIZE=50MB`)
- Expiry default **40 hari** (`PRO_TIER_EXPIRY_DAYS=40`)
- **Credits Pro dibatasi** (default **100**) untuk mendorong repurchase:
  - `PRO_TIER_MAX_CREDITS=100`
  - penggunaan disimpan di `users_quota.pro_credits_used`
  - webhook purchase mereset `pro_credits_used=0`

## 4) Payment (LemonSqueezy) + Webhook
### A) Checkout dibuat server-side
- `POST /api/v1/checkout/lemonsqueezy`
  - Backend membuat hosted checkout via LemonSqueezy API (API key tidak pernah di browser).
  - Backend menyisipkan `checkout_data.custom` untuk mapping webhook:
    - `email`
    - `ip_hash`
    - `session_id`
    - `supabase_user_id` **hanya jika** request authenticated (string, tidak boleh null)

### B) Webhook aktivasi Pro
- `POST /api/v1/webhooks/lemonsqueezy`
  - Verifikasi signature (HMAC SHA256).
  - Persist event ke tabel `orders` (idempotent).
  - Untuk event paid (`order_created` / legacy `order:created`):
    - mengaktifkan Pro (`users_quota.is_pro=true`, `pro_expiry_date=now+PRO_TIER_EXPIRY_DAYS`)
    - set marker: `pro_verified=true`, `pro_source="webhook"`
    - reset `pro_credits_used=0`
    - best-effort linking ke account jika ada `custom_data.supabase_user_id` (dengan unique constraint)

## 5) Supabase Auth: “Restore-only login” (Pro-gated)
Tujuan: user **tidak bisa “login mode”** kecuali memang punya Pro entitlement.

### A) Magic link ditangani frontend
- Frontend mengirim magic link via Supabase SDK.
- Backend tidak mengirim email login.
- Backend hanya memverifikasi JWT (JWKS cached).

### B) Claim Pro setelah login
- Setelah callback Supabase (frontend sudah punya access token):
  - Frontend wajib memanggil:
    - `POST /api/v1/session/restore` (harus authenticated)
    - body `{ "email": "<email pembeli>" }`
  - Lalu validasi status:
    - `GET /api/v1/session/me` (authenticated)
  - UI aturan:
    - jika `is_pro && pro_verified && !needs_restore` → tampilkan Pro UI
    - selain itu → frontend `signOut()` dan tetap guest

## 6) Endpoint API (v2)
Endpoint utama (v1 tetap dipakai, ditambah session/payment endpoints):

- `POST /api/v1/optimize`
  - Guest: kuota by `session_id/ip_hash`
  - Authenticated: kuota **account-only** by `user_id`
- `GET /api/v1/status/{job_id}`
  - Guest: akses by `job_id`
  - Authenticated: hanya bisa akses job milik account (otherwise 404)
- `POST /api/v1/checkout/lemonsqueezy`
- `POST /api/v1/webhooks/lemonsqueezy`
- `GET /api/v1/session/me`
  - Guest: return state dari session/ip
  - Authenticated: return state **account-only**
- `POST /api/v1/session/restore`
  - Guest: relink session → (legacy/compat) bisa tetap ada, tetapi flow v2 menekankan authenticated restore untuk claim Pro ke account
  - Authenticated: bind Pro entitlement ke account (user_id) + link `users.supabase_user_id`
- `POST /api/v1/session/logout`
  - rotate/clear session cookie server-side

## 7) Skema Database (SQLite)
### Tabel `users`
- `id` (UUID internal)
- `email` (unique)
- `supabase_user_id` (nullable, **unique index**)

### Tabel `users_quota`
- `id`
- `user_id` (FK users.id, nullable)
- `session_id` (nullable)
- `ip_hash` (nullable)
- `credits_used` (free)
- `is_pro`, `pro_expiry_date`
- `pro_credits_used` (pro credits counter)
- `pro_verified` (boolean marker)
- `pro_source` (`webhook|restore|token|unknown|none`)

### Tabel `jobs`
- `id` (job_id UUID)
- `user_quota_id`
- `status`, sizes, `r2_url`, timestamps, `expiry_date`

### Tabel `orders`
- `provider`, `external_id`, `event_name`, `status`, `email`, `user_id`, `raw_payload_json`

## 8) Keamanan & Validasi File
Tetap mengikuti v1:
- strip EXIF/metadata
- magic number validation
- decompression bomb protection (pixel cap)

## 9) Konfigurasi Environment (ringkas)
- CORS: `CORS_ALLOW_ORIGINS`, `CORS_ALLOW_CREDENTIALS`
- Session cookie: `SESSION_COOKIE_NAME`, `SESSION_COOKIE_MAX_AGE_DAYS`
- LemonSqueezy: `LEMONSQUEEZY_SECRET`, `LEMONSQUEEZY_API_KEY`, `LEMONSQUEEZY_STORE_ID`, `LEMONSQUEEZY_VARIANT_ID`
- Supabase verify-only: `SUPABASE_JWKS_URL`, `SUPABASE_JWT_ISSUER`, `SUPABASE_JWT_AUDIENCE`, `SUPABASE_JWKS_CACHE_TTL_SECONDS`

---

## Catatan Implementasi (yang sudah ada di codebase v2)
- Supabase JWT diverifikasi offline via JWKS (cached).
- `users.supabase_user_id` memiliki unique index untuk lookup cepat dan aman dari duplikasi.
- Authenticated `session/me` bersifat account-only (restore-only login policy).
- Checkout LemonSqueezy tidak mengirim `custom_data.supabase_user_id` jika tidak authenticated (menghindari 422).
