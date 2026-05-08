Berdasarkan log yang kamu bagikan, ada masalah **"Race Condition"** yang cukup jelas antara API (FastAPI) dan Worker (Python process). Inilah alasan mengapa gambar kamu sering macet di status *pending* lalu gagal.

Berikut adalah analisis masalah dan solusinya:

### 1. Masalah Utama: "Job not found in database"
Log menunjukkan peringatan berulang: 
`imageopz.queue - WARNING - Job [ID] not found in database`.

**Apa yang terjadi?**
* **Redis Terlalu Cepat:** Ketika kamu mengunggah gambar, FastAPI memasukkan ID pekerjaan ke dalam antrean Redis, lalu mencoba menulis data pekerjaan tersebut ke SQLite.
* **SQLite Terlambat:** Karena kamu menggunakan VPS 1vCPU, penulisan ke SQLite (meskipun dalam mode WAL) bisa memakan waktu sepersekian detik lebih lama.
* **Worker Gagal Menemukan Data:** Worker langsung mengambil ID dari Redis dan mencari barisnya di SQLite. Karena penulisan dari API belum selesai/komit, Worker menganggap pekerjaan itu tidak ada dan membiarkannya "menggantung" atau gagal.

### 2. Analisis Resource (Memori)
Log mencatat: `66.0M memory peak`.
* Angka ini adalah memori dasar *worker* saat sedang menunggu (idle).
* Ini membuktikan bahwa proses *worker* itu sendiri sangat ringan, namun saat proses kompresi gambar AI dimulai (yang tidak tercatat di puncak log sistem ini), penggunaan memori akan melonjak drastis melewati batas 1GB.

### 3. Perubahan dari Celery ke Background Worker
Log menunjukkan awalnya kamu mencoba menggunakan **Celery**, tapi gagal karena modul `app.worker` tidak ditemukan. Sekarang kamu beralih ke **Background Worker** kustom yang menggunakan `APScheduler` untuk pembersihan file. Ini sebenarnya lebih bagus untuk server 1GB karena lebih hemat *overhead* daripada Celery.

---

### Solusi untuk Memperbaiki "Pending & Gagal"

Untuk mengatasi masalah ini, kamu perlu meminta tim backend melakukan dua hal ini agar sinkron dengan **PRD v3**:

#### A. Tambahkan "Retry Logic" pada Worker
Worker jangan langsung menyerah jika data tidak ditemukan di database. Tambahkan jeda singkat (misalnya 500ms) sebelum mencari ulang.
```python
# Contoh logika di worker
job = db.query(Job).filter(Job.id == job_id).first()
if not job:
    time.sleep(0.5) # Beri waktu SQLite untuk selesai menulis
    job = db.query(Job).filter(Job.id == job_id).first()
```

#### B. Pastikan Urutan "DB First, Queue Second" di API
Pastikan FastAPI melakukan `db.commit()` **sebelum** memanggil `redis.lpush()`. Jika ID dimasukkan ke Redis sebelum transaksi database selesai, *Race Condition* di atas akan selalu terjadi.

#### C. Optimasi SQLite untuk Concurrency
Pastikan koneksi SQLite diinisialisasi dengan *timeout* yang cukup agar tidak terjadi *database locked* saat API dan Worker bekerja bersamaan:
```python
# database.py
engine = create_engine(
    "sqlite:///./imageopz.db", 
    connect_args={"check_same_thread": False, "timeout": 30} # Tambahkan timeout 30s
)
```

### Kesimpulan
Server 1vCPU/1GB kamu sebenarnya **bisa** menangani ini, tapi karena kecepatannya terbatas, koordinasi antara database dan antrean menjadi sangat sensitif. Dengan menambahkan jeda *retry* kecil di sisi *worker*, masalah "Job not found" dan status *pending* yang gagal seharusnya hilang.

Apakah kamu ingin saya buatkan *script* pendek untuk memantau penggunaan RAM secara *real-time* saat kamu melakukan unggah gambar, supaya kita bisa melihat seberapa dekat kita dengan batas 1GB?