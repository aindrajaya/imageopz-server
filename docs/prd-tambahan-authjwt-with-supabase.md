Arsitektur backend untuk **ImageOpz** menggunakan **Python (FastAPI)** dan **Supabase Auth** dirancang agar sinkron dengan *frontend* Vite Anda. Fokus utama arsitektur ini adalah menjadi "mesin pekerja" yang ringan, aman, dan efisien dalam mengelola sumber daya 1GB RAM di Seattle[cite: 2].

Berikut adalah detail arsitektur backend lengkapnya:

---

## 1. Komponen Arsitektur Backend
Pemisahan fungsionalitas dilakukan agar proses API tidak terganggu oleh proses berat dari CLI[cite: 2].

*   **API Gateway (FastAPI):** Menangani *request* masuk, verifikasi JWT Supabase, dan manajemen status pekerjaan di SQLite[cite: 2].
*   **Identity Verifier (PyJWT):** Melakukan verifikasi token JWT yang dikirim *frontend* secara lokal menggunakan *Public Key* dari Supabase. Ini meniadakan latensi panggilan API ke luar pada setiap permintaan[cite: 2].
*   **Task Broker (Redis):** Menyimpan daftar antrean gambar yang harus diproses[cite: 2].
*   **Worker Engine (Python CLI Wrapper):** Proses terpisah yang mengambil satu tugas dari Redis, menjalankan kompresi, lalu mengirim hasilnya ke Cloudflare R2[cite: 2].
*   **Database (SQLite WAL):** Sumber kebenaran untuk data kuota pengguna (`users_quota`) dan riwayat pekerjaan (`jobs`)[cite: 2].

---

## 2. Alur Kerja Sinkron (Frontend-Backend Handshake)

Untuk memastikan sistem sinkron, alur otorisasi dilakukan sebagai berikut[cite: 3]:

1.  **Handshake Sesi:**
    *   *Frontend* mengirim JWT di header `Authorization: Bearer <token>`.
    *   Backend mengekstrak `user_id` (sub) dari JWT.
    *   Backend memeriksa tabel `users_quota`. Jika `user_id` baru (hasil dari Magic Link pertama kali), backend membuat baris baru dengan status `Free`[cite: 2].
2.  **Submit Optimasi (`POST /api/v1/optimize`):**
    *   Backend memvalidasi kuota berdasarkan `user_id` yang terverifikasi.
    *   Backend menghasilkan **UUID v4** untuk pekerjaan tersebut dan memasukkannya ke antrean Redis[cite: 2].
3.  **Proses Sekuensial (Worker):**
    *   Karena RAM hanya 1GB, *worker* disetel dengan `concurrency: 1`[cite: 2].
    *   *Worker* memproses gambar -> Unggah ke **Cloudflare R2** -> Perbarui status di SQLite menjadi `done`[cite: 2].
4.  **Polling & Result:**
    *   *Frontend* melakukan polling. Backend menjawab dengan URL dari Cloudflare R2 setelah status berubah menjadi `done`[cite: 2, 3].

---

## 3. Spesifikasi Teknis & Best Practice (GTM Edition)

### A. Keamanan Tanpa Beban
*   **Passive Verification:** Jangan gunakan *secret key* di backend. Gunakan *Public Key* (JWKS) Supabase untuk memverifikasi JWT. Ini sangat aman dan sangat cepat[cite: 2].
*   **Middleware Otorisasi:** Gunakan `Depends` di FastAPI untuk membungkus semua rute sensitif. Hanya pengguna dengan JWT valid yang bisa menyentuh CPU Anda[cite: 2].

### B. Manajemen Memori 1GB
*   **Sub-process Isolation:** Jangan jalankan kompresi gambar di dalam proses utama FastAPI. Gunakan `subprocess.run()` atau *library* antrean seperti **RQ (Redis Queue)** agar jika terjadi kegagalan memori (*crash*), API utama Anda tetap hidup[cite: 2].
*   **Garbage Collection:** Panggil `gc.collect()` secara eksplisit setelah pengunggahan ke R2 selesai untuk memastikan memori 1GB tidak cepat habis oleh sisa-sisa objek gambar[cite: 2].

### C. Integrasi LemonSqueezy (The Bus Test)
*   **Webhook Listener:** Backend menyediakan satu rute publik `/api/v1/webhooks/lemonsqueezy`.
*   **Status Update:** Saat LemonSqueezy mengirim sinyal "Payment Success", backend mencocokkan email pembayar dengan email di database pengguna dan mengubah kolom `is_pro` menjadi `true`[cite: 2].

---

## 4. Struktur Folder Backend (Modular)

```text
backend/
├── app/
│   ├── api/
│   │   ├── endpoints/       # optimize.py, session.py, webhooks.py
│   │   └── middleware/      # auth.py (JWT Verification)
│   ├── core/
│   │   ├── config.py        # Kredensial R2, Redis, Supabase
│   │   └── security.py      # Logika decoding JWT
│   ├── db/
│   │   ├── models.py        # Skema SQLite
│   │   └── database.py      # Koneksi SQLite WAL
│   ├── worker/
│   │   └── processor.py     # Wrapper Python CLI & R2 Upload
│   └── main.py              # Inisialisasi FastAPI
├── scripts/
│   └── cleanup.py           # Cron job hapus file lama (7/40 hari)
├── requirements.txt
└── .env
```

### Mengapa pendekatan ini sinkron dengan Frontend?
Sistem ini menggunakan `user_id` dari Supabase sebagai jembatan tunggal[cite: 3]. *Frontend* tidak perlu tahu tentang SQLite, dan *Backend* tidak perlu tahu tentang *password* pengguna[cite: 2]. Jika pengguna menghapus *cache browser*, mereka cukup minta *Magic Link* baru, dan setelah masuk, mereka otomatis mendapatkan kembali akses ke riwayat pekerjaan mereka karena `user_id` dari Supabase bersifat permanen[cite: 1, 3].

Apakah Anda ingin saya membuatkan skema tabel SQLite yang tepat untuk menyimpan relasi antara `user_id` Supabase dan masa retensi file (7 vs 40 hari)?[cite: 2]