from __future__ import annotations

from dataclasses import dataclass
import copy
import json
import re
from urllib.parse import quote
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class JournalMatch:
    profile_id: str
    profile_name: str
    canonical_title: str
    publisher: str
    confidence: str
    source: str


JOURNAL_PROFILES: dict[str, dict] = {
    "generic": {"name": "Generic journal", "bibliographystyle": "plain", "citation_style": "numeric"},
    "ieee": {"name": "IEEE journal", "documentclass": "IEEEtran", "documentclass_options": "journal", "class_managed_layout": True, "bibliographystyle": "IEEEtran", "citation_package": "cite", "citation_style": "numeric"},
    "acm": {"name": "ACM publication", "documentclass": "acmart", "documentclass_options": "manuscript", "class_managed_layout": True, "bibliographystyle": "ACM-Reference-Format", "citation_style": "author-year"},
    "elsevier": {"name": "Elsevier journal", "documentclass": "elsarticle", "documentclass_options": "preprint", "class_managed_layout": True, "bibliographystyle": "elsarticle-num", "citation_style": "numeric"},
    "springer": {"name": "Springer Nature journal", "documentclass": "sn-jnl", "documentclass_options": "sn-mathphys-num", "class_managed_layout": True, "bibliographystyle": "sn-mathphys-num", "citation_style": "numeric"},
    "nature": {"name": "Nature Portfolio journal", "documentclass": "sn-jnl", "documentclass_options": "sn-mathphys-num", "class_managed_layout": True, "bibliographystyle": "sn-mathphys-num", "citation_style": "numeric"},
    "apa": {"name": "APA style", "bibliographystyle": "apalike", "citation_style": "author-year"},
}


def profile_choices() -> list[str]:
    return [f"{profile_id}: {profile['name']}" for profile_id, profile in JOURNAL_PROFILES.items()]


def _profile_from_text(value: str) -> str:
    normalized = value.lower()
    keywords = {
        "ieee": "ieee", "association for computing machinery": "acm", " acm": "acm",
        "elsevier": "elsevier", "springer": "springer", "nature": "nature",
        "american psychological association": "apa", "apa": "apa",
    }
    for keyword, profile_id in keywords.items():
        if keyword in normalized:
            return profile_id
    return "generic"


def _crossref_lookup(journal_name: str) -> tuple[str, str] | None:
    url = "https://api.crossref.org/journals?query=" + quote(journal_name) + "&rows=1"
    request = Request(url, headers={"User-Agent": "PaperFormat-Agent/1.0 (journal-format-matcher)"})
    try:
        with urlopen(request, timeout=6) as response:
            payload = json.loads(response.read().decode("utf-8"))
        items = payload.get("message", {}).get("items", [])
        if items:
            item = items[0]
            return item.get("title", journal_name), item.get("publisher", "")
    except OSError:
        return None
    return None


def resolve_journal(journal_name: str | None) -> JournalMatch:
    name = re.sub(r"\s+", " ", journal_name or "").strip()
    if not name:
        return JournalMatch("generic", "Generic journal", "", "", "none", "No journal name provided")
    direct_profile = _profile_from_text(name)
    if direct_profile != "generic":
        profile = JOURNAL_PROFILES[direct_profile]
        return JournalMatch(direct_profile, profile["name"], name, "", "name matched", "Local publisher-name matching")
    result = _crossref_lookup(name)
    if result:
        title, publisher = result
        profile_id = _profile_from_text(f"{title} {publisher}")
        confidence = "publisher matched" if profile_id != "generic" else "metadata only"
        source = "Crossref metadata"
    else:
        title, publisher = name, ""
        profile_id = _profile_from_text(name)
        confidence = "name matched" if profile_id != "generic" else "unmatched"
        source = "Local name matching (network unavailable or no exact result)"
    profile = JOURNAL_PROFILES[profile_id]
    return JournalMatch(profile_id, profile["name"], title, publisher, confidence, source)


def apply_journal_profile(base_rules: dict, profile_id: str) -> dict:
    profile = JOURNAL_PROFILES.get(profile_id, JOURNAL_PROFILES["generic"])
    rules = copy.deepcopy(base_rules)
    rules.update({key: value for key, value in profile.items() if key != "name"})
    rules["journal_profile"] = profile_id
    return rules
