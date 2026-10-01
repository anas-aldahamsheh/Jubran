"""Uploaded dish photos live as files on disk (MEDIA_DIR), not inside the database.

The database keeps only a short storage key per photo. A photo never changes once
saved (a new upload gets a new key), so it can be cached by browsers for a long time.
"""
import os
import uuid
from pathlib import Path
from typing import Optional

from jubran.settings import settings

_EXTENSIONS = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif"}


class MediaStore:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()

    def path(self, key: str) -> Path:
        """The file for ``key``; refuses anything that would leave the media folder."""
        target = (self.root / key).resolve()
        if self.root not in target.parents:
            raise ValueError("Invalid media key")
        return target

    def save(self, data: bytes, content_type: str, folder: str = "products") -> str:
        key = f"{folder}/{uuid.uuid4().hex}.{_EXTENSIONS.get(content_type, 'bin')}"
        target = self.path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".part")
        with open(temporary, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)  # never a half-written photo under the real name
        return key

    def delete(self, key: Optional[str]) -> None:
        if not key:
            return
        try:
            self.path(key).unlink(missing_ok=True)
        except (OSError, ValueError):
            pass  # a leftover file is harmless; a failed request would not be


def media_store() -> MediaStore:
    return MediaStore(Path(settings.MEDIA_DIR))
