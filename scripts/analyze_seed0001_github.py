#!/usr/bin/env python3
"""
Clone and analyze public GitHub repositories for seed0001.
Outputs visible reports under data/travis_github_profile/.
No private credentials required. Public repos only.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "travis_github_profile"
REPOS_DIR = OUT / "repos"
USER = "seed0001"
API = f"https://api.github.com/users/{USER}/repos?per_page=100&sort=updated"

SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".next", "target", ".pytest_cache"}
SAFE_TEXT_FILES = {
    "README.md", "readme.md", "README.txt", "package.json", "requirements.txt", "pyproject.toml",
    "setup.py", "Cargo.toml", "go.mod", "composer.json", "pom.xml", "Dockerfile", "docker-compose.yml",
    "tsconfig.json", "vite.config.js", "vite.config.ts", "next.config.js", "tailwind.config.js",
}
EXT_LANGUAGE = {
    ".py": "Python", ".js": "JavaScript", ".jsx": "JavaScript/React", ".ts": "TypeScript", ".tsx": "TypeScript/React",
    ".html": "HTML", ".css": "CSS", ".scss": "SCSS", ".json": "JSON", ".md": "Markdown",
    ".yml": "YAML", ".yaml": "YAML", ".toml": "TOML", ".sql": "SQL", ".sh": "Shell", ".ps1": "PowerShell",
    ".cs": "C#", ".java": "Java", ".go": "Go", ".rs": "Rust", ".php": "PHP", ".cpp": "C++", ".c": "C",
}
THEME_KEYWORDS = {
    "ai_companion_memory": ["memory", "soul", "companion", "persona", "andrew", "agent", "llm", "ollama", "openai", "grok"],
    "discord_community": ["discord", "guild", "bot", "channel", "role"],
    "web_app_ui": ["react", "vite", "next", "tailwind", "html", "css", "frontend", "dashboard"],
    "automation_tools": ["tool", "script", "automation", "cli", "workflow", "scheduler"],
    "data_market_research": ["market", "crypto", "stock", "forex", "data", "analysis", "dashboard"],
    "local_models": ["ollama", "local", "model", "gemma", "llama", "qwen"],
}


def run(cmd: list[str], cwd: Path | None = None, timeout: int = 120) -> tuple[int, str, str]:
    p = subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def fetch_repos() -> list[dict[str, Any]]:
    repos = []
    url = API
    while url:
        req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "Andrew-Repo-Analyzer"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            repos.extend(data)
            link = resp.headers.get("Link", "")
        next_url = None
        for part in link.split(","):
            if 'rel="next"' in part:
                m = re.search(r"<([^>]+)>", part)
                if m:
                    next_url = m.group(1)
        url = next_url
    return repos


def clone_or_update(repo: dict[str, Any]) -> dict[str, Any]:
    name = repo["name"]
    url = repo["clone_url"]
    dest = REPOS_DIR / name
    if dest.exists() and (dest / ".git").exists():
        code, out, err = run(["git", "pull", "--ff-only"], cwd=dest, timeout=120)
        action = "updated" if code == 0 else "update_failed"
    else:
        code, out, err = run(["git", "clone", "--depth", "200", url, str(dest)], timeout=240)
        action = "cloned" if code == 0 else "clone_failed"
    return {"name": name, "path": str(dest), "action": action, "returncode": code, "stdout": out[-1000:], "stderr": err[-1000:]}


def file_inventory(path: Path) -> dict[str, Any]:
    file_count = 0
    total_bytes = 0
    ext_counts = Counter()
    lang_counts = Counter()
    safe_files = []
    newest_mtime = 0.0
    oldest_mtime = None
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fn in files:
            fp = Path(root) / fn
            try:
                st = fp.stat()
            except OSError:
                continue
            file_count += 1
            total_bytes += st.st_size
            newest_mtime = max(newest_mtime, st.st_mtime)
            oldest_mtime = st.st_mtime if oldest_mtime is None else min(oldest_mtime, st.st_mtime)
            ext = fp.suffix.lower() or "[no_ext]"
            ext_counts[ext] += 1
            if ext in EXT_LANGUAGE:
                lang_counts[EXT_LANGUAGE[ext]] += 1
            rel = fp.relative_to(path).as_posix()
            if fn in SAFE_TEXT_FILES or rel in SAFE_TEXT_FILES:
                safe_files.append(rel)
    return {
        "file_count": file_count,
        "total_bytes": total_bytes,
        "extensions": dict(ext_counts.most_common(20)),
        "languages_by_file_count": dict(lang_counts.most_common()),
        "safe_files": sorted(set(safe_files))[:50],
        "oldest_file_mtime": datetime.fromtimestamp(oldest_mtime, timezone.utc).isoformat() if oldest_mtime else None,
        "newest_file_mtime": datetime.fromtimestamp(newest_mtime, timezone.utc).isoformat() if newest_mtime else None,
    }


def read_safe_snippets(path: Path, safe_files: list[str]) -> dict[str, str]:
    snippets = {}
    for rel in safe_files[:20]:
        fp = path / rel
        try:
            text = fp.read_text(encoding="utf-8", errors="replace")[:4000]
            snippets[rel] = text
        except Exception:
            pass
    return snippets


def git_info(path: Path) -> dict[str, Any]:
    info: dict[str, Any] = {}
    code, out, err = run(["git", "rev-list", "--count", "HEAD"], cwd=path)
    info["commit_count"] = int(out) if code == 0 and out.isdigit() else 0
    code, out, err = run(["git", "log", "--reverse", "--format=%aI|%s", "--max-count=1"], cwd=path)
    if code == 0 and out:
        parts = out.split("|", 1)
        info["first_commit"] = parts[0]
        info["first_commit_subject"] = parts[1] if len(parts) > 1 else ""
    code, out, err = run(["git", "log", "-1", "--format=%aI|%s"], cwd=path)
    if code == 0 and out:
        parts = out.split("|", 1)
        info["last_commit"] = parts[0]
        info["last_commit_subject"] = parts[1] if len(parts) > 1 else ""
    code, out, err = run(["git", "log", "--format=%aI", "--date=iso"], cwd=path)
    by_month = Counter()
    by_day = Counter()
    if code == 0:
        for line in out.splitlines():
            if len(line) >= 10:
                by_month[line[:7]] += 1
                by_day[line[:10]] += 1
    info["commits_by_month"] = dict(sorted(by_month.items()))
    info["top_commit_days"] = dict(by_day.most_common(10))
    return info


def detect_themes(repo_name: str, description: str | None, snippets: dict[str, str], inv: dict[str, Any]) -> list[str]:
    blob = " ".join([repo_name, description or "", " ".join(snippets.keys()), " ".join(snippets.values())[:8000], json.dumps(inv.get("languages_by_file_count", {}))]).lower()
    themes = []
    for theme, words in THEME_KEYWORDS.items():
        if any(w in blob for w in words):
            themes.append(theme)
    return themes


def summarize_repo(repo: dict[str, Any], analysis: dict[str, Any]) -> str:
    name = repo["name"]
    desc = repo.get("description") or "No GitHub description."
    langs = analysis["inventory"].get("languages_by_file_count", {})
    themes = ", ".join(analysis.get("themes", [])) or "unclear"
    commits = analysis["git"].get("commit_count", 0)
    first = analysis["git"].get("first_commit", repo.get("created_at"))
    last = analysis["git"].get("last_commit", repo.get("updated_at"))
    return f"### {name}\n\n- Description: {desc}\n- Primary GitHub language: {repo.get('language') or 'unknown'}\n- Local language mix: {langs or 'unknown'}\n- Themes: {themes}\n- GitHub updated: {repo.get('updated_at')}\n- First/last commit: {first} → {last}\n- Commit count: {commits}\n- Files: {analysis['inventory'].get('file_count')} | Size: {round(analysis['inventory'].get('total_bytes', 0)/1024, 1)} KB\n"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    REPOS_DIR.mkdir(parents=True, exist_ok=True)
    repos = fetch_repos()
    (OUT / "github_api_repos.json").write_text(json.dumps(repos, indent=2), encoding="utf-8")

    clone_results = [clone_or_update(r) for r in repos if not r.get("fork")]
    (OUT / "clone_results.json").write_text(json.dumps(clone_results, indent=2), encoding="utf-8")

    analyses = []
    for repo in repos:
        if repo.get("fork"):
            continue
        path = REPOS_DIR / repo["name"]
        if not path.exists():
            continue
        inv = file_inventory(path)
        snippets = read_safe_snippets(path, inv["safe_files"])
        gi = git_info(path)
        themes = detect_themes(repo["name"], repo.get("description"), snippets, inv)
        analyses.append({
            "repo": {
                "name": repo["name"], "description": repo.get("description"), "html_url": repo.get("html_url"),
                "created_at": repo.get("created_at"), "updated_at": repo.get("updated_at"), "pushed_at": repo.get("pushed_at"),
                "language": repo.get("language"), "stargazers_count": repo.get("stargazers_count"), "forks_count": repo.get("forks_count"),
                "fork": repo.get("fork"), "archived": repo.get("archived"), "private": repo.get("private"),
            },
            "path": str(path),
            "inventory": inv,
            "git": gi,
            "themes": themes,
            "safe_snippet_files": list(snippets.keys()),
        })

    (OUT / "repos_inventory.json").write_text(json.dumps(analyses, indent=2), encoding="utf-8")

    # Reports
    repo_summaries = "# Repo Summaries — seed0001\n\n" + "\n".join(summarize_repo(a["repo"], a) for a in analyses)
    (OUT / "repo_summaries.md").write_text(repo_summaries, encoding="utf-8")

    lang_total = Counter()
    theme_total = Counter()
    month_total = Counter()
    for a in analyses:
        lang_total.update(a["inventory"].get("languages_by_file_count", {}))
        theme_total.update(a.get("themes", []))
        month_total.update(a["git"].get("commits_by_month", {}))

    lang_report = "# Language / Stack Report — seed0001\n\n"
    lang_report += "## Languages by local file count\n\n" + "\n".join(f"- {k}: {v}" for k, v in lang_total.most_common()) + "\n\n"
    lang_report += "## Project themes\n\n" + "\n".join(f"- {k}: {v}" for k, v in theme_total.most_common()) + "\n"
    (OUT / "language_stack_report.md").write_text(lang_report, encoding="utf-8")

    timeline = "# Timeline Analysis — seed0001\n\n"
    timeline += "## Commits by month across cloned repos\n\n"
    for month, count in sorted(month_total.items()):
        timeline += f"- {month}: {count}\n"
    timeline += "\n## Per-repo activity windows\n\n"
    for a in sorted(analyses, key=lambda x: x["repo"].get("updated_at") or "", reverse=True):
        g = a["git"]
        timeline += f"- **{a['repo']['name']}**: {g.get('first_commit', a['repo'].get('created_at'))} → {g.get('last_commit', a['repo'].get('updated_at'))}; commits={g.get('commit_count',0)}; updated={a['repo'].get('updated_at')}\n"
    (OUT / "timeline_analysis.md").write_text(timeline, encoding="utf-8")

    active = sorted(analyses, key=lambda x: x["repo"].get("updated_at") or "", reverse=True)
    builder = "# Travis Builder Profile from GitHub — seed0001\n\n"
    builder += f"Generated: {datetime.now(timezone.utc).isoformat()}\n\n"
    builder += "## High-level read\n\n"
    builder += "Your GitHub profile shows a builder who prefers making practical systems: tools, local workflows, Discord/community infrastructure, web interfaces, and companion/memory-oriented experiments. The pattern is not just isolated scripts; it is repeated attempts to turn ideas into usable frameworks with onboarding and support around them.\n\n"
    builder += "## Strong signals\n\n"
    for theme, count in theme_total.most_common():
        builder += f"- {theme.replace('_', ' ')}: seen across {count} repo(s)\n"
    builder += "\n## Stack instincts\n\n"
    for lang, count in lang_total.most_common(10):
        builder += f"- {lang}: {count} files\n"
    builder += "\n## Work rhythm\n\n"
    if month_total:
        busiest = month_total.most_common(5)
        builder += "Busiest commit months found locally:\n" + "\n".join(f"- {m}: {c} commits" for m, c in busiest) + "\n\n"
    builder += "## Current/recent focus candidates\n\n"
    for a in active[:8]:
        builder += f"- **{a['repo']['name']}** — updated {a['repo'].get('updated_at')}; themes: {', '.join(a.get('themes', [])) or 'unclear'}\n"
    builder += "\n## Profile draft\n\n"
    builder += "Travis appears to build by exploration: start with a strong concept, create a working prototype, wire it into real tools or communities, then iterate through use. He seems drawn to systems that feel alive or operational rather than static: bots, companions, dashboards, local automation, memory, and support infrastructure. His builder identity is less 'polished product manager first' and more 'working prototype that becomes real through interaction.'\n"
    (OUT / "builder_profile.md").write_text(builder, encoding="utf-8")

    revival = "# Revival Candidates — seed0001\n\n"
    for a in active:
        commits = a["git"].get("commit_count", 0)
        stars = a["repo"].get("stargazers_count", 0)
        themes = a.get("themes", [])
        score = commits + stars * 5 + len(themes) * 3
        if score >= 3:
            revival += f"## {a['repo']['name']}\n\n- URL: {a['repo'].get('html_url')}\n- Updated: {a['repo'].get('updated_at')}\n- Commits: {commits}\n- Themes: {', '.join(themes) or 'unclear'}\n- Why revisit: has enough structure/activity to mine for reusable ideas, docs, or integration into the Andrew ecosystem.\n\n"
    (OUT / "revival_candidates.md").write_text(revival, encoding="utf-8")

    print(f"Fetched repos: {len(repos)}")
    print(f"Cloned/updated non-forks: {len(clone_results)}")
    print(f"Analyzed: {len(analyses)}")
    print(f"Output: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
