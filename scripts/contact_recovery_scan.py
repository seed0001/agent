from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from datetime import datetime

ROOTS = [
    Path(r"C:\Users\aztre\Desktop\Andrew-Core-Foundation"),
    Path(r"C:\Users\aztre\Desktop\agent"),
    Path(r"C:\Users\aztre\Desktop\Travis Projects"),
    Path(r"C:\Users\aztre\Desktop\caretaker"),
]

SKIP_PARTS = {
    ".git", "__pycache__", ".pytest_cache", "node_modules", ".venv", "venv",
    "luxtts", "suno_chrome_profile", "generated_images", "voices", "zipvoice",
}
TEXT_EXTS = {".md", ".txt", ".json", ".jsonl", ".log", ".py", ".env", ".csv"}
DB_EXTS = {".db", ".sqlite", ".sqlite3"}

REL_TERMS = [
    "family", "friend", "friends", "best friend", "good friend", "wife", "girlfriend",
    "mom", "mother", "dad", "father", "brother", "sister", "son", "daughter", "kid", "kids",
    "child", "children", "cousin", "uncle", "aunt", "grandma", "grandpa", "discord", "contact",
]
KNOWN_NAMES = ["Travis", "Andrew", "Brandon", "Chance", "Sarah", "Leora", "Nova", "Adam", "Luna"]
STOP_NAMES = {
    "Andrew", "Travis", "Discord", "Good", "Vibes", "OpenAI", "Ollama", "Gemma", "Grok",
    "Creator", "User", "Assistant", "System", "Memory", "Profile", "Project", "Roadmap",
    "Windows", "Python", "PowerShell", "GitHub", "README", "Tool", "Tools", "Channel",
    "Category", "Role", "Roles", "Text", "Voice", "Start", "Community", "Help", "Lab",
    "The", "This", "That", "When", "What", "Where", "Current", "Recent", "Core",
}
NAME_RE = re.compile(r"\b[A-Z][a-zA-Z]{2,}(?:\s+[A-Z][a-zA-Z]{2,})?\b")


def should_skip(path: Path) -> bool:
    return any(part in SKIP_PARTS for part in path.parts)


def read_text(path: Path) -> str:
    return path.read_text(errors="ignore")


def snippets_for_terms(text: str, terms: list[str], radius: int = 220, max_per_term: int = 5):
    low = text.lower()
    out = []
    for term in terms:
        start = 0
        count = 0
        t = term.lower()
        while count < max_per_term:
            i = low.find(t, start)
            if i < 0:
                break
            a = max(0, i - radius)
            b = min(len(text), i + len(term) + radius)
            snip = " ".join(text[a:b].split())
            out.append((term, snip))
            start = i + len(term)
            count += 1
    return out


def classify_name(name: str) -> bool:
    if name in STOP_NAMES:
        return False
    if len(name) < 3:
        return False
    if name.upper() == name:
        return False
    # Avoid channel-like title fragments
    if any(x in name for x in ["Channel", "Category", "Role", "Memory", "Model"]):
        return False
    return True


def scan_text_file(path: Path, findings: dict, name_counts: Counter):
    try:
        text = read_text(path)
    except Exception:
        return
    if not text:
        return
    rel_hits = snippets_for_terms(text, REL_TERMS, max_per_term=8)
    known_hits = snippets_for_terms(text, KNOWN_NAMES, max_per_term=10)
    if rel_hits or known_hits:
        findings[str(path)].extend(rel_hits + known_hits)
    for name in NAME_RE.findall(text):
        if classify_name(name):
            name_counts[name] += 1


def scan_db(path: Path, findings: dict, name_counts: Counter):
    try:
        con = sqlite3.connect(str(path))
        cur = con.cursor()
        tables = [r[0] for r in cur.execute("select name from sqlite_master where type='table'").fetchall()]
        combined = []
        for table in tables:
            try:
                cols = [r[1] for r in cur.execute(f"pragma table_info({table})").fetchall()]
                text_cols = [c for c in cols if c.lower() in {"content", "value", "message", "text", "summary", "name", "display_name", "notes"}]
                if not text_cols:
                    continue
                q = "select " + ",".join(text_cols) + f" from {table} limit 5000"
                for row in cur.execute(q).fetchall():
                    combined.append(" | ".join(str(x) for x in row if x is not None))
            except Exception:
                continue
        text = "\n".join(combined)
        rel_hits = snippets_for_terms(text, REL_TERMS, max_per_term=12)
        known_hits = snippets_for_terms(text, KNOWN_NAMES, max_per_term=12)
        if rel_hits or known_hits:
            findings[str(path)].extend(rel_hits + known_hits)
        for name in NAME_RE.findall(text):
            if classify_name(name):
                name_counts[name] += 1
    except Exception:
        return


def main():
    findings = defaultdict(list)
    name_counts = Counter()
    scanned = []
    for root in ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or should_skip(path):
                continue
            if path.stat().st_size > 8_000_000:
                continue
            ext = path.suffix.lower()
            if ext in TEXT_EXTS or path.name in {"README", ".env"}:
                scanned.append(str(path))
                scan_text_file(path, findings, name_counts)
            elif ext in DB_EXTS:
                scanned.append(str(path))
                scan_db(path, findings, name_counts)

    # Load explicit contact files if present
    explicit_contacts = []
    for p in [
        Path(r"C:\Users\aztre\Desktop\Andrew-Core-Foundation\data\profiles\default\contacts.json"),
        Path(r"C:\Users\aztre\Desktop\agent\data\profiles\default\contacts.json"),
    ]:
        if p.exists():
            try:
                explicit_contacts.append({"path": str(p), "data": json.loads(p.read_text(errors="ignore"))})
            except Exception as e:
                explicit_contacts.append({"path": str(p), "error": str(e)})

    out_dir = Path(r"C:\Users\aztre\Desktop\Andrew-Core-Foundation\data\contact_recovery")
    out_dir.mkdir(parents=True, exist_ok=True)
    report = out_dir / "contact_recovery_report.md"
    raw = out_dir / "contact_recovery_raw.json"

    raw.write_text(json.dumps({
        "generated_at": datetime.now().isoformat(),
        "roots": [str(r) for r in ROOTS],
        "scanned_count": len(scanned),
        "explicit_contacts": explicit_contacts,
        "top_names": name_counts.most_common(120),
        "findings": {k: v[:40] for k, v in findings.items()},
    }, indent=2), encoding="utf-8")

    lines = []
    lines.append("# Travis Contact / Personal Info Recovery Report")
    lines.append("")
    lines.append(f"Generated: {datetime.now().isoformat()}")
    lines.append("")
    lines.append("## Sources scanned")
    for r in ROOTS:
        lines.append(f"- {r} {'(exists)' if r.exists() else '(missing)'}")
    lines.append(f"- Files/DBs scanned: {len(scanned)}")
    lines.append("")
    lines.append("## Explicit contact stores")
    if explicit_contacts:
        for c in explicit_contacts:
            lines.append(f"### {c['path']}")
            lines.append("```json")
            lines.append(json.dumps(c.get("data", c.get("error")), indent=2)[:4000])
            lines.append("```")
    else:
        lines.append("No explicit contact files found.")
    lines.append("")
    lines.append("## Top candidate proper names")
    for name, count in name_counts.most_common(60):
        lines.append(f"- {name}: {count}")
    lines.append("")
    lines.append("## Relationship/contact snippets")
    for path, hits in sorted(findings.items())[:80]:
        lines.append(f"### {path}")
        for term, snip in hits[:20]:
            lines.append(f"- **{term}**: {snip}")
        lines.append("")
    lines.append("")
    lines.append(f"Raw JSON: `{raw}`")
    report.write_text("\n".join(lines), encoding="utf-8")
    print(str(report))
    print(str(raw))

if __name__ == "__main__":
    main()
