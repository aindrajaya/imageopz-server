Log `multipart.multipart - DEBUG - Calling on_part_data ...` itu **bukan error**—itu debug dari `python-multipart` saat parsing `multipart/form-data` (kalau multiple image, chunk-nya banyak jadi kelihatan “kacau”). Sama juga `PIL.PngImagePlugin - DEBUG ...` dan `watchfiles.main - DEBUG ...` itu efek `DEBUG=True`.

Kenapa “menunggu lama” waktu multiple image biasanya karena kombinasi ini:

- **Worker concurrency = 1** (`app/workers/worker_runner.py`), jadi kalau upload 10 gambar, diproses **serial** (total waktu = penjumlahan).
- **Frontend polling terlalu agresif**: tiap job dipoll sendiri-sendiri, interval kecil → jadi ratusan request `/api/v1/status/{job_id}` (kelihatan di log kamu).
- **Frontend salah kondisi berhenti**: harus berhenti saat `status` jadi `"done"`/`"failed"`, jangan nunggu `r2_url` terisi. Di local tanpa R2, `r2_url` bisa `null` tapi `status` sudah `"done"`.

Checklist cepat:
- Pastikan proses worker jalan (`python run_worker.py`). Kalau worker mati, job akan “pending” terus.
- Di frontend, polling per job:
  - interval minimal `1000–2000ms`, stop ketika `status in ["done","failed"]`
  - tambahkan backoff (mis. 1s → 2s → 4s)
- Kalau kamu mau lebih rapi untuk bulk: bikin 1 poll untuk semua job (butuh endpoint bulk status). Kalau kamu setuju, aku bisa tambahkan endpoint `POST /api/v1/status/bulk` supaya frontend cukup 1 request per tick.

Issue happen when 2nd attempt