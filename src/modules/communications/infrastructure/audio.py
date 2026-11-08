"""Turns a browser recording into a WhatsApp voice message.

Browsers record WebM or MP4, while WhatsApp plays only Ogg Opus as a voice message.
"""

from __future__ import annotations

import subprocess
import tempfile

from shared.domain.errors import DomainValidationError

_TIMEOUT_SECONDS = 30


class FfmpegVoiceEncoder:
    def encode(self, content: bytes) -> bytes:
        # From a file, not a pipe: MP4 keeps its index at the end and needs seeking.
        with tempfile.NamedTemporaryFile() as source:
            source.write(content)
            source.flush()
            command = [
                "ffmpeg", "-v", "error", "-i", source.name, "-vn",
                "-c:a", "libopus", "-b:a", "32k", "-f", "ogg", "pipe:1",
            ]  # fmt: skip
            try:
                result = subprocess.run(  # noqa: S603  # fixed command, our temp file
                    command, capture_output=True, timeout=_TIMEOUT_SECONDS, check=True
                )
            except (subprocess.SubprocessError, OSError) as exc:
                raise DomainValidationError("The recording could not be processed.") from exc
        return result.stdout
