"""Async HTTP downloader for OTA firmware packages.

Features:
- Streaming download with configurable chunk size
- Resume via HTTP Range headers when a .partial file exists
- SHA-256 integrity verification after full download
- Size limit check against Content-Length before downloading body
- on_progress callback for download monitoring
- Atomic rename: .partial file renamed to final path on success
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx


class HttpDownloader:
    """Async streaming HTTP downloader with resume and SHA-256 verification.

    Downloads firmware packages to a staging directory. If a .partial file
    from a previous interrupted download exists, it resumes using an HTTP
    Range request. On SHA-256 mismatch, the .partial file is deleted.
    """

    def __init__(
        self,
        staging_dir: Path,
        max_package_mb: int = 500,
        chunk_size: int = 65536,
        timeout_s: float = 300.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """Initialise the downloader.

        Args:
            staging_dir: Directory where downloaded files are staged.
                Created automatically if it does not exist.
            max_package_mb: Maximum allowed package size in megabytes.
                Downloads whose Content-Length exceeds this are rejected
                before any body bytes are transferred.
            chunk_size: HTTP streaming read chunk size in bytes.
            timeout_s: Total download timeout in seconds.
            transport: Optional httpx transport override for testing
                (e.g. httpx.MockTransport). If None, the default httpx
                transport is used.
        """
        self._staging_dir: Path = staging_dir
        self._max_package_mb: int = max_package_mb
        self._chunk_size: int = chunk_size
        self._timeout_s: float = timeout_s
        self._transport: httpx.AsyncBaseTransport | None = transport

    async def download(
        self,
        url: str,
        dest_filename: str,
        expected_sha256: str,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> Path:
        """Download a firmware package from ``url`` to the staging directory.

        Resumes from an existing .partial file if present. Verifies SHA-256
        of the complete file after download. On success, renames .partial to
        the final destination path.

        Args:
            url: HTTP or HTTPS URL of the firmware package.
            dest_filename: File name (not path) for the downloaded file within
                staging_dir (e.g. "firmware-1.2.3.tar.gz").
            expected_sha256: Lowercase hex SHA-256 digest of the complete file.
                Verified after full download.
            on_progress: Optional callback invoked after each chunk with
                (downloaded_bytes, total_bytes). total_bytes is 0 if unknown.

        Returns:
            Path to the downloaded file within staging_dir.

        Raises:
            ValueError: If Content-Length exceeds max_package_mb, or if the
                computed SHA-256 does not match expected_sha256.
            httpx.HTTPError: On network or HTTP errors.
        """
        # Ensure staging directory exists
        self._staging_dir.mkdir(parents=True, exist_ok=True)

        dest: Path = self._staging_dir / dest_filename
        partial: Path = dest.with_suffix(dest.suffix + ".partial")

        # Resume support: read existing partial file
        resume_from: int = 0
        hasher: hashlib._Hash = hashlib.sha256()

        if partial.exists():
            resume_from = partial.stat().st_size
            # Pre-hash the already-downloaded bytes
            with partial.open("rb") as fh:
                while True:
                    buf: bytes = fh.read(self._chunk_size)
                    if not buf:
                        break
                    hasher.update(buf)

        # Build request headers
        headers: dict[str, str] = {}
        if resume_from > 0:
            headers["Range"] = f"bytes={resume_from}-"

        # Perform the streaming download
        client_kwargs: dict[str, Any] = {}
        if self._transport is not None:
            client_kwargs["transport"] = self._transport

        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=self._timeout_s,
            **client_kwargs,
        ) as client:
            async with client.stream("GET", url, headers=headers) as response:
                response.raise_for_status()

                # Check declared Content-Length against size limit
                content_length_str: str | None = response.headers.get("content-length")
                if content_length_str is not None:
                    content_length: int = int(content_length_str)
                    total_size: int = resume_from + content_length
                    max_bytes: int = self._max_package_mb * 1024 * 1024
                    if total_size > max_bytes:
                        msg: str = (
                            f"Package too large: {total_size / (1024 * 1024):.1f} MB "
                            f"exceeds max {self._max_package_mb} MB"
                        )
                        raise ValueError(msg)
                    declared_total: int = total_size
                else:
                    declared_total = 0  # Unknown total size

                # Stream chunks, appending to .partial
                downloaded: int = resume_from
                with partial.open("ab") as fh:
                    async for chunk in response.aiter_bytes(self._chunk_size):
                        fh.write(chunk)
                        hasher.update(chunk)
                        downloaded += len(chunk)
                        if on_progress is not None:
                            on_progress(downloaded, declared_total)

        # Verify SHA-256 of the complete file
        actual_sha256: str = hasher.hexdigest()
        if actual_sha256 != expected_sha256.lower():
            partial.unlink(missing_ok=True)
            msg = (
                f"SHA-256 mismatch for {dest_filename}: "
                f"expected={expected_sha256} actual={actual_sha256}"
            )
            raise ValueError(msg)

        # Atomic rename: .partial -> dest
        partial.rename(dest)
        return dest

    def cleanup_staging(self) -> None:
        """Remove all files in the staging directory.

        Useful for cleaning up after a failed or cancelled update.
        """
        if not self._staging_dir.exists():
            return
        for item in self._staging_dir.iterdir():
            if item.is_file():
                item.unlink()
