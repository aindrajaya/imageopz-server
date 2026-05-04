Berikut adalah pembaruan **PRD Server (Backend)** yang telah diselaraskan dengan kebutuhan antarmuka terbaru (v1.4). Karena kapabilitas antarmuka kini mencakup *bulk upload*, batas ukuran dinamis, dan perlindungan keamanan ekstra, arsitektur backend dioptimalkan untuk menangani logika tersebut tanpa membebani memori 1 GB pada VPS Vultr.

---

# PRD Server (Backend): ImageOpz MVP v1.4

## 1. Arsitektur & Infrastruktur Dasar
Backend beroperasi sebagai pelayan API (*API Gateway*) sekaligus mesin pekerja (*worker engine*) asinkron yang sangat hemat memori.

*   **Lingkungan:** Vultr VPS (Seattle) — 1 vCPU, 1 GB RAM, 25 GB SSD.
*   **Framework API:** **FastAPI** (Python) via Uvicorn. Sangat ideal untuk menangani ribuan *request* serentak secara asinkron.
*   **Message Broker & Queue:** **Redis** dan **BullMQ** (atau *Celery/RQ* pada Python).
*   **Penyimpanan Eksternal:** **Cloudflare R2** (S3-compatible) untuk distribusi hasil akhir secara global.
*   **Database:** **SQLite3** dengan `PRAGMA journal_mode=WAL;` untuk mencegah *database locked error* saat *polling* dan *writing* terjadi bersamaan.

---

## 2. Manajemen Kuota & Autentikasi (Sisi Server)
Meskipun *Local Storage* digunakan di klien, backend bertindak sebagai sumber kebenaran (*source of truth*) untuk mencegah manipulasi kuota.

*   **Free Tier (Unauthenticated):**
    *   Dilacak berdasarkan **Alamat IP** atau *fingerprint* sesi.
    *   Maksimal **3 kredit optimasi** secara keseluruhan.
    *   Batas maksimal ukuran file: **10 MB** per gambar.
    *   Batas unggahan massal: Maksimal **3 gambar** dalam satu *request*.
*   **Pro Tier (Authenticated/Paid):**
    *   Divalidasi menggunakan token JWT (dihasilkan setelah pembayaran sukses via LemonSqueezy).
    *   Membuka batas maksimal ukuran file menjadi **50 MB** per gambar.
    *   Batas unggahan massal ditingkatkan hingga **30 gambar** dalam satu *request*.

---

## 3. Spesifikasi API Endpoint

*   **`POST /api/v1/optimize` (Mendukung *Bulk Upload*)**
    *   **Fungsi:** Menerima *payload* berupa array/daftar gambar (mendukung format JPEG, PNG, WEBP).
    *   **Logika Pengecekan:**
        1. Verifikasi token Pro atau cek limit IP pengguna gratis.
        2. Validasi jumlah file dalam satu unggahan (maks 3 untuk gratis, 30 untuk Pro).
        3. Menolak file secara individual jika ukurannya melebihi batasan (*threshold*) pengguna.
    *   **Proses:** Mendaftarkan setiap file ke dalam antrean Redis secara terpisah dan menyimpan semua ID ke SQLite.
    *   **Response:** `202 Accepted` dengan daftar `job_id` untuk setiap gambar.
*   **`GET /api/v1/status/{job_id}`**
    *   **Fungsi:** Endpoint *polling* untuk menggerakkan *progress bar* di klien.
    *   **Response:** Mengembalikan status terkini (`pending`, `processing`, `done`, `failed`), statistik rasio byte, dan URL Cloudflare R2 jika proses telah selesai.
*   **`POST /api/v1/webhooks/lemonsqueezy`**
    *   **Fungsi:** Menangkap *event* pembayaran untuk memberikan akses Pro.
    *   **Proses:** Memvalidasi *signature* LemonSqueezy, lalu menghasilkan sesi Pro untuk pengguna atau memperbarui `expiry_date` file menjadi 40 hari.

---

## 4. Keamanan & Validasi Mesin (Engine-Level Security)
Lapisan ini krusial untuk mencegah serangan dari file yang sengaja dirusak atau disusupi kode berbahaya, memastikan keamanan peladen dan integritas data ekspor.

*   **Pembersihan EXIF & Metadata (XSS Prevention):** Sebelum gambar diproses atau dikompresi, skrip Python (menggunakan *Pillow* atau *ExifRead*) akan memindai dan menghapus semua metadata EXIF. Ini mematikan potensi eksekusi *malicious script* (XSS) yang bersembunyi di balik data gambar.
*   **Validasi Tipe Berkas (*Magic Number*):** Pemeriksaan `python-magic` untuk memastikan struktur heksadesimal berkas benar-benar cocok dengan JPEG, PNG, atau WEBP, bukan sekadar melihat ekstensi `.png`.
*   **Proteksi *Out-of-Memory* (OOM Killer Prevention):** Pembatasan total piksel gambar via Pillow (`Image.MAX_IMAGE_PIXELS`) untuk menangkal serangan *Decompression Bomb*.

---

## 5. Spesifikasi *Worker* & Penanganan Antrean (BullMQ)
Menangani 30 gambar resolusi tinggi (50 MB) dari pengguna Pro membutuhkan manajemen antrean yang presisi agar RAM 1 GB tidak kelebihan beban.

*   **Strict Concurrency:** Ditetapkan pada `concurrency: 1`. *Worker* hanya memproses satu gambar pada satu waktu secara sekuensial. Jika pengguna Pro mengunggah 30 gambar, antrean akan diselesaikan satu per satu.
*   **Streaming I/O:** Membaca file gambar secara *chunked* agar tidak memuat keseluruhan file berukuran 50 MB langsung ke dalam memori RAM sekaligus.
*   **Pembersihan Otomatis Memori Bebas:** Pemanggilan fungsi pelepasan memori (*garbage collection*) di Python segera setelah satu gambar selesai diunggah ke Cloudflare R2 dan file lokal di `/tmp/uploads` dihapus.

---

## 6. Skema Database (SQLite WAL Mode)
Tabel dibuat sangat efisien. Logika lokalisasi bahasa (EN/ID) hanya ditangani di *frontend*, sehingga *backend* tetap *stateless* terhadap preferensi bahasa antarmuka.

**Tabel `users_quota` (Pelacakan Free Tier)**
*   `ip_hash` / `session_id` (Primary Key)
*   `credits_used` (Integer, Default 0)
*   `is_pro` (Boolean, Default false)

**Tabel `jobs` (Pelacakan Proses & Hasil)**
*   `id` (String/UUID v4, Primary Key)
*   `session_id` (Foreign Key ke pelacakan kuota)
*   `status` (Enum: `pending`, `processing`, `done`, `failed`)
*   `original_size` (Integer)
*   `optimized_size` (Integer)
*   `r2_url` (String, Nullable)
*   `created_at` (Timestamp)
*   `expiry_date` (Timestamp) — Otomatis 7 hari (Free) atau 40 hari (Pro).

---

## 7. Skrip Otomatisasi (Cron Jobs) & *Fallback*
*   **Skrip `cleanup_expired_files.py` (Berjalan setiap jam 00:00 WIB):** Memindai semua baris di SQLite di mana `expiry_date` telah terlampaui. Skrip ini akan melakukan *API Call* ke Cloudflare R2 untuk menghapus objek gambar selamanya, menjamin klaim privasi di poin 2 (Frontend PRD).
*   **Sistem Notifikasi Gagal (Email Fallback):** Jika proses terhenti atau file korup, integrasi API Resend akan mengirim pesan yang memberitahukan status kegagalan kepada pengguna, agar waktu tunggu pengguna tidak terbuang sia-sia.
