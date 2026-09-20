#!/usr/bin/env python3
"""Authenticated acceptance checks for the ten deployed Qull connectors.

Usage:
  python tools/verify_deployment.py --base-url https://api.example.com \
      --key-a-file /secure/review-a.key --key-b-file /secure/review-b.key \
      --report /secure/acceptance-report.json

Keys are read from files, never command-line values or logs. Use two NEW,
DISPOSABLE review identities: the test creates clearly synthetic operational
records and verifies that identity B cannot read identity A's new records.
It never calls Stripe setup/payment endpoints, sends letters, files claims,
contacts providers, or deletes records. Created IDs are recorded for later,
explicitly authorized operator cleanup. No owner-wide deletion is implemented.

The Class Action Cash scan uses an active settlement not already present in
identity A's account, so it never merges synthetic evidence into an existing
match. If none exists, that workflow is reported BLOCKED, not passed.

Exit codes: 0 all checks passed; 1 a check failed; 2 checks were blocked or
configuration was invalid. TLS verification stays enabled; redirects are never
followed, preventing a redirect from receiving the Authorization header.
Only Python's standard library is required.
"""
from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import ssl
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener, HTTPSHandler
import uuid

SERVICES = (
    'deposit-recovery', 'eu261-flight-comp', 'subscription-slayer', 'bill-negotiator',
    'final-paycheck', 'class-action-cash', 'unclaimed-property', 'moving-concierge',
    '401k-match', 'medical-bill-fighter',
)
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
SAFE_ID = re.compile(r'^[A-Za-z0-9_-]{1,128}$')
# Explicit POST allowlist: no billing, outcome, cancellation-confirmation,
# recovery-confirmation, payment, provider action, or deletion operation.
SAFE_POSTS = (
    re.compile(r'^/api/(cases|claims|moves|plans|subscriptions|searches)$'),
    re.compile(r'^/api/scan$'),
    re.compile(r'^/api/cases/[A-Za-z0-9_-]+/demand-letter$'),
    re.compile(r'^/api/claims/[A-Za-z0-9_-]+/claim-pack$'),
)


class CheckFailure(Exception):
    """Contains only a safe operational message, never a response body or key."""


class Blocked(Exception):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass
class Reply:
    status: int
    headers: dict[str, str]
    body: bytes

    def json(self) -> dict:
        try:
            value = json.loads(self.body)
        except (ValueError, UnicodeError):
            raise CheckFailure('Response was not valid JSON') from None
        if not isinstance(value, dict):
            raise CheckFailure('Expected a JSON object')
        return value


def load_key(path: Path) -> str:
    """Read a bounded opaque key file without exposing its contents in errors."""
    try:
        if path.stat().st_size > 2048:
            raise CheckFailure('Key file is too large')
        key = path.read_text(encoding='utf-8').strip()
    except (OSError, UnicodeError):
        raise CheckFailure('Could not read an API key file') from None
    if not 32 <= len(key) <= 512 or any(c.isspace() for c in key):
        raise CheckFailure('API key file must contain one opaque key, with no spaces')
    return key


def validate_base_url(value: str) -> str:
    value = value.rstrip('/')
    parsed = urlsplit(value)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment):
        raise CheckFailure('Base URL must be HTTPS, without credentials, query, or fragment')
    return value


def validate_operation(method: str, path: str, payload: Any) -> None:
    if not path.startswith('/') or path.startswith('//') or '..' in path or '\\' in path:
        raise CheckFailure('Unsafe relative request path')
    if method == 'GET':
        return
    if method != 'POST':
        raise CheckFailure('This acceptance tool never updates or deletes existing records')
    if path == '/mcp':
        if not isinstance(payload, dict) or payload.get('method') not in (
                'initialize', 'notifications/initialized', 'tools/list'):
            raise CheckFailure('Only MCP initialization and tool discovery are allowed')
        return
    if not any(pattern.fullmatch(path) for pattern in SAFE_POSTS):
        raise CheckFailure('POST operation is outside the non-payment acceptance allowlist')
    if path == '/api/scan' and (not isinstance(payload, dict) or payload.get('source') != 'receipts'):
        raise CheckFailure('Only explicitly supplied synthetic receipt scanning is allowed')


class Verifier:
    def __init__(self, base_url: str, key_a: str, key_b: str, timeout: float = 20):
        self.base_url = validate_base_url(base_url)
        if key_a == key_b:
            raise CheckFailure('Two different API keys for distinct review identities are required')
        self._keys = {'a': key_a, 'b': key_b}
        self.timeout = timeout
        self.opener = build_opener(NoRedirect(), HTTPSHandler(context=ssl.create_default_context()))
        self.run_id = uuid.uuid4().hex[:12]
        self.records: list[dict] = []
        self.report_path: Path | None = None
        self.checks: list[dict] = []
        self.today = date.today()
        self.name = f'SYNTHETIC Qull review {self.run_id}'
        self.email = f'qull-review-{self.run_id}@example.com'
        self.address = f'SYNTHETIC verification address {self.run_id}; not for mailing'

    def request(self, service: str, method: str, path: str, payload=None,
                identity: str | None = 'a', extra_headers: dict | None = None) -> Reply:
        if service not in SERVICES:
            raise CheckFailure('Unknown connector')
        validate_operation(method, path, payload)
        headers = {'Accept': 'application/json', 'User-Agent': 'Qull-Deployment-Acceptance/1.0'}
        if identity:
            headers['Authorization'] = 'Bearer ' + self._keys[identity]
        if extra_headers:
            if any(key.lower() == 'authorization' for key in extra_headers):
                raise CheckFailure('Authorization cannot be overridden')
            headers.update(extra_headers)
        data = json.dumps(payload, allow_nan=False).encode() if payload is not None else None
        if data is not None:
            headers['Content-Type'] = 'application/json'
        url = f'{self.base_url}/{service}{path}'
        request = Request(url, data=data, headers=headers, method=method)
        try:
            try:
                response = self.opener.open(request, timeout=self.timeout)
            except HTTPError as error:
                response = error
            with response:
                body = response.read(MAX_RESPONSE_BYTES + 1)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise CheckFailure('Response exceeded the bounded download size')
                return Reply(response.status, {k.lower(): v for k, v in response.headers.items()}, body)
        except (URLError, TimeoutError, OSError):
            raise CheckFailure('Network or TLS request failed; no response content was logged') from None

    def expect(self, reply: Reply, expected=(200,)) -> Reply:
        if reply.status not in expected:
            raise CheckFailure(f'Unexpected HTTP {reply.status}; expected ' + '/'.join(map(str, expected)))
        return reply

    def check(self, service: str, name: str, action):
        try:
            details = action() or {}
            record = {'service': service, 'check': name, 'status': 'PASS', **details}
        except Blocked as exc:
            record = {'service': service, 'check': name, 'status': 'BLOCKED', 'reason': str(exc)}
        except CheckFailure as exc:
            record = {'service': service, 'check': name, 'status': 'FAIL', 'reason': str(exc)}
        except Exception:
            # Do not print arbitrary exception values, response bodies or headers.
            record = {'service': service, 'check': name, 'status': 'FAIL', 'reason': 'Unexpected response shape or local verification error'}
        self.checks.append(record)
        self.checkpoint()
        print(f"{record['status']:7} {service}: {name}" + (f" — {record['reason']}" if 'reason' in record else ''), flush=True)

    def liveness(self, service):
        data = self.expect(self.request(service, 'GET', '/health', identity=None)).json()
        if (data.get('ok') is not True and data.get('status') != 'ok') or data.get('service') != service:
            raise CheckFailure('Health response did not identify the expected running service')

    def readiness(self, service):
        data = self.expect(self.request(service, 'GET', '/ready', identity=None)).json()
        if data.get('status') != 'ready':
            raise CheckFailure('Configuration readiness did not report ready')
        return {'scope': 'Configuration readiness only; payment processing was not tested'}

    def anonymous(self, service):
        self.expect(self.request(service, 'GET', '/api/acceptance-anonymous-check', identity=None), (401,))

    def mcp(self, service):
        accept = {'Accept': 'application/json, text/event-stream'}
        initialize = {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
            'protocolVersion': '2025-03-26', 'capabilities': {},
            'clientInfo': {'name': 'qull-deployment-acceptance', 'version': '1.0'}}}
        self.expect(self.request(service, 'POST', '/mcp', initialize, None, accept), (401,))
        first = self.rpc(self.expect(self.request(service, 'POST', '/mcp', initialize, 'a', accept)))
        protocol = first.get('result', {}).get('protocolVersion')
        if not isinstance(protocol, str):
            raise CheckFailure('MCP initialization did not negotiate a protocol')
        headers = {**accept, 'MCP-Protocol-Version': protocol}
        # Stateless transport reauthenticates every request; no session reuse.
        listed = self.rpc(self.expect(self.request(service, 'POST', '/mcp',
            {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list', 'params': {}}, 'a', headers)))
        tools = listed.get('result', {}).get('tools')
        if not isinstance(tools, list) or not tools:
            raise CheckFailure('MCP returned no discoverable tools')
        if not all(isinstance(t, dict) and isinstance(t.get('name'), str) for t in tools):
            raise CheckFailure('MCP tool descriptions were malformed')
        return {'tools_discovered': len(tools), 'tools_invoked': 0}

    @staticmethod
    def rpc(reply):
        if 'text/event-stream' not in reply.headers.get('content-type', ''):
            data = reply.json()
        else:
            data = None
            try:
                for line in reply.body.decode().splitlines():
                    if line.startswith('data:'):
                        candidate = json.loads(line[5:].strip())
                        if isinstance(candidate, dict) and 'id' in candidate:
                            data = candidate
            except (ValueError, UnicodeError):
                raise CheckFailure('MCP event response could not be decoded') from None
            if data is None:
                raise CheckFailure('MCP response contained no result')
        if 'error' in data:
            raise CheckFailure('MCP returned a protocol error')
        return data

    def created(self, service, method_path, payload, id_key, resource_path, expected=(200, 201)):
        value = self.expect(self.request(service, 'POST', method_path, payload), expected).json()
        item = value
        for part in id_key.split('.'):
            item = item[part]
        ident = str(item)
        if not SAFE_ID.fullmatch(ident):
            raise CheckFailure('Created record identifier had an unexpected format')
        record_path = resource_path.format(id=ident)
        # Record immediately, so failures after creation still leave a cleanup manifest.
        self.record_fixture({'service': service, 'identity': 'a', 'record_id': ident,
                             'resource_path': record_path, 'run_id': self.run_id})
        self.expect(self.request(service, 'GET', record_path, identity='a'))
        self.expect(self.request(service, 'GET', record_path, identity='b'), (404,))
        self.expect(self.request(service, 'GET', record_path, identity=None), (401,))
        return ident, value

    @staticmethod
    def pdf_bytes(reply: Reply):
        if not reply.body.startswith(b'%PDF-') or 'application/pdf' not in reply.headers.get('content-type', ''):
            raise CheckFailure('Deliverable did not contain an application/pdf document')
        return {'pdf_bytes': len(reply.body), 'pdf_sha256': hashlib.sha256(reply.body).hexdigest()}

    @staticmethod
    def pdf_json(body):
        try:
            pdf = base64.b64decode(body['pdf_base64'], validate=True)
        except (KeyError, ValueError):
            raise CheckFailure('JSON deliverable did not contain a valid PDF encoding') from None
        if not pdf.startswith(b'%PDF-'):
            raise CheckFailure('Decoded document was not a PDF')
        return {'pdf_bytes': len(pdf), 'pdf_sha256': hashlib.sha256(pdf).hexdigest()}

    def workflow(self, service):
        old = (self.today - timedelta(days=70)).isoformat()
        if service == 'deposit-recovery':
            ident, _ = self.created(service, '/api/cases', {
                'tenant_name': self.name, 'tenant_forwarding_address': self.address, 'state': 'CA',
                'move_out': old, 'deposit': 1000, 'landlord_name': 'SYNTHETIC landlord',
                'landlord_address': self.address, 'rental_address': self.address}, 'case_id', '/api/cases/{id}')
            return self.pdf_json(self.expect(self.request(service, 'POST', f'/api/cases/{ident}/demand-letter')).json())
        if service == 'eu261-flight-comp':
            ident, result = self.created(service, '/api/claims', {
                'passenger_name': self.name, 'passenger_email': self.email, 'flight_number': 'QV123',
                'flight_date': old, 'airline': 'SYNTHETIC carrier', 'airline_eu_licensed': True,
                'from_iata': 'FRA', 'to_iata': 'CDG', 'disruption': 'delay', 'arrival_delay_h': 4},
                'claim_id', '/api/claims/{id}')
            if result.get('eligible') is not True:
                raise CheckFailure('Synthetic qualifying flight was not eligible')
            generated = self.expect(self.request(service, 'POST', f'/api/claims/{ident}/claim-pack')).json()
            info = self.pdf_json(generated)
            download = f'/api/claims/{ident}/claim-pack.pdf'
            self.pdf_bytes(self.expect(self.request(service, 'GET', download)))
            self.expect(self.request(service, 'GET', download, identity='b'), (404,))
            return info
        if service == 'subscription-slayer':
            ident, _ = self.created(service, '/api/subscriptions', {
                'merchant': self.name, 'amount': 9.99, 'currency': 'USD', 'frequency': 'monthly'},
                'subscription.id', '/api/subscriptions/{id}/cancel-pack')
            result = self.expect(self.request(service, 'GET', f'/api/subscriptions/{ident}/cancel-pack')).json()
            if not result.get('cancel_pack', {}).get('steps'):
                raise CheckFailure('Cancellation guide contained no steps')
            return {'cancellation_performed': False}
        if service == 'bill-negotiator':
            ident, _ = self.created(service, '/api/cases', {
                'provider': 'SYNTHETIC provider', 'service_type': 'internet', 'current_monthly_bill': 100,
                'user_name': self.name}, 'case_id', '/api/cases/{id}')
            result = self.expect(self.request(service, 'GET', f'/api/cases/{ident}/script')).json()
            if not result.get('script'):
                raise CheckFailure('Bill negotiation guide was empty')
            return {'provider_contacted': False}
        if service == 'final-paycheck':
            ident, _ = self.created(service, '/api/cases', {
                'employee_name': self.name, 'employee_email': self.email, 'employer_name': 'SYNTHETIC employer',
                'employer_address': self.address, 'state': 'CA', 'last_day_worked': old,
                'termination_type': 'fired', 'wages_owed': 1000, 'forwarding_address': self.address},
                'id', '/api/cases/{id}')
            return self.pdf_bytes(self.expect(self.request(service, 'POST', f'/api/cases/{ident}/demand-letter')))
        if service == 'class-action-cash':
            return self.class_action()
        if service == 'unclaimed-property':
            ident, _ = self.created(service, '/api/searches', {
                'full_legal_name': self.name, 'email': self.email,
                'states_of_residence': [{'abbr': 'CA', 'years': '2020-2025'}]}, 'search_id', '/api/searches/{id}')
            pack = self.expect(self.request(service, 'GET', f'/api/searches/{ident}/claim-pack?state=CA')).json()
            if not pack.get('steps') or not pack.get('portal_url', '').startswith('https://'):
                raise CheckFailure('Free claim guide lacked filing steps or HTTPS directory link')
            status = self.expect(self.request(service, 'GET', f'/api/searches/{ident}/billing/status')).json()
            if status.get('billing_status') != 'disabled':
                raise CheckFailure('FoundMoney paid assistance was unexpectedly enabled')
            return {'paid_assistance': 'disabled', 'claim_filed': False}
        if service == 'moving-concierge':
            ident, _ = self.created(service, '/api/moves', {
                'name': self.name, 'email': self.email, 'old_address': self.address,
                'new_address': self.address, 'move_date': (self.today + timedelta(days=30)).isoformat(), 'state': 'CA'},
                'move_id', '/api/moves/{id}')
            summary = self.expect(self.request(service, 'GET', f'/api/moves/{ident}')).json()
            if summary.get('pack_locked') is not True or summary.get('items') != []:
                raise CheckFailure('Unpaid moving pack was not locked')
            self.expect(self.request(service, 'GET', f'/api/moves/{ident}/pack'), (402,))
            return {'paid_pack_tested': False, 'payment_boundary': 'locked as expected'}
        if service == '401k-match':
            ident, result = self.created(service, '/api/plans', {
                'name': self.name, 'salary': 120000, 'pay_frequency': 'biweekly',
                'current_contrib_pct': 4, 'match_pct': 50, 'match_cap_pct': 6}, 'plan_id', '/api/plans/{id}')
            if result.get('uncaptured_match_annual') != 1200:
                raise CheckFailure('Known educational example produced unexpected match math')
            self.expect(self.request(service, 'GET', f'/api/plans/{ident}/pack'), (402,))
            return {'paid_pack_tested': False, 'payment_boundary': 'locked as expected'}
        if service == 'medical-bill-fighter':
            ident, result = self.created(service, '/api/cases', {
                'patient_name': self.name, 'patient_email': self.email, 'provider_name': 'SYNTHETIC clinic',
                'bill_date': old, 'billed_patient_responsibility': 500,
                'line_items': [{'code': '99213', 'description': 'Synthetic example line', 'amount': 250},
                               {'code': '99213', 'description': 'Synthetic example line', 'amount': 250}]},
                'case_id', '/api/cases/{id}')
            if not result.get('findings'):
                raise CheckFailure('Synthetic duplicate-line example produced no review flags')
            path = f'/api/cases/{ident}/pack?type=itemized'
            info = self.pdf_bytes(self.expect(self.request(service, 'GET', path)))
            self.expect(self.request(service, 'GET', path, identity='b'), (404,))
            return info
        raise CheckFailure('No workflow for connector')

    def class_action(self):
        service = 'class-action-cash'
        catalog = self.expect(self.request(service, 'GET', '/api/settlements')).json().get('settlements', [])
        existing = self.expect(self.request(service, 'GET', '/api/matches')).json().get('matches', [])
        names = {row.get('settlement_name') for row in existing}
        receipt = None
        for opportunity in catalog:
            if opportunity.get('name') in names:
                continue
            for keyword in opportunity.get('match_keywords', []):
                candidate = {'id': 'synthetic-' + self.run_id, 'sender': self.email,
                    'subject': 'SYNTHETIC ACCEPTANCE TEST, not a purchase or claim', 'date': self.today.isoformat(),
                    'snippet': f'SYNTHETIC acceptance test keyword: {keyword}. This is not evidence of eligibility or a real purchase.'}
                text = ' '.join(candidate[field] for field in ('sender', 'subject', 'snippet')).casefold()
                # Matching uses substring keywords. Refuse a candidate that could
                # merge this synthetic receipt into any existing settlement match.
                touches_existing = any(row.get('name') in names and any(
                    str(term).casefold() in text for term in row.get('match_keywords', [])) for row in catalog)
                if not touches_existing:
                    receipt = candidate
                    break
            if receipt:
                break
        if receipt is None:
            raise Blocked('No active verified settlement unused by this identity; no existing match was modified')
        result = self.expect(self.request(service, 'POST', '/api/scan', {
            'source': 'receipts', 'receipts': [receipt]})).json()
        new_matches = [row for row in result.get('matches', []) if row.get('settlement_name') not in names]
        if not new_matches:
            raise CheckFailure('Synthetic receipt scan returned no new candidate')
        for match in new_matches:
            ident = str(match.get('id', ''))
            if not SAFE_ID.fullmatch(ident):
                raise CheckFailure('Created match identifier was malformed')
            self.record_fixture({'service': service, 'identity': 'a', 'record_id': ident,
                                 'resource_path': f'/api/matches/{ident}/billing/status', 'run_id': self.run_id})
            path = f'/api/matches/{ident}/billing/status'
            self.expect(self.request(service, 'GET', path))
            self.expect(self.request(service, 'GET', path, identity='b'), (404,))
            self.expect(self.request(service, 'GET', path, identity=None), (401,))
        return {'new_candidates': len(new_matches), 'eligibility_attested': False, 'claim_pack_requested': False, 'claim_filed': False}

    def record_fixture(self, record):
        self.records.append(record)
        self.checkpoint()

    def checkpoint(self):
        if self.report_path:
            with self.report_path.open('w', encoding='utf-8') as handle:
                json.dump({'run_id': self.run_id, 'in_progress': True,
                           'checks': self.checks, 'created_fixtures': self.records,
                           'cleanup': 'Not performed; explicit operator authorization is required.'}, handle, indent=2)
                handle.write('\n')

    def run(self, services):
        for service in services:
            self.check(service, 'public health', lambda s=service: self.liveness(s))
            self.check(service, 'configuration readiness', lambda s=service: self.readiness(s))
            self.check(service, 'anonymous REST denied', lambda s=service: self.anonymous(s))
            self.check(service, 'MCP authenticated discovery', lambda s=service: self.mcp(s))
            self.check(service, 'synthetic workflow, owner isolation, deliverable', lambda s=service: self.workflow(s))
        statuses = [c['status'] for c in self.checks]
        return {'run_id': self.run_id, 'finished_at': datetime.now(timezone.utc).isoformat(),
                'passed': statuses.count('PASS'), 'failed': statuses.count('FAIL'), 'blocked': statuses.count('BLOCKED'),
                'checks': self.checks, 'created_fixtures': self.records,
                'scope': 'Authenticated operational checks only. No payment methods, fees, external submissions, provider contact or data deletion.',
                'cleanup': 'Not performed. Fixture IDs identify only records created by this run; operator authorization is required for later cleanup.'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--base-url', required=True, help='HTTPS origin/prefix before /<connector-slug>')
    parser.add_argument('--key-a-file', type=Path, required=True, help='Private file containing the first disposable review API key')
    parser.add_argument('--key-b-file', type=Path, required=True, help='Private file containing the second, distinct-identity review API key')
    parser.add_argument('--report', type=Path, help='Optional JSON report path; contains synthetic fixture IDs but no keys or response bodies')
    parser.add_argument('--service', action='append', choices=SERVICES, help='Run only this connector; repeat as needed; default all ten')
    parser.add_argument('--timeout', type=float, default=20, help='Per-request timeout in seconds (1–60)')
    args = parser.parse_args(argv)
    try:
        if not 1 <= args.timeout <= 60:
            raise CheckFailure('Timeout must be between 1 and 60 seconds')
        verifier = Verifier(args.base_url, load_key(args.key_a_file), load_key(args.key_b_file), args.timeout)
        if args.report:
            # Reserve a new restricted report before creating any remote records.
            target = args.report.resolve()
            if target in (args.key_a_file.resolve(), args.key_b_file.resolve()):
                raise CheckFailure('Report path must not replace a key file')
            target.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
            verifier.report_path = target
            verifier.checkpoint()
        result = verifier.run(tuple(dict.fromkeys(args.service or SERVICES)))
        if verifier.report_path:
            with verifier.report_path.open('w', encoding='utf-8') as handle:
                json.dump(result, handle, indent=2)
                handle.write('\n')
        print(f"Completed: {result['passed']} passed, {result['failed']} failed, {result['blocked']} blocked; {len(result['created_fixtures'])} synthetic records created.")
        if not args.report:
            print(json.dumps({'run_id': result['run_id'], 'created_fixtures': result['created_fixtures']}, indent=2))
        return 1 if result['failed'] else 2 if result['blocked'] else 0
    except CheckFailure as exc:
        print('Configuration error: ' + str(exc), file=sys.stderr)
        return 2
    except OSError:
        print('Could not save the acceptance report; no credential or response content was logged.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
