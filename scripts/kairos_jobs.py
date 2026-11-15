#!/usr/bin/env python3
"""Entry point for the fail-closed scheduled job adapter."""
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ops.scheduled_jobs import main


if __name__ == "__main__":
    raise SystemExit(main())
