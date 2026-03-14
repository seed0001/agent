"""
Run the neuron swarm (Option C: hybrid).
Usage: python run_swarm.py "input one" "input two" "input three"
Requires Ollama with llama3.2:latest.
"""
import asyncio
import sys

# Ensure project root on path
from pathlib import Path
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.swarm import run, Signal


async def main():
    inputs = sys.argv[1:4] if len(sys.argv) > 1 else [
        "User asked about the weather.",
        "System context: afternoon, spring.",
    ]
    print("Input signals:", inputs)
    print("Running swarm (Ollama llama3.2:latest)...")
    result = await run(inputs)
    print("\nSwarm output:")
    print(result.content)


if __name__ == "__main__":
    asyncio.run(main())
