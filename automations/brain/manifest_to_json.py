#!/usr/bin/env python3
"""Convert routing_manifest.toml → routing_manifest.json for Node.js fast-path.

Run once after editing routing_manifest.toml:
    python3 manifest_to_json.py

Outputs routing_manifest.json in the same directory.
"""
import json
import sys
from pathlib import Path

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[no-redef]

HERE = Path(__file__).resolve().parent
TOML_PATH = HERE / "routing_manifest.toml"
JSON_PATH = HERE / "routing_manifest.json"


def main() -> int:
    if not TOML_PATH.exists():
        print(f"ERROR: {TOML_PATH} not found", file=sys.stderr)
        return 1
    with open(TOML_PATH, "rb") as f:
        manifest = tomllib.load(f)
    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    print(f"OK: {JSON_PATH} ({JSON_PATH.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
