#!/usr/bin/env python3
"""Read-only local deployment inventory and HTTP verification; never prints secrets."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shlex
import urllib.error
import urllib.request
from urllib.parse import urlsplit

SLUGS = ('deposit-recovery', 'eu261-flight-comp', 'subscription-slayer', 'bill-negotiator',
         'final-paycheck', 'class-action-cash', 'unclaimed-property', 'moving-concierge',
         '401k-match', 'medical-bill-fighter')


def env_file(path):
    """Read simple systemd EnvironmentFile entries without shell evaluation."""
    result = {}
    if not path.exists():
        return result
    for number, line in enumerate(path.read_text().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith(('#', ';')):
            continue
        if '=' not in line:
            raise ValueError(f'unsupported environment assignment at line {number}')
        name, value = line.split('=', 1)
        if not re.fullmatch(r'[A-Z_][A-Z_0-9]*', name):
            raise ValueError(f'unsupported environment variable at line {number}')
        words = shlex.split(value, comments=False)
        if len(words) > 1:
            raise ValueError(f'unquoted environment value at line {number}')
        result[name] = words[0] if words else ''
    return result


def key_mode(value, publishable=False):
    if publishable:
        return 'live' if value.startswith('pk_live_') else 'test' if value.startswith('pk_test_') else 'missing_or_unknown'
    if value.startswith(('sk_live_', 'rk_live_')):
        return 'live'
    if value.startswith(('sk_test_', 'rk_test_', 'rkcs_test_')):
        return 'test'
    return 'missing_or_unknown'


def file_summary(path):
    if not path.exists():
        return {'exists': False}
    info = path.stat()
    return {'exists': True, 'bytes': info.st_size, 'mode': oct(info.st_mode & 0o777)}


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def inventory(root, domain, phase):
    checks, errors = {}, []
    if not re.fullmatch(r'[a-z0-9][a-z0-9.-]*[a-z0-9]', domain):
        raise ValueError('domain must be a DNS hostname')
    cert = root / 'etc/letsencrypt/live' / domain
    checks['certificate_files'] = {name: (cert / name).is_file() for name in ('fullchain.pem', 'privkey.pem')}
    if phase == 'activate' and not all(checks['certificate_files'].values()):
        errors.append('TLS certificate/private-key files are missing')
    # Report only conflicting filenames, never nginx configuration contents.
    conflicts = []
    enabled = root / 'etc/nginx/sites-enabled'
    if enabled.exists():
        for path in enabled.iterdir():
            if path.name == 'qull-connectors' or not path.is_file():
                continue
            content = path.read_text(errors='replace')
            if re.search(r'zone=qull_(?:std|money):', content) or re.search(r'\bserver_name\s+[^;]*\b' + re.escape(domain) + r'\b', content):
                conflicts.append(path.name)
    checks['conflicting_enabled_nginx_files'] = conflicts
    if conflicts:
        errors.append('Resolve legacy nginx vhost conflicts before activation: ' + ', '.join(conflicts))
    connectors = {}
    for index, slug in enumerate(SLUGS):
        prefix = f'{slug}: '
        env_path = root / 'etc/connectors' / f'{slug}.env'
        try:
            values = env_file(env_path)
            if phase == 'activate':
                managed = root / 'srv/connectors' / f'.staged-{slug}' / '.managed.env'
            else:
                managed = root / 'etc/connectors' / f'{slug}.managed.env'
            effective = {**values, **env_file(managed)}
        except (OSError, ValueError) as error:
            errors.append(prefix + type(error).__name__ + ' reading environment file')
            effective = values = {}
        mode = values.get('STRIPE_MODE', 'test')
        secret_mode = key_mode(values.get('STRIPE_SECRET_KEY', ''))
        public_mode = key_mode(values.get('STRIPE_PUBLISHABLE_KEY', ''), True)
        old = root / 'srv/connectors' / slug / 'data/app.db'
        new = root / 'var/lib/qull' / slug / 'app.db'
        ledger = root / 'var/lib/qull' / slug / 'billing.sqlite3'
        marker = new.parent / '.legacy-migration.json'
        item = {'stripe_mode': mode, 'secret_key_mode': secret_mode, 'publishable_key_mode': public_mode,
                'legacy_database': file_summary(old), 'durable_database': file_summary(new),
                'payment_ledger': file_summary(ledger), 'legacy_migration_recorded': marker.is_file(),
                'custom_database_override_present': bool(values.get('FINAL_PAYCHECK_DB')),
                'selected_data_directory': effective.get('DATA_DIR', '(application default)'),
                'selected_ledger_path': effective.get('BILLING_LEDGER_PATH', '(application default)')}
        if phase == 'activate':
            if mode not in ('live', 'test') or secret_mode != mode or public_mode != mode:
                errors.append(prefix + 'Stripe mode and server/browser keys are missing or inconsistent')
            if effective.get('ENV') != 'production':
                errors.append(prefix + 'production environment is not selected')
            expected = f'/var/lib/qull/{slug}'
            if effective.get('DATA_DIR') != expected or effective.get('BILLING_LEDGER_PATH') != expected + '/billing.sqlite3':
                errors.append(prefix + 'durable application/payment paths do not match the managed layout')
            if effective.get('PUBLIC_BASE_URL') != f'https://{domain}/{slug}':
                errors.append(prefix + 'public return URL does not match this deployment')
            rest = values.get('REST_PORT', values.get('DEPOSIT_REST_PORT', str(8471 + index)))
            mcp = values.get('MCP_PORT', values.get('DEPOSIT_MCP_PORT', str(8571 + index)))
            if rest != str(8471 + index) or mcp != str(8571 + index):
                errors.append(prefix + 'custom ports do not match generated nginx upstreams')
            custom = values.get('FINAL_PAYCHECK_DB')
            if custom and custom != expected + '/app.db':
                errors.append(prefix + 'custom FINAL_PAYCHECK_DB requires explicit migration first')
            if old.exists() and new.exists() and not marker.exists():
                errors.append(prefix + 'both legacy and durable app databases exist without a migration record; choose the authoritative database before activation')
            if marker.exists() and old.exists():
                try:
                    entry = json.loads(marker.read_text())
                    if entry.get('legacy_sha256') != digest(old) or Path(str(old) + '-wal').exists():
                        errors.append(prefix + 'legacy database changed after recorded migration; reconcile before activation')
                except (OSError, ValueError):
                    errors.append(prefix + 'legacy migration record is invalid')
            for name in ('ALLOW_DEV_IDENTITY', 'FINAL_PAYCHECK_STRIPE_MOCK', 'CLASS_ACTION_CASH_DRY_RUN', 'FINAL_PAYCHECK_TODAY'):
                if values.get(name) not in (None, '', '0'):
                    errors.append(prefix + 'unsafe production flag enabled: ' + name)
        connectors[slug] = item
    checks['connectors'] = connectors
    return {'operation': 'read_only_local_preflight', 'phase': phase, 'domain': domain, 'checks': checks,
            'errors': errors, 'passed': not errors,
            'limits': 'Does not validate secret permissions with Stripe, database contents, or business workflows.'}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def verify(base, key_file=None):
    parsed = urlsplit(base)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path.strip('/'):
        raise ValueError('base must be an HTTPS origin without a path or credentials')
    token = key_file.read_text().strip() if key_file else None
    if token and (not 32 <= len(token) <= 512 or any(char.isspace() for char in token)):
        raise ValueError('invalid key file format')
    opener = urllib.request.build_opener(NoRedirect)
    rows, errors = [], []
    for slug in SLUGS:
        for label, suffix, headers, expected in (
            ('health', '/health', {}, 200), ('ready', '/ready', {}, 200),
            ('missing_key', '/api/__qull_auth_probe__', {}, 401),
            ('spoofed_header', '/api/__qull_auth_probe__', {'X-Platform-User-Id': 'deployment-probe'}, 401),
            ('payment_return', '/billing/return', {}, 200),
            *(([('valid_key', '/api/__qull_auth_probe__', {'Authorization': 'Bearer ' + token}, 404)]) if token else [])):
            request = urllib.request.Request(base.rstrip('/') + '/' + slug + suffix, headers=headers, method='GET')
            try:
                with opener.open(request, timeout=10) as response:
                    status = response.status
                    response.read(4096)
            except urllib.error.HTTPError as response:
                status = response.code
                response.close()
            except (urllib.error.URLError, TimeoutError):
                status = 'connection_failed'
            rows.append({'connector': slug, 'check': label, 'status': status, 'expected': expected})
            if status != expected:
                errors.append(slug + ': ' + label)
    return {'operation': 'read_only_http_verification', 'checks': rows, 'passed': not errors, 'errors': errors,
            'limits': 'No payments or user-data mutations. A 404 on the nonexistent authenticated probe proves middleware accepted the key, not a complete customer workflow.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    local = sub.add_parser('preflight')
    local.add_argument('--root', type=Path, default=Path('/'))
    local.add_argument('--domain', required=True)
    local.add_argument('--phase', choices=('inventory', 'activate'), default='inventory')
    http = sub.add_parser('verify')
    http.add_argument('--base', required=True)
    http.add_argument('--key-file', type=Path)
    args = parser.parse_args()
    result = inventory(args.root, args.domain, args.phase) if args.command == 'preflight' else verify(args.base, args.key_file)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result['passed'] else 1)


if __name__ == '__main__':
    main()
