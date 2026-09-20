"""Error detection rules for medical bills/EOBs.

Every rule returns a list of findings shaped as:
  {rule, severity, line_refs, explanation, suggested_action}

SEVERITY is one of: "error" | "warning" | "info".

Hard rule: findings never assert legal conclusions. Balance-billing language
uses "may be protected — verify with the provider/insurer or a licensed
attorney" phrasing. Upcoding/unbundling pairs are explicitly labeled
"heuristic". Findings are data derived from the intake; no user input is ever
executed or used to alter control flow beyond matching.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import CaseIntake

_CODE_PAIRS_PATH = Path(__file__).with_name("code_pairs.json")


def _load_code_pairs() -> list[dict[str, Any]]:
    try:
        data = json.loads(_CODE_PAIRS_PATH.read_text(encoding="utf-8"))
        return data.get("pairs", [])
    except (OSError, ValueError):
        return []


_CODE_PAIRS = _load_code_pairs()


def _finding(rule: str, severity: str, line_refs: list[int],
             explanation: str, suggested_action: str) -> dict[str, Any]:
    return {
        "rule": rule,
        "severity": severity,
        "line_refs": line_refs,
        "explanation": explanation,
        "suggested_action": suggested_action,
    }


def _rule_duplicates(case: CaseIntake) -> list[dict[str, Any]]:
    """Rule 1: same code + same amount appearing 2+ times (likely double billing)."""
    groups: dict[tuple[str, float], list[int]] = {}
    for idx, item in enumerate(case.line_items):
        key = (item.code, round(item.amount, 2))
        groups.setdefault(key, []).append(idx)
    findings = []
    for (code, amount), refs in sorted(groups.items()):
        if len(refs) >= 2:
            findings.append(_finding(
                rule="duplicate_line_items",
                severity="error",
                line_refs=refs,
                explanation=(
                    f"Code {code} at ${amount:,.2f} appears {len(refs)} times "
                    f"(lines {[r + 1 for r in refs]}). This may indicate the same "
                    "charge was billed more than once."
                ),
                suggested_action=("Ask the provider to confirm whether the service "
                                  "was actually performed multiple times; if not, "
                                  "request removal of the duplicate charge(s)."),
            ))
    return findings


def _rule_bill_vs_eob(case: CaseIntake) -> list[dict[str, Any]]:
    """Rule 2: billed patient responsibility != EOB patient responsibility."""
    if case.eob is None:
        return []
    billed = round(case.billed_patient_responsibility, 2)
    eob = round(case.eob.patient_responsibility, 2)
    if billed == eob:
        return []
    diff = billed - eob
    direction = "higher" if diff > 0 else "lower"
    return [_finding(
        rule="bill_vs_eob_mismatch",
        severity="error",
        line_refs=[],
        explanation=(
            f"The bill asks you to pay ${billed:,.2f}, but your EOB says your "
            f"patient responsibility is ${eob:,.2f} — a ${abs(diff):,.2f} "
            f"{direction} amount on the bill."
        ),
        suggested_action=("Do not pay the billed amount yet. Contact the provider's "
                          "billing department with your EOB and ask them to "
                          "reconcile to the EOB figure before paying."),
    )]


def _rule_balance_billing(case: CaseIntake) -> list[dict[str, Any]]:
    """Rule 3: possible balance-billing scenario.

    LEGAL-SENSITIVE: this is a flag only. We describe the federal No Surprises
    Act protection in conditional language and instruct the user to verify.
    """
    triggered = case.is_out_of_network and (case.is_emergency or case.facility_in_network)
    if not triggered:
        return []
    context = ("emergency care from an out-of-network provider"
               if case.is_emergency
               else "out-of-network charges at an in-network facility")
    return [_finding(
        rule="possible_balance_billing",
        severity="warning",
        line_refs=[],
        explanation=(
            f"This case involves {context}. Under the federal No Surprises Act, "
            "patients in these situations MAY be protected from being billed "
            "more than their in-network cost-sharing amount — this is a flag "
            "for review only, not a legal determination."
        ),
        suggested_action=("Verify with your insurer and the provider whether No "
                          "Surprises Act protections apply to this bill, and "
                          "consider consulting a licensed attorney or your "
                          "state insurance regulator before paying."),
    )]


def _rule_upcoding_unbundling(case: CaseIntake) -> list[dict[str, Any]]:
    """Rule 4: heuristic mutually-exclusive code pairs billed together."""
    codes_present = {item.code for item in case.line_items}
    findings = []
    for pair in _CODE_PAIRS:
        codes = pair.get("codes", [])
        if len(codes) >= 2 and all(c in codes_present for c in codes):
            refs = [i for i, it in enumerate(case.line_items) if it.code in codes]
            findings.append(_finding(
                rule="possible_unbundling",
                severity="warning",
                line_refs=refs,
                explanation=(
                    f"Codes {', '.join(codes)} were billed together. These codes "
                    f"are often mutually exclusive ({pair.get('note', '')}). "
                    "HEURISTIC FLAG ONLY — this does not prove upcoding or "
                    "unbundling; coding depends on clinical detail we do not have."
                ),
                suggested_action=("Ask the provider's billing/coding department to "
                                  "confirm the codes were applied correctly and "
                                  "not unbundled."),
            ))
    return findings


def _rule_missing_itemization(case: CaseIntake) -> list[dict[str, Any]]:
    """Rule 5: thin bill with a large total -> request itemization."""
    total = round(case.billed_patient_responsibility, 2)
    if len(case.line_items) >= 3 or total <= 1000:
        return []
    return [_finding(
        rule="missing_itemized_detail",
        severity="warning",
        line_refs=[],
        explanation=(
            f"The bill shows only {len(case.line_items)} line item(s) for a total "
            f"of ${total:,.2f}. Without itemized CPT/HCPCS detail, billing errors "
            "cannot be verified."
        ),
        suggested_action=("Request a fully itemized bill from the provider before "
                          "paying or disputing specific charges."),
    )]


def _rule_prompt_pay(case: CaseIntake) -> list[dict[str, Any]]:
    """Rule 6: prompt-pay discount suggestion (negotiation, not an error)."""
    total = round(sum(i.amount * i.quantity for i in case.line_items), 2)
    return [_finding(
        rule="prompt_pay_suggestion",
        severity="info",
        line_refs=[],
        explanation=(
            f"On a ${total:,.2f} balance, many providers offer 10–20% discounts "
            "for immediate lump-sum payment. This is a negotiation opportunity, "
            "not a billing error."
        ),
        suggested_action=("Use the negotiation script pack to ask the billing "
                          "department about a prompt-pay discount before paying "
                          "the full amount."),
    )]


_RULES = [
    _rule_duplicates,
    _rule_bill_vs_eob,
    _rule_balance_billing,
    _rule_upcoding_unbundling,
    _rule_missing_itemization,
    _rule_prompt_pay,
]


def detect(case: CaseIntake) -> list[dict[str, Any]]:
    """Run all detection rules against a case intake. Returns findings list."""
    findings: list[dict[str, Any]] = []
    for rule in _RULES:
        findings.extend(rule(case))
    return findings
