from __future__ import annotations

import shutil
import subprocess
import re
import sys
import threading
from pathlib import Path


_ACTIVE_PROCESSES: set[subprocess.Popen[str]] = set()
_PROCESS_LOCK = threading.Lock()


def resolve_tectonic_binary(explicit_path: str | None = None) -> str | None:
    candidates = []
    if explicit_path:
        candidates.append(Path(explicit_path))

    bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    candidates.append(bundle_root / "tools" / "tectonic.exe")
    candidates.append(Path.cwd() / "tools" / "tectonic.exe")
    which_path = shutil.which("tectonic")
    if which_path:
        candidates.append(Path(which_path))

    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return None


def resolve_xelatex_binary() -> str | None:
    candidates = []
    which_path = shutil.which("xelatex")
    if which_path:
        candidates.append(Path(which_path))
    candidates.append(Path.home() / "AppData" / "Local" / "Programs" / "MiKTeX" / "miktex" / "bin" / "x64" / "xelatex.exe")
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return None


def _run_command(command: list[str], cwd: Path, timeout_seconds: int = 45) -> tuple[bool, str]:
    process: subprocess.Popen[str] | None = None
    try:
        process = subprocess.Popen(
            command,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        with _PROCESS_LOCK:
            _ACTIVE_PROCESSES.add(process)
        stdout, stderr = process.communicate(timeout=timeout_seconds)
        return process.returncode == 0, (stdout or "") + (stderr or "")
    except subprocess.TimeoutExpired:
        if process:
            process.kill()
            stdout, stderr = process.communicate()
            output = (stdout or "") + (stderr or "")
        else:
            output = ""
        return False, output + f"\nCompilation timed out after {timeout_seconds} seconds."
    finally:
        if process:
            with _PROCESS_LOCK:
                _ACTIVE_PROCESSES.discard(process)


def cancel_active_compilations() -> int:
    with _PROCESS_LOCK:
        processes = list(_ACTIVE_PROCESSES)
    for process in processes:
        if process.poll() is None:
            process.terminate()
    return len(processes)


def _compile_with_xelatex(input_file: Path, output_dir: Path, xelatex: str) -> tuple[bool, str]:
    command = [xelatex, "-interaction=nonstopmode", "-halt-on-error", f"-output-directory={output_dir}", str(input_file)]
    ok, output = _run_command(command, input_file.parent)
    if not ok:
        missing_class = re.search(r"File `([^`]+\.cls)' not found", output)
        timed_out = "Compilation timed out" in output
        preview_input = output_dir / "preview_main.tex"
        source = input_file.read_text(encoding="utf-8", errors="replace")
        preview_source = re.sub(r"\\documentclass(?:\[[^\]]*\])?\{[^}]*\}", r"\\documentclass[12pt]{article}", source, count=1)
        preview_input.write_text(preview_source, encoding="utf-8")
        preview_command = [xelatex, "-interaction=nonstopmode", "-halt-on-error", f"-output-directory={output_dir}", str(preview_input)]
        preview_ok, preview_output = _run_command(preview_command, output_dir)
        preview_pdf = output_dir / "preview_main.pdf"
        if preview_pdf.exists():
            shutil.copy2(preview_pdf, output_dir / f"{input_file.stem}.pdf")
            if missing_class:
                reason = f"required class '{missing_class.group(1)}' is unavailable"
            elif timed_out:
                reason = "official template compilation exceeded 45 seconds"
            else:
                reason = "official template compilation reported a LaTeX error"
            quality_note = "" if preview_ok else " The source contains recoverable LaTeX text errors; inspect the preview and feedback report."
            note = f"\nPreview fallback: {reason}; generated an article-class preview PDF." + quality_note
            return True, output + "\n" + preview_output + note
        return False, output

    source = input_file.read_text(encoding="utf-8", errors="replace")
    if "\\bibliography{" in source:
        for bibliography in input_file.parent.rglob("*.bib"):
            destination = output_dir / bibliography.name
            if not destination.exists():
                shutil.copy2(bibliography, destination)
        bibtex = Path(xelatex).with_name("bibtex.exe")
        if not bibtex.exists():
            bibtex = Path(shutil.which("bibtex") or "")
        if not bibtex.exists():
            return False, output + "\nBibTeX executable was not found."
        bib_ok, bib_output = _run_command([str(bibtex), input_file.stem], output_dir)
        output += "\n" + bib_output
        if not bib_ok:
            return False, output
        for _ in range(2):
            rerun_ok, rerun_output = _run_command(command, input_file.parent)
            output += "\n" + rerun_output
            if not rerun_ok:
                return False, output
    return True, output


def compile_tex(input_path: str | Path, outdir: str | Path, tectonic_path: str | None = None) -> tuple[bool, str]:
    input_file = Path(input_path).resolve()
    output_dir = Path(outdir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    xelatex = resolve_xelatex_binary()
    if xelatex:
        ok, output = _compile_with_xelatex(input_file, output_dir, xelatex)
        return ok, "Compiler: MiKTeX XeLaTeX\n" + output

    binary = resolve_tectonic_binary(tectonic_path)
    if not binary:
        return False, "No local LaTeX compiler was found. Install MiKTeX or provide Tectonic."

    command = [
        binary,
        str(input_file),
        "--outdir",
        str(output_dir),
        "--keep-logs",
        "--keep-intermediates",
        "--synctex",
    ]

    ok, output = _run_command(command, input_file.parent)
    return ok, "Compiler: Tectonic\n" + output


def explain_compile_failure(log_text: str) -> str:
    lowered = log_text.lower()
    if "failed to download" in lowered or "client error (connect)" in lowered:
        return "Compilation failed because Tectonic tried to download missing LaTeX resources, but network access was blocked."
    if "not found" in lowered and "font" in lowered:
        return "Compilation failed because required fonts or LaTeX resources were not available locally."
    return "Compilation failed. Please check the compile log for details."
