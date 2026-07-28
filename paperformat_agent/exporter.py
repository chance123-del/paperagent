from __future__ import annotations

"""Formal delivery exports for the generated LaTeX manuscript."""

import re
import shutil
import subprocess
from pathlib import Path


def _plain_latex(value: str) -> str:
    value = re.sub(r"\\(?:textbf|textit|emph|underline)\{([^{}]*)\}", r"\1", value)
    value = re.sub(r"\\href\{[^{}]*\}\{([^{}]*)\}", r"\1", value)
    value = value.replace(r"\&", "&").replace(r"\%", "%").replace(r"\_", "_")
    value = re.sub(r"\\[a-zA-Z]+(?:\[[^\]]*\])?", "", value)
    return value.replace("{", "").replace("}", "").strip()


def _fallback_docx(tex_path: Path, output_path: Path, project_dir: Path) -> str:
    from docx import Document
    from docx.shared import Cm, Pt

    source = tex_path.read_text(encoding="utf-8", errors="replace")
    document = Document()
    section = document.sections[0]
    section.top_margin = Cm(2.54)
    section.bottom_margin = Cm(2.54)
    section.left_margin = Cm(2.54)
    section.right_margin = Cm(2.54)
    normal = document.styles["Normal"]
    normal.font.name = "Times New Roman"
    normal.font.size = Pt(11)

    title = re.search(r"\\title\{(.+?)\}", source, re.DOTALL)
    if title:
        document.add_heading(_plain_latex(title.group(1)), level=0)

    in_abstract = False
    in_figure = False
    paragraph: list[str] = []

    def flush() -> None:
        if paragraph:
            text = _plain_latex(" ".join(paragraph))
            if text:
                document.add_paragraph(text)
            paragraph.clear()

    for raw in source.splitlines():
        line = raw.strip()
        if not line or line.startswith("%"):
            flush()
            continue
        if line.startswith(r"\begin{abstract}"):
            flush()
            in_abstract = True
            document.add_heading("Abstract", level=1)
            continue
        if line.startswith(r"\end{abstract}"):
            flush()
            in_abstract = False
            continue
        heading = re.match(r"\\(section|subsection|subsubsection)\{(.+)\}", line)
        if heading:
            flush()
            document.add_heading(_plain_latex(heading.group(2)), level={"section": 1, "subsection": 2, "subsubsection": 3}[heading.group(1)])
            continue
        if line.startswith(r"\begin{figure}"):
            flush()
            in_figure = True
            continue
        image = re.search(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}", line)
        if image:
            candidate = project_dir / image.group(1)
            if candidate.exists():
                try:
                    document.add_picture(str(candidate), width=Cm(14))
                except Exception:
                    pass
            continue
        caption = re.search(r"\\caption\{(.+)\}", line)
        if caption:
            flush()
            document.add_paragraph(_plain_latex(caption.group(1)), style="Caption")
            continue
        if line.startswith(r"\end{figure}"):
            in_figure = False
            continue
        if line.startswith("\\") or in_figure:
            continue
        paragraph.append(line)

    flush()
    document.core_properties.title = _plain_latex(title.group(1)) if title else tex_path.stem
    document.save(output_path)
    return "Word exported with the built-in structured DOCX converter (Pandoc was not found)."


def export_docx_from_tex(tex_path: str | Path, output_path: str | Path, project_dir: str | Path) -> tuple[bool, str]:
    """Export a DOCX, preferring Pandoc while guaranteeing a local fallback."""
    tex = Path(tex_path).resolve()
    destination = Path(output_path).resolve()
    project = Path(project_dir).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    pandoc = shutil.which("pandoc")
    if pandoc:
        command = [pandoc, str(tex), "--from=latex", "--to=docx", f"--resource-path={project}", "-o", str(destination)]
        completed = subprocess.run(command, cwd=str(project), capture_output=True, text=True, encoding="utf-8", errors="replace")
        if completed.returncode == 0 and destination.exists() and destination.stat().st_size:
            return True, "Word exported with Pandoc."
        pandoc_note = (completed.stderr or completed.stdout).strip()
    else:
        pandoc_note = "Pandoc not found"
    try:
        return True, _fallback_docx(tex, destination, project)
    except Exception as exc:
        return False, f"Word export failed: {pandoc_note}; fallback error: {exc}"
