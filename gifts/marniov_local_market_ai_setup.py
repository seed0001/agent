#!/usr/bin/env python3
"""
marniov_local_market_ai_setup.py

A small local-market-intelligence starter kit.
Gifted by Andrew, with Travis' approval.

What this does:
- Checks that Python is usable.
- Checks for Ollama.
- Pulls recommended local models for a 16GB RAM / 8GB VRAM system:
    qwen2.5:7b-instruct  -> main local reasoning model
    nomic-embed-text     -> local embeddings/search model
- Creates a project folder: local_market_analyst/
- Creates a virtual environment.
- Installs free Python packages.
- Writes a starter CLI app that gathers basic free market data and asks Ollama to explain it.

Safety note:
This is a research/education tool, not financial advice and not an auto-trader.
Do not let it place trades automatically.
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_DIR = Path.cwd() / "local_market_analyst"
VENV_DIR = PROJECT_DIR / ".venv"
MAIN_MODEL = "qwen2.5:7b-instruct"
EMBED_MODEL = "nomic-embed-text"

REQUIREMENTS = """\
requests>=2.31.0
pandas>=2.0.0
yfinance>=0.2.40
ccxt>=4.0.0
duckdb>=1.0.0
feedparser>=6.0.11
rich>=13.7.0
plotly>=5.22.0
"""

README = f"""# Local Market Analyst

Local/free market research starter kit using Ollama.

Recommended models:

- Main reasoning model: `{MAIN_MODEL}`
- Embedding model: `{EMBED_MODEL}`

This is for research and learning only. It is **not financial advice** and it does **not auto-trade**.

## Run

Windows PowerShell:

```powershell
.\\.venv\\Scripts\\activate
python market_brain.py --watchlist BTC-USD,GC=F,HG=F,EURUSD=X,SPY
```

Linux/macOS:

```bash
source .venv/bin/activate
python market_brain.py --watchlist BTC-USD,GC=F,HG=F,EURUSD=X,SPY
```

## Good starter watchlist

- `BTC-USD` = Bitcoin
- `ETH-USD` = Ethereum
- `GC=F` = Gold futures
- `SI=F` = Silver futures
- `HG=F` = Copper futures
- `CL=F` = Crude oil futures
- `EURUSD=X` = EUR/USD forex
- `JPY=X` = USD/JPY forex
- `SPY` = S&P 500 ETF
- `QQQ` = Nasdaq ETF

## Design

```text
Free data sources -> Python collector -> DuckDB/CSV -> indicators/stats -> Ollama explanation
```

Build it as a research dashboard first. Do not wire it to real trading until you understand risk controls.
"""

MARKET_BRAIN = r'''#!/usr/bin/env python3
"""
market_brain.py

Simple local market research CLI.
Uses free yfinance data + local Ollama reasoning.
No paid API keys. No auto-trading.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd
import yfinance as yf
from rich.console import Console
from rich.table import Table

console = Console()
DB_PATH = Path("market_data.duckdb")
DEFAULT_MODEL = "qwen2.5:7b-instruct"


def fetch_ticker(symbol: str, period: str = "1mo", interval: str = "1d") -> dict:
    data = yf.download(symbol, period=period, interval=interval, progress=False, auto_adjust=True)
    if data.empty:
        return {"symbol": symbol, "error": "No data returned"}

    close = data["Close"].dropna()
    if close.empty:
        return {"symbol": symbol, "error": "No close prices returned"}

    latest = float(close.iloc[-1])
    first = float(close.iloc[0])
    change_pct = ((latest - first) / first) * 100 if first else 0.0
    returns = close.pct_change().dropna()
    volatility = float(returns.std() * (252 ** 0.5) * 100) if len(returns) > 1 else 0.0

    return {
        "symbol": symbol,
        "latest_close": round(latest, 6),
        "period_start_close": round(first, 6),
        "period_change_pct": round(change_pct, 3),
        "annualized_volatility_pct_est": round(volatility, 3),
        "rows": int(len(data)),
        "as_of_utc": datetime.now(timezone.utc).isoformat(),
    }


def save_results(rows: list[dict]) -> None:
    con = duckdb.connect(str(DB_PATH))
    df = pd.DataFrame(rows)
    con.execute("CREATE TABLE IF NOT EXISTS market_snapshots AS SELECT * FROM df WHERE 1=0")
    con.execute("INSERT INTO market_snapshots SELECT * FROM df")
    con.close()


def ask_ollama(model: str, rows: list[dict], question: str) -> str:
    prompt = f"""
You are a cautious local market research analyst.
Do not give financial advice. Do not tell the user to buy or sell.
Explain what changed, what looks unusual, and what risks to investigate.

User question:
{question}

Market snapshot JSON:
{json.dumps(rows, indent=2)}

Return:
1. Short overview
2. Notable movers
3. Risk notes
4. Questions to research next
""".strip()

    cmd = ["ollama", "run", model, prompt]
    try:
        result = subprocess.run(cmd, text=True, capture_output=True, check=False, encoding="utf-8")
    except FileNotFoundError:
        return "Ollama was not found. Install Ollama first: https://ollama.com/download"

    if result.returncode != 0:
        return f"Ollama error:\n{result.stderr.strip()}"
    return result.stdout.strip()


def print_table(rows: list[dict]) -> None:
    table = Table(title="Local Market Snapshot")
    table.add_column("Symbol")
    table.add_column("Latest")
    table.add_column("Change %")
    table.add_column("Vol % est")
    table.add_column("Status")

    for row in rows:
        if "error" in row:
            table.add_row(row["symbol"], "-", "-", "-", row["error"])
        else:
            table.add_row(
                row["symbol"],
                str(row["latest_close"]),
                str(row["period_change_pct"]),
                str(row["annualized_volatility_pct_est"]),
                "ok",
            )
    console.print(table)


def main() -> int:
    parser = argparse.ArgumentParser(description="Local free market analyst using Ollama + yfinance")
    parser.add_argument("--watchlist", default="BTC-USD,GC=F,HG=F,EURUSD=X,SPY", help="Comma-separated yfinance symbols")
    parser.add_argument("--period", default="1mo", help="yfinance period, e.g. 5d, 1mo, 6mo, 1y")
    parser.add_argument("--interval", default="1d", help="yfinance interval, e.g. 1d, 1h")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Ollama model name")
    parser.add_argument("--question", default="What changed in this watchlist and what risks should I research next?")
    parser.add_argument("--no-ollama", action="store_true", help="Only fetch data; skip Ollama explanation")
    args = parser.parse_args()

    symbols = [s.strip() for s in args.watchlist.split(",") if s.strip()]
    rows = [fetch_ticker(symbol, args.period, args.interval) for symbol in symbols]
    save_results(rows)
    print_table(rows)

    if not args.no_ollama:
        console.rule("Ollama Analysis")
        console.print(ask_ollama(args.model, rows, args.question))

    console.print(f"\nSaved snapshot to: {DB_PATH.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


def run(cmd: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    print(f"\n> {' '.join(cmd)}")
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None, check=check)


def python_in_venv() -> Path:
    if platform.system().lower().startswith("win"):
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def check_ollama() -> bool:
    return shutil.which("ollama") is not None


def pull_model(model: str) -> None:
    run(["ollama", "pull", model], check=True)


def write_project_files() -> None:
    PROJECT_DIR.mkdir(parents=True, exist_ok=True)
    (PROJECT_DIR / "requirements.txt").write_text(REQUIREMENTS, encoding="utf-8")
    (PROJECT_DIR / "README.md").write_text(README, encoding="utf-8")
    (PROJECT_DIR / "market_brain.py").write_text(MARKET_BRAIN, encoding="utf-8")


def create_venv_and_install(skip_pip: bool = False) -> None:
    if not VENV_DIR.exists():
        run([sys.executable, "-m", "venv", str(VENV_DIR)], check=True)
    if skip_pip:
        print("Skipping package install because --skip-pip was used.")
        return
    py = python_in_venv()
    run([str(py), "-m", "pip", "install", "--upgrade", "pip"], check=True)
    run([str(py), "-m", "pip", "install", "-r", str(PROJECT_DIR / "requirements.txt")], check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Set up a local Ollama market analyst starter project")
    parser.add_argument("--main-model", default=MAIN_MODEL)
    parser.add_argument("--embed-model", default=EMBED_MODEL)
    parser.add_argument("--skip-model-pull", action="store_true", help="Do not pull Ollama models")
    parser.add_argument("--skip-pip", action="store_true", help="Create files but do not install Python packages")
    args = parser.parse_args()

    print("Local Market Analyst setup")
    print(f"Project folder: {PROJECT_DIR}")
    print(f"Python: {sys.version.split()[0]}")

    if not check_ollama():
        print("\nOllama was not found on PATH.")
        print("Install it first: https://ollama.com/download")
        print("Then rerun this script.")
        return 2

    if not args.skip_model_pull:
        pull_model(args.main_model)
        pull_model(args.embed_model)

    write_project_files()
    create_venv_and_install(skip_pip=args.skip_pip)

    print("\nSetup complete.")
    print("\nNext commands:")
    if platform.system().lower().startswith("win"):
        print(r"  cd local_market_analyst")
        print(r"  .\.venv\Scripts\activate")
    else:
        print("  cd local_market_analyst")
        print("  source .venv/bin/activate")
    print("  python market_brain.py --watchlist BTC-USD,GC=F,HG=F,EURUSD=X,SPY")
    print("\nRemember: research only. No auto-trading.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
