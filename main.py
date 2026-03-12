"""Entry point: run the assistive agent web dashboard."""
import sys
from pathlib import Path

# Ensure project root is on path
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.web.app import run

if __name__ == "__main__":
    run()
