#!/usr/bin/env python3
"""Check this kit's routing and auth declarations without making API requests.

Exit 1 means contract blockers remain. This does not test the backend, payments,
legal compliance, or Meta acceptance. It never sends credentials or payments.
"""

import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit

METHODS = {"get", "post", "put", "patch", "delete", "options", "head", "trace"}


def inspect(path):
    spec = json.loads(path.read_text())
    schemes = spec.get("components", {}).get("securitySchemes", {})
    issues = []
    operations = 0
    for route, item in spec.get("paths", {}).items():
        for method, operation in item.items():
            if method not in METHODS:
                continue
            operations += 1
            servers = operation.get("servers", item.get("servers", spec.get("servers", [])))
            if not servers:
                issues.append(f"{method.upper()} {route}: no explicit server")
            for server in servers:
                full_path = urlsplit(server["url"].rstrip("/") + route).path
                if "/api/api/" in full_path:
                    issues.append(f"{method.upper()} {route}: duplicate /api prefix")
            if route == "/health":
                continue
            security = operation.get("security", spec.get("security", []))
            if not security or any(not requirement for requirement in security):
                issues.append(f"{method.upper()} {route}: authentication is unspecified or optional")
            for requirement in security:
                for name in requirement:
                    if name not in schemes:
                        issues.append(f"{method.upper()} {route}: undefined security scheme {name}")
    if not operations:
        issues.append("No operations found")
    return {"file": path.name, "operations": operations, "issues": issues}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec-dir", type=Path, default=Path(__file__).resolve().parents[1] / "openapi")
    args = parser.parse_args()
    files = sorted(args.spec_dir.glob("*.json"))
    if not files:
        parser.error("No JSON specifications found")
    results = [inspect(path) for path in files]
    print(json.dumps({"scope": "Static contract checks only; no network or payment requests", "results": results}, indent=2))
    return int(any(result["issues"] for result in results))


if __name__ == "__main__":
    raise SystemExit(main())
