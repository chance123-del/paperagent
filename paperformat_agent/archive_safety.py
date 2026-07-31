from __future__ import annotations

"""Bounded ZIP extraction for untrusted project uploads."""

from pathlib import Path
import shutil
import zipfile


MAX_ARCHIVE_FILES = 500
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_MEMBER_BYTES = 64 * 1024 * 1024
EXECUTABLE_SUFFIXES = {".bat", ".cmd", ".com", ".dll", ".exe", ".js", ".msi", ".ps1", ".vbs"}


def safe_extract_zip(archive: zipfile.ZipFile, destination: Path) -> None:
    """Extract a ZIP only after path, type, file-count, and size checks."""
    members = [member for member in archive.infolist() if not member.is_dir()]
    if len(members) > MAX_ARCHIVE_FILES:
        raise ValueError(f"Uploaded ZIP contains more than {MAX_ARCHIVE_FILES} files.")
    total_size = sum(member.file_size for member in members)
    if total_size > MAX_ARCHIVE_BYTES:
        raise ValueError("Uploaded ZIP exceeds the uncompressed size limit.")

    root = destination.resolve()
    for member in members:
        if member.file_size > MAX_MEMBER_BYTES:
            raise ValueError(f"ZIP member exceeds the size limit: {member.filename}")
        target = (destination / member.filename).resolve()
        if root not in target.parents:
            raise ValueError("Uploaded ZIP contains an unsafe path.")
        if target.suffix.lower() in EXECUTABLE_SUFFIXES:
            raise ValueError(f"Uploaded ZIP contains a disallowed executable file: {member.filename}")

    for member in members:
        target = (destination / member.filename).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(member) as source, target.open("wb") as output:
            shutil.copyfileobj(source, output)
