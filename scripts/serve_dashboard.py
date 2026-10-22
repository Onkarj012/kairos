"""Run the local read-only Kairos dashboard."""
import sys
from pathlib import Path

import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from serve.api import create_app


if __name__ == "__main__":
    uvicorn.run(create_app(), host="127.0.0.1", port=8765)
