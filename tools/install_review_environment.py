#!/usr/bin/env python3
"""Install a consistent union of the ten pinned connector requirements."""
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
requirements = {}
for source in [*sorted((ROOT / "backend").glob("*/requirements.txt")), ROOT / "requirements-review.txt"]:
    for raw in source.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "==" not in line or line.startswith("-"):
            raise SystemExit(f"Unpinned or non-index dependency in {source.name}")
        name = line.split("==")[0].lower().replace("_", "-")
        if name in requirements and requirements[name] != line:
            raise SystemExit(f"Conflicting dependency pins for {name}")
        requirements[name] = line
with tempfile.TemporaryDirectory(prefix="qull-deps-") as directory:
    path = Path(directory) / "requirements.txt"
    path.write_text("\n".join(sorted(requirements.values())) + "\n")
    subprocess.run([sys.executable, "-m", "pip", "install", "-r", str(path)], check=True)
subprocess.run([sys.executable, "-m", "pip", "check"], check=True)
