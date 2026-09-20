"""Run services in isolated interpreters and disposable DATA_DIRs.

These tests make no network requests and never use a Stripe account. They cover
actual REST/MCP application entrypoints; payment-provider behavior is covered by
the dedicated shared-billing suite.
"""
import os
from pathlib import Path
import subprocess
import sys
import pytest

ROOT = Path(__file__).resolve().parents[1]
SERVICES = ['deposit-recovery', 'eu261-flight-comp', 'bill-negotiator', 'final-paycheck', 'class-action-cash']


@pytest.mark.parametrize('slug', SERVICES)
def test_workflow_security_billing_and_privacy(slug, tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith(('STRIPE_', 'QULL_', 'FINAL_PAYCHECK_'))}
    env.update(ENV='test', ALLOW_DEV_IDENTITY='1', DATA_DIR=str(tmp_path),
               PYTHONPATH=str(ROOT / 'backend'), TEST_SERVICE=slug,
               FINAL_PAYCHECK_TODAY='2026-09-20')
    result = subprocess.run([sys.executable, str(ROOT / 'tests/workflow_a/scenarios.py')],
                            cwd=ROOT / 'backend' / slug, env=env, capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, (result.stdout + result.stderr)[-4000:]
