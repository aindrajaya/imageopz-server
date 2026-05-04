#!/usr/bin/env python3
"""
Generate OpenAPI spec from the FastAPI app without running the server.

Outputs:
  - docs/openapi.json
"""

from __future__ import annotations

import json
from pathlib import Path
import sys


def main() -> None:
    # Ensure repo root is on sys.path when running as a script
    repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_root))

    from app.main import app

    schema = app.openapi()
    out_path = Path("docs") / "openapi.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(schema, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
