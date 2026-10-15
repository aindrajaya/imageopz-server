# Contract: presigned uploads API (for T-006c)

Status: implemented in T-006b (backend). Source: `app/routes/uploads.py`, `app/services/uploads.py`.
`POST /api/v1/optimize` still exists until T-006b2 removes it; new clients use only this flow.

## Flow
1. `POST /api/v1/uploads/presign` with the file list -> one job, object key and presigned PUT URL per file.
   Credits are reserved now (one per file).
2. Browser `PUT`s each file's bytes straight to its `upload_url` (R2), with exactly the returned headers.
3. `POST /api/v1/uploads/complete` with the job ids -> each job becomes `pending` (queued) or `rejected` (credit back).
4. Poll `GET /api/v1/status/{job_id}` as before (`pending` -> `processing` -> `done` | `failed`).

## Identity (both endpoints)
Same as before: cookie `imageopz_sid` or header `X-Session-Id`, optional `Authorization: Bearer <Supabase JWT>`.
Use the same identity for presign and complete: jobs belong to the quota that presigned them; another identity gets 404
(this includes signing in between presign and complete).

## POST /api/v1/uploads/presign
Request:
```json
{"files": [{"name": "photo.jpg", "size": 482113, "content_type": "image/jpeg"}],
 "auth_token": null}
```
- `name`: 1-255 chars, extension `.jpg|.jpeg|.png|.webp`; stored in the DB only, never in the object key.
- `size`: exact byte size of the file (`File.size`), > 0. Signed into the URL: a body of any other length is refused by R2.
- `content_type`: `image/jpeg|image/png|image/webp` (lower-cased by the server).
- `auth_token`: LEGACY optional Pro JWT from the webhook response; omit it.

Response `201`:
```json
{"files": [{"job_id": "1b4e...", "object_key": "uploads/1b4e...", "upload_url": "https://<account>.r2.cloudflarestorage.com/...",
            "method": "PUT", "headers": {"Content-Type": "image/jpeg"}, "expires_at": "2026-10-15T09:15:00"}],
 "complete_by": "2026-10-15T09:30:00"}
```
Timestamps are UTC without offset. `upload_url` is valid for 15 min (`expires_at`); `/complete` must be called before
`complete_by` (30 min after presign).

Errors (`ErrorResponse`: `{"success": false, "error", "message", "details"}`). They are checked before any job, credit
or object is written; identity resolution may still create the session's quota row (as `/optimize` did):

| Status | error | When |
|---|---|---|
| 400 | `UNSUPPORTED_TYPE` | content type or extension not allowed |
| 413 | `FILE_TOO_LARGE` | `size` above the tier limit (free 10 MB, Pro 50 MB) |
| 429 | `QUOTA_EXCEEDED` | too many files for the tier (free 3, Pro 30), Pro expired, or not enough credits |
| 429 | `ACTIVATION_PENDING` | guest asked for more than 3 files right after starting a checkout; `details.retry_after_ms` |
| 422 | `VALIDATION_ERROR` | malformed body |
| 503 | `UPLOADS_UNAVAILABLE` | uploads storage not configured or signing failed (credits not kept) |

## The PUT (browser -> R2)
- `fetch(upload_url, {method: "PUT", headers: {"Content-Type": <headers["Content-Type"]>}, body: file})`.
- Send no other custom headers and no credentials (no cookies, no Authorization). The browser sets Content-Length.
- Success: `200`. `403` means a wrong Content-Type, a different length than declared, or an expired URL: do not
  retry the same URL; abandon the file or start a new presign.
- R2 CORS (verified in T-006b phase 0) allows origins `https://imageopz.aindrajaya.my.id` and `http://localhost:3000`,
  methods `GET, PUT`, header `content-type`, preflight cache 3600 s. No response headers are exposed (ETag is not needed).

## POST /api/v1/uploads/complete
Request: `{"job_ids": ["1b4e...", "..."]}` (1..tier file limit, duplicates ignored). Batch-only and idempotent.

Response `200`: `{"jobs": [{"job_id": "1b4e...", "status": "pending", "reason": null}]}` in request order.
- `pending`: accepted and queued once. Repeating `/complete` returns the current status and never re-queues.
- `rejected` + `reason`: refused; the credit is returned and the object deleted.
- Any other status (`processing`, `done`, `failed`, `rejected`): reported as is.

| reason | Meaning |
|---|---|
| `expired` | `/complete` came after `complete_by` (also set by the server-side sweeper) |
| `missing` | no object at the key (PUT never succeeded) |
| `size_mismatch` | stored size differs from the declared size or exceeds the tier limit |
| `type_mismatch` | stored Content-Type differs from the declared one |
| `invalid_image` | bad extension, magic bytes/MIME not the declared image type, or header unreadable in the first 64 KiB |
| `too_many_pixels` | width x height above the limit (250 MP configured; see follow-up F4) |
| `presign_failed` | the server could not sign the URLs (only seen via `/status`) |

Errors: `404 JOB_NOT_FOUND` (`details.job_ids`: unknown or not yours; nothing changes), `400 TOO_MANY_JOBS`,
`503 QUEUE_UNAVAILABLE` / `STORAGE_UNAVAILABLE` with `details.jobs` = the per-job list above. In a 503, jobs shown
`uploading` were not queued: retry `/complete` with the same ids (already-queued jobs are not queued twice).

## Client guidance
- Concurrency: 4-6 PUTs in flight; presign the whole batch once; call `/complete` once with all job ids whose PUT
  returned 200. Abandoned jobs become `rejected`/`expired` and get their credit back after `complete_by`, when the
  sweeper runs (scheduled from T-007/T-008) or when `/complete` is called for them.
- Retry a failed PUT (network error, 5xx) with backoff while `expires_at` is ahead; after that presign again.
- Retry `/complete` on network errors, 5xx or 503 with backoff; it is safe to repeat.
- Abort: stop PUTs and do not call `/complete` for those jobs. There is no cancel endpoint.
- Show `rejected` reasons per file; poll `/status` only for `pending` jobs.
