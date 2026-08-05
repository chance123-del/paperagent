from __future__ import annotations

from pathlib import Path
import re
import shutil

from .models import RepairAction


def add_bibliography_to_project(project_dir: Path, uploaded_bib: str | None) -> str | None:
    if not uploaded_bib:
        return None
    source = Path(uploaded_bib)
    if source.suffix.lower() != ".bib":
        raise ValueError("Reference input must be a BibTeX (.bib) file.")
    destination = project_dir / "references.bib"
    shutil.copy2(source, destination)
    return destination.stem


def bibliography_keys(bib_path: Path) -> list[str]:
    """Return BibTeX keys in library order for numeric-marker conversion."""
    content = bib_path.read_text(encoding="utf-8", errors="replace")
    keys = re.findall(r"(?im)^\s*@\w+\s*\{\s*([^,\s]+)\s*,", content)
    return list(dict.fromkeys(key.strip() for key in keys if key.strip()))


def _numbers_from_marker(value: str) -> list[int]:
    values: list[int] = []
    for part in re.split(r"[,，]", value):
        part = part.strip()
        if not part:
            continue
        range_match = re.fullmatch(r"(\d+)\s*[-–]\s*(\d+)", part)
        if range_match:
            start, end = map(int, range_match.groups())
            if end >= start and end - start < 50:
                values.extend(range(start, end + 1))
            continue
        if part.isdigit():
            values.append(int(part))
    return list(dict.fromkeys(values))


def apply_numeric_markers(
    text: str, bib_path: Path | None, actions: list[RepairAction]
) -> tuple[str, list[tuple[int | str, str]], list[int | str]]:
    r"""Turn numeric and explicit [CITE:key] markers into \cite commands.

    The mapping deliberately follows the uploaded library order. This matches the
    conventional workflow where students export their existing numbered reference
    list to BibTeX before reformatting for a new journal.
    """
    if not bib_path or not bib_path.exists():
        return text, [], []
    keys = bibliography_keys(bib_path)
    if not keys:
        return text, [], []

    used_numbers: list[int | str] = []
    unresolved: list[int | str] = []

    def replace(match: re.Match[str]) -> str:
        numbers = _numbers_from_marker(match.group(1))
        if not numbers:
            return match.group(0)
        usable = [number for number in numbers if 1 <= number <= len(keys)]
        unresolved.extend(number for number in numbers if number not in usable)
        if not usable:
            return match.group(0)
        used_numbers.extend(usable)
        return r"\cite{" + ",".join(keys[number - 1] for number in usable) + "}"

    key_lookup = {key.lower(): key for key in keys}

    def replace_explicit(match: re.Match[str]) -> str:
        requested = [item.strip() for item in re.split(r"[;,]", match.group(1)) if item.strip()]
        resolved = [key_lookup[item.lower()] for item in requested if item.lower() in key_lookup]
        unresolved.extend(item for item in requested if item.lower() not in key_lookup)
        used_numbers.extend(f"CITE:{key}" for key in resolved)
        return r"\cite{" + ",".join(resolved) + "}" if resolved else match.group(0)

    converted = re.sub(r"\[CITE:\s*([^\]]+)\]", replace_explicit, text, flags=re.IGNORECASE)
    # Numeric square-bracket markers are intentionally the only accepted shorthand.
    # This avoids changing mathematical expressions or author-year citations.
    converted = re.sub(r"(?<!\\)\[\s*(\d+(?:\s*[-–,，]\s*\d+)*)\s*\]", replace, converted)
    used_numbers = list(dict.fromkeys(used_numbers))
    unresolved = list(dict.fromkeys(unresolved))
    if used_numbers:
        actions.append(RepairAction("citation_markers", f"Converted {len(used_numbers)} explicit or numeric citation markers."))
    mappings: list[tuple[int | str, str]] = []
    for marker in used_numbers:
        if isinstance(marker, int):
            mappings.append((marker, keys[marker - 1]))
        else:
            mappings.append((marker, marker.split(":", 1)[1]))
    return converted, mappings, unresolved


def remove_embedded_reference_list(text: str, actions: list[RepairAction]) -> str:
    """Remove an imported prose reference section before LaTeX regenerates it."""
    pattern = re.compile(r"\\(?:section|section\*)\{(?:References|Reference|参考文献)\}.*?(?=\\end\{document\})", re.IGNORECASE | re.DOTALL)
    cleaned, count = pattern.subn("", text, count=1)
    if count:
        actions.append(RepairAction("embedded_reference_list", "Removed the imported reference list; it will be regenerated from the uploaded library."))
    return cleaned


def apply_bibliography(text: str, database_name: str | None, rules: dict, actions: list[RepairAction]) -> str:
    if not database_name:
        return text
    style = rules.get("bibliographystyle", "plain")
    style_command = rf"\bibliographystyle{{{style}}}"
    bibliography_command = rf"\bibliography{{{database_name}}}"
    package = rules.get("citation_package")
    if package and not re.search(rf"\\usepackage(?:\[[^\]]*\])?\{{{re.escape(package)}\}}", text):
        text = text.replace("\\begin{document}", rf"\usepackage{{{package}}}" + "\n\\begin{document}", 1)
        actions.append(RepairAction("citation_package", f"Inserted citation package '{package}'."))
    if re.search(r"\\bibliographystyle\{[^}]*\}", text):
        text = re.sub(r"\\bibliographystyle\{[^}]*\}", style_command, text, count=1)
    else:
        text = text.replace("\\end{document}", style_command + "\n\\end{document}", 1)
    if not re.search(r"\\bibliography\{[^}]*\}|\\printbibliography", text):
        text = text.replace("\\end{document}", bibliography_command + "\n\\end{document}", 1)
        actions.append(RepairAction("missing_bibliography", f"Linked bibliography database '{database_name}.bib'."))
    actions.append(RepairAction("bibliography_style", f"Applied bibliography style '{style}'."))
    return text
