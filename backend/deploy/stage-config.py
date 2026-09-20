#!/usr/bin/env python3
"""Write only a staged nonsecret managed environment, never the live config."""
import argparse
from pathlib import Path
import re


def stage(directory, slug, domain):
    if not re.fullmatch(r'[a-z0-9-]+', slug) or not re.fullmatch(r'[a-z0-9][a-z0-9.-]*[a-z0-9]', domain):
        raise ValueError('Invalid connector slug or domain')
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / '.managed.env'
    path.write_text(f'''ENV=production
HOST=127.0.0.1
DATA_DIR=/var/lib/qull/{slug}
BILLING_LEDGER_PATH=/var/lib/qull/{slug}/billing.sqlite3
QULL_API_KEYS_FILE=/etc/connectors/user-keys.json
PUBLIC_BASE_URL=https://{domain}/{slug}
''')
    path.chmod(0o640)
    return path


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--slug', required=True)
    parser.add_argument('--domain', required=True)
    args = parser.parse_args()
    stage(args.directory, args.slug, args.domain)
