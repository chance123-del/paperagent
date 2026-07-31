from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import shutil
import zipfile

from .archive_safety import safe_extract_zip
from .source_converter import load_source, render_latex


PREFERRED_MAIN_NAMES = ("main.tex", "thesis.tex", "paper.tex", "article.tex", "manuscript.tex")


@dataclass
class PreparedProject:
    source_name: str
    source_kind: str
    project_dir: Path
    main_tex_path: Path
    main_tex_encoding: str
    source_notes: list[str] = field(default_factory=list)


def _copy_directory_contents(source_dir: Path, target_dir: Path) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    for item in source_dir.iterdir():
        destination = target_dir / item.name
        if item.is_dir():
            shutil.copytree(item, destination, dirs_exist_ok=True)
        else:
            shutil.copy2(item, destination)


def _extract_zip(zip_path: Path, target_dir: Path) -> None:
    with zipfile.ZipFile(zip_path, "r") as archive:
        safe_extract_zip(archive, target_dir)


def _looks_like_main_tex(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = path.read_text(encoding="utf-8", errors="ignore")
    return "\\documentclass" in text and "\\begin{document}" in text

def find_main_tex(project_dir: Path) -> Path:
    tex_files = sorted(project_dir.rglob("*.tex"))
    if not tex_files:
        raise ValueError("No .tex file was found in the uploaded content.")

    candidates = [path for path in tex_files if _looks_like_main_tex(path)]
    if not candidates:
        names = ", ".join(path.name for path in tex_files[:10])
        raise ValueError(
            "Found .tex files, but none looked like a main LaTeX document with "
            f"\\documentclass and \\begin{{document}}. Candidates: {names}"
        )

    prioritized = []
    for preferred_name in PREFERRED_MAIN_NAMES:
        for candidate in candidates:
            if candidate.name.lower() == preferred_name:
                prioritized.append(candidate)
    if prioritized:
        return prioritized[0]

    candidates.sort(key=lambda path: (len(path.relative_to(project_dir).parts), path.name.lower()))
    return candidates[0]


def prepare_project(input_path: str | Path, workspace_dir: str | Path, rules: dict | None = None) -> PreparedProject:
    source_path = Path(input_path)
    if not source_path.exists():
        raise ValueError(f"Input file was not found: {source_path}")

    workspace = Path(workspace_dir)
    project_dir = workspace / "project"
    project_dir.mkdir(parents=True, exist_ok=True)

    suffix = source_path.suffix.lower()
    if suffix == ".zip":
        _extract_zip(source_path, project_dir)
        main_tex = find_main_tex(project_dir)
        from .text_io import read_text_best_effort

        _, detected_encoding = read_text_best_effort(main_tex)
        return PreparedProject(source_path.name, "zip", project_dir, main_tex, detected_encoding)
    if suffix == ".tex":
        _copy_directory_contents(source_path.parent, project_dir)
        main_tex = project_dir / source_path.name
        if not main_tex.exists():
            raise ValueError("The uploaded .tex file could not be copied into the working directory.")
        if not _looks_like_main_tex(main_tex):
            raise ValueError("The uploaded .tex file does not look like a main LaTeX document.")
        from .text_io import read_text_best_effort

        _, detected_encoding = read_text_best_effort(main_tex)
        return PreparedProject(source_path.name, "tex", project_dir, main_tex, detected_encoding)

    if suffix in {".docx", ".pdf", ".md", ".markdown"}:
        document = load_source(source_path, project_dir / "assets")
        main_tex = project_dir / "main.tex"
        from .text_io import write_text_with_encoding

        write_text_with_encoding(main_tex, render_latex(document, rules or {}))
        return PreparedProject(source_path.name, suffix.removeprefix("."), project_dir, main_tex, "utf-8", document.notes)

    raise ValueError("Unsupported input. Upload a .docx, .pdf, .md, .tex, or .zip LaTeX project.")


def package_project(project_dir: str | Path, destination_zip: str | Path) -> Path:
    project_path = Path(project_dir)
    destination = Path(destination_zip)
    destination.parent.mkdir(parents=True, exist_ok=True)
    archive_base = destination.with_suffix("")
    created = shutil.make_archive(str(archive_base), "zip", root_dir=str(project_path))
    return Path(created)
