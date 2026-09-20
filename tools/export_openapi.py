#!/usr/bin/env python3
"""Export all ten contracts from isolated copies of the actual applications.

No server, network requests, live secrets or production data are used. Install
backend requirements first. Output is deterministic for PUBLIC_BASE_URL defaults.
"""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SLUGS = (
    "deposit-recovery", "eu261-flight-comp", "subscription-slayer", "bill-negotiator",
    "final-paycheck", "class-action-cash", "unclaimed-property", "moving-concierge",
    "401k-match", "medical-bill-fighter",
)


def export(slug):
    with tempfile.TemporaryDirectory(prefix="qull-schema-") as temp:
        work = Path(temp)
        for name in (slug, "life-events", "api_support", "payment_support"):
            source = ROOT / "backend" / name
            if source.exists():
                shutil.copytree(source, work / name, ignore=shutil.ignore_patterns(
                    "*.db", "*.db-*", "*.sqlite*", "__pycache__", ".venv", ".env", "*.pem"))
        env = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "LANG") if key in os.environ}
        env.update(ENV="test", PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(work),
                   PUBLIC_BASE_URL=f"https://5.78.152.6.nip.io/{slug}")
        code = (
            "import json, asyncio; from pathlib import Path; from app import app; "
            "from mcp_server import server; "
            "Path('contract.json').write_text(json.dumps(app.openapi(), indent=2, ensure_ascii=False)+'\\n'); "
            "Path('tools.json').write_text(json.dumps([t.model_dump(by_alias=True, exclude_none=True) "
            "for t in asyncio.run(server.list_tools())]))"
        )
        result = subprocess.run([sys.executable, "-c", code], cwd=work / slug, env=env,
                                capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise RuntimeError(f"{slug} schema export failed:\n{result.stderr[-6000:]}")
        contract = json.loads((work / slug / "contract.json").read_text())
        from openapi_spec_validator import validate
        validate(contract)
        from jsonschema import Draft202012Validator
        for item in contract["paths"].values():
            for operation in item.values():
                if not isinstance(operation, dict):
                    continue
                for response in operation.get("responses", {}).values():
                    for media in response.get("content", {}).values():
                        examples = ([media["example"]] if "example" in media else [])
                        examples += [item["value"] for item in media.get("examples", {}).values()
                                     if isinstance(item, dict) and "value" in item]
                        if "$ref" not in media.get("schema", {}):
                            for example in examples:
                                Draft202012Validator(media.get("schema", {})).validate(example)
        target = ROOT / "openapi" / f"{slug}.json"
        target.write_text(json.dumps(contract, indent=2, ensure_ascii=False) + "\n")
        tools = json.loads((work / slug / "tools.json").read_text())
        manifest_path = ROOT / "backend" / slug / "connector" / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["version"] = contract["info"]["version"]
        manifest["tools"] = [
            {"name": tool["name"], "description": tool.get("description", ""),
             "input_schema": tool["inputSchema"],
             **({"output_schema": tool["outputSchema"]} if "outputSchema" in tool else {})}
            for tool in tools
        ]
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
        print(f"{slug}: {len(contract['paths'])} paths validated; {len(tools)} MCP tools exported")


if __name__ == "__main__":
    for slug in SLUGS:
        export(slug)
