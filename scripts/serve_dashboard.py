"""Run the local read-only Kairos dashboard at http://127.0.0.1:8765/accounts."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Keep direct-file execution equivalent to `python -m scripts.serve_dashboard`.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uvicorn

from serve.api import create_app


def main() -> None:
    """Serve the dashboard on localhost."""
    parser = argparse.ArgumentParser(description="Serve the Kairos dashboard on 127.0.0.1.")
    parser.add_argument("--port", type=int, default=8765)
    uvicorn.run(create_app(), host="127.0.0.1", port=parser.parse_args().port)


if __name__ == "__main__":
    main()
