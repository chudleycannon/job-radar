"""Candidate profile import and evidence helpers."""

from __future__ import annotations

import re
from pathlib import Path

from . import store


def cv_text(path: str | Path) -> str:
    p = Path(path).expanduser()
    if p.suffix.lower() == ".docx":
        from .runner import docx_to_text
        return docx_to_text(p)
    if p.suffix.lower() == ".pdf":
        from .rank import _pdf_to_text
        return _pdf_to_text(p)
    return p.read_text(encoding="utf-8", errors="ignore")


def import_cv(con, path: str | Path) -> int:
    """Import a CV into proposed, human-reviewable evidence.

    This is intentionally conservative. It creates reviewable notes from the
    source document instead of silently turning a presentation CV into trusted
    profile facts.
    """
    p = Path(path).expanduser()
    text = cv_text(p)
    if not text.strip():
        raise ValueError(f"could not read any text out of {p.name}")
    source = f"CV import: {p.name}"
    existing = store.get_meta(con, "candidate_profile_imported_cv_path", "")
    if existing == str(p.resolve()):
        return 0
    sections = _sections(text)
    if not sections:
        sections = [("Imported CV evidence", _clean(text)[:4000])]
    n = 0
    n += _import_keyword_groups(con, text, source)
    for title, body in sections[:24]:
        body = _clean(body)
        if len(body) < 20:
            continue
        if _is_keyword_section(title):
            continue
        store.add_candidate_evidence(
            con, title=title, body=body[:4000],
            category=_category(title, body),
            tags=_tags(title + "\n" + body),
            source=source, confidence=0.55, status="proposed")
        n += 1
    store.set_meta(con, "candidate_profile_imported_cv_path", str(p.resolve()))
    store.set_meta(con, "candidate_profile_imported_cv_name", p.name)
    return n


def suggest_from_screen_answer(con, uid: str, answer: str) -> int:
    """Turn a role-specific screening answer into proposed reusable evidence."""
    text = _clean(answer)
    if len(text) < 20:
        return 0
    title = _title_from_text(text)
    store.add_candidate_evidence(
        con, title=title, body=text[:4000], category=_category(title, text),
        tags=_tags(text), source=f"Screening answer: {uid[:12]}",
        confidence=0.65, status="proposed")
    return 1


def _import_keyword_groups(con, text: str, source: str) -> int:
    n = 0
    for title, keywords in _keyword_groups(text):
        store.add_candidate_keywords(
            con, title=title, keywords=keywords, source=source,
            status="proposed")
        n += 1
    return n


def _keyword_groups(text: str) -> list[tuple[str, list[str]]]:
    groups: list[tuple[str, list[str]]] = []
    for raw in text.splitlines():
        line = raw.strip().lstrip("-* ").strip()
        if not line or ":" not in line:
            continue
        title, rest = line.split(":", 1)
        title = title.strip()
        if len(title.split()) > 6 or len(title) > 70:
            continue
        keywords = [k.strip().strip(".") for k in rest.split(",") if k.strip()]
        if len(keywords) < 3:
            continue
        groups.append((title, keywords[:80]))
    return groups[:24]


def _is_keyword_section(title: str) -> bool:
    low = title.lower()
    return "core expertise" in low or "keyword" in low or low == "skills"


def _sections(text: str) -> list[tuple[str, str]]:
    lines = [l.rstrip() for l in text.replace("\r\n", "\n").split("\n")]
    sections: list[tuple[str, list[str]]] = []
    current = ["Profile"]
    body: list[str] = []
    for line in lines:
        clean = line.strip()
        if _heading(clean) and body:
            sections.append((current[0], body))
            current = [clean.strip("#: ")]
            body = []
        elif clean:
            body.append(clean)
    if body:
        sections.append((current[0], body))
    out = []
    for title, chunk in sections:
        joined = "\n".join(chunk).strip()
        if joined:
            out.append((title[:90], joined))
    return out


def _heading(line: str) -> bool:
    if not line or len(line) > 90:
        return False
    if line.startswith("#"):
        return True
    words = line.split()
    if len(words) > 8:
        return False
    alpha = [c for c in line if c.isalpha()]
    if alpha and sum(1 for c in alpha if c.isupper()) / len(alpha) > 0.7:
        return True
    known = {
        "profile", "summary", "experience", "employment", "career history",
        "professional experience", "skills", "technical skills",
        "education", "certifications", "selected achievements",
        "achievements", "projects", "personal statement",
    }
    return line.lower().strip(":") in known


def _category(title: str, body: str) -> str:
    text = f"{title}\n{body}".lower()
    if "certification" in text or "education" in text:
        return "certification"
    if "incident" in text or "major incident" in text:
        return "incident_management"
    if "governance" in text or "audit" in text or "risk" in text:
        return "governance"
    if "deploy" in text or "delivery" in text or "release" in text:
        return "delivery"
    if "lead" in text or "manager" in text or "head of" in text:
        return "leadership"
    if "skill" in text or "tool" in text or "technology" in text:
        return "skill"
    if re.search(r"\b\d+[%x]?\b", text):
        return "achievement"
    if "experience" in text or "employment" in text or "career" in text:
        return "employment"
    return "general"


def _tags(text: str) -> list[str]:
    low = text.lower()
    pairs = [
        ("incident", "incident management"), ("itil", "ITIL"),
        ("root cause", "root cause"), ("audit", "audit"),
        ("governance", "governance"), ("saas", "SaaS"),
        ("data", "data"), ("platform", "platform"),
        ("ci/cd", "CI/CD"), ("deployment", "deployment"),
        ("stakeholder", "stakeholder management"),
        ("leadership", "leadership"), ("customer", "customer impact"),
    ]
    return [label for needle, label in pairs if needle in low][:8]


def _clean(text: str) -> str:
    text = re.sub(r"\n{3,}", "\n\n", text.strip())
    return re.sub(r"[ \t]+", " ", text)


def _title_from_text(text: str) -> str:
    first = re.split(r"[.\n]", text.strip(), 1)[0]
    words = first.split()
    if len(words) > 10:
        first = " ".join(words[:10])
    return first.strip()[:80] or "Screening answer evidence"
