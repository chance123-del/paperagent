from __future__ import annotations

from pathlib import Path


READ_ENCODINGS = ("utf-8", "utf-8-sig", "gb18030", "gbk")


def read_text_best_effort(path: str | Path) -> tuple[str, str]:
    file_path = Path(path)
    for encoding in READ_ENCODINGS:
        try:
            return file_path.read_text(encoding=encoding), encoding
        except UnicodeDecodeError:
            continue
    return file_path.read_text(encoding="utf-8", errors="replace"), "utf-8"


def write_text_with_encoding(path: str | Path, text: str, encoding: str = "utf-8") -> None:
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(text, encoding=encoding)
