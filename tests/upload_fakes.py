"""
In-memory UploadStore for tests (no moto, no network).
No app imports at module level: test modules import this at collection time, before conftest sets the test env,
and importing app.config then would build settings from the developer .env.
"""
import io
from typing import Dict, Iterator, List, Optional, Tuple

from PIL import Image


def png_bytes(width: int = 8, height: int = 8) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (255, 0, 0)).save(buf, format="PNG")
    return buf.getvalue()


class FakeUploadStore:
    def __init__(self) -> None:
        self.objects: Dict[str, Tuple[bytes, Optional[str]]] = {}
        self.presigned: List[dict] = []
        self.deleted: List[str] = []
        self.fail_presign = False

    # Simulates the browser's PUT to the presigned URL.
    def put(self, key: str, data: bytes, content_type: Optional[str]) -> None:
        self.objects[key] = (data, content_type)

    def presign_put(self, key: str, content_type: str, expires_in: int, content_length: Optional[int] = None) -> str:
        if self.fail_presign:
            raise RuntimeError("presign failed")
        self.presigned.append({"key": key, "content_type": content_type, "expires_in": expires_in, "content_length": content_length})
        return f"https://fake-r2.invalid/{key}?X-Amz-Signature=fake"

    def head(self, key: str):
        from app.services.upload_store import ObjectHead

        if key not in self.objects:
            return None
        data, content_type = self.objects[key]
        return ObjectHead(size=len(data), content_type=content_type)

    def get_range(self, key: str, start: int, end: int) -> bytes:
        return self.objects[key][0][start:end + 1]

    def get_stream(self, key: str, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
        data = self.objects[key][0]
        for i in range(0, len(data), chunk_size):
            yield data[i:i + chunk_size]

    def delete(self, key: str) -> None:
        self.deleted.append(key)
        self.objects.pop(key, None)


class FakeEnqueuer:
    """Records enqueue calls; `accept(job_id) -> bool` decides success (no network)."""

    def __init__(self, accept=None) -> None:
        self.calls: List[str] = []
        self.attempts: List[Tuple[str, int]] = []
        self.accept = accept or (lambda job_id: True)

    def enqueue(self, job_id: str, attempt: int = 0) -> bool:
        self.calls.append(job_id)
        self.attempts.append((job_id, attempt))
        return self.accept(job_id)
