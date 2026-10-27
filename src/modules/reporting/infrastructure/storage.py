"""Filesystem export storage. Keys are namespaced by tenant and validated to
prevent path traversal; files are written under a single base directory
(a Docker volume in the stack)."""

from __future__ import annotations

import re
from pathlib import Path

from shared.domain.errors import DomainValidationError

_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}/[0-9a-f-]{36}\.[a-z0-9]{1,8}$")


class FilesystemExportStorage:
    def __init__(self, base_dir: str) -> None:
        self._base = Path(base_dir).resolve()

    def _path(self, key: str) -> Path:
        if not _KEY_RE.match(key):
            raise DomainValidationError("Invalid storage key.")
        path = (self._base / key).resolve()
        if not path.is_relative_to(self._base):
            raise DomainValidationError("Invalid storage key.")
        return path

    def put(self, key: str, content: bytes, content_type: str) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()
