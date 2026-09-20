"""Isolated service regressions. All Stripe I/O is replaced; no external accounts used."""
import os
from pathlib import Path
import subprocess
import sys
import pytest

BACKEND = Path(__file__).resolve().parents[1]
SERVICES = ['subscription-slayer','unclaimed-property','moving-concierge','401k-match','medical-bill-fighter']

@pytest.mark.parametrize('slug', SERVICES)
def test_customer_workflow_isolation_and_billing(slug, tmp_path):
    result = subprocess.run([sys.executable, str(BACKEND/'tests'/'workflow_b_scenarios.py'), slug], cwd=BACKEND/slug,
                            env={**os.environ, 'PYTHONPATH': str(BACKEND)+os.pathsep+str(BACKEND/slug), 'ENV':'test',
                                 'ALLOW_DEV_IDENTITY':'1', 'DATA_DIR':str(tmp_path), 'PUBLIC_BASE_URL':'https://example.test/'+slug},
                            text=True, capture_output=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
