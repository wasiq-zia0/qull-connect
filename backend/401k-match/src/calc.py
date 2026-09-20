"""401(k) match math engine.

All money values are dollars. Every intermediate step is returned so the
API/MCP output can show its work.
"""

import math

# Tax-year constant: IRS elective-deferral limit for 2026 ($23,500).
# MUST be reviewed/updated every tax year.
IRS_ELECTIVE_DEFERRAL_LIMIT = 23500.0
IRS_LIMIT_TAX_YEAR = 2026

PAY_PERIODS = {
    "weekly": 52,
    "biweekly": 26,
    "semimonthly": 24,
    "monthly": 12,
}

ANNUAL_FEE_DOLLARS = 99.0
ANNUAL_FEE_CENTS = 9900


def ceil_to_half(pct: float) -> float:
    """Round a percentage UP to the nearest 0.5%."""
    return math.ceil(pct * 2) / 2


def calculate(
    salary: float,
    pay_frequency: str,
    current_contrib_pct: float,
    match_pct: float,
    match_cap_pct: float,
    true_up: bool = False,
) -> dict:
    """Run the full match-capture calculation. Returns a dict of named steps."""
    periods = PAY_PERIODS[pay_frequency]

    per_paycheck_gross = salary / periods
    current_per_paycheck = per_paycheck_gross * current_contrib_pct / 100
    current_annual_employee = current_per_paycheck * periods  # == salary * pct/100

    cap_dollars = salary * match_cap_pct / 100
    matchable = min(current_annual_employee, cap_dollars)
    employer_match_now = matchable * match_pct / 100

    max_match = cap_dollars * match_pct / 100
    uncaptured = max_match - employer_match_now

    # Recommended contribution: the smallest 0.5%-step percent that makes
    # annual employee contribution >= cap_dollars, without breaching the IRS limit.
    raw_required_pct = match_cap_pct  # employee annual = salary*pct/100 >= cap_dollars
    required_pct = ceil_to_half(raw_required_pct)
    max_pct_for_irs = (IRS_ELECTIVE_DEFERRAL_LIMIT / salary) * 100
    irs_limited = required_pct > max_pct_for_irs
    if irs_limited:
        required_pct = math.floor(max_pct_for_irs * 2) / 2

    new_annual_employee = salary * required_pct / 100
    new_per_paycheck = new_annual_employee / periods
    new_matchable = min(new_annual_employee, cap_dollars)
    new_employer_match = new_matchable * match_pct / 100
    projected_annual_gain = new_employer_match - employer_match_now

    def r(x: float) -> float:
        return round(x, 2)

    return {
        "salary": r(salary),
        "pay_frequency": pay_frequency,
        "pay_periods_per_year": periods,
        "per_paycheck_gross": r(per_paycheck_gross),
        "current_contrib_pct": current_contrib_pct,
        "current_per_paycheck_contrib": r(current_per_paycheck),
        "current_annual_employee_contrib": r(current_annual_employee),
        "match_pct": match_pct,
        "match_cap_pct": match_cap_pct,
        "match_cap_dollars": r(cap_dollars),
        "matchable_dollars": r(matchable),
        "employer_match_now": r(employer_match_now),
        "max_annual_employer_match": r(max_match),
        "uncaptured_match_annual": r(uncaptured),
        "required_contrib_pct": required_pct,
        "irs_elective_deferral_limit": IRS_ELECTIVE_DEFERRAL_LIMIT,
        "irs_limit_tax_year": IRS_LIMIT_TAX_YEAR,
        "irs_limit_binding": irs_limited,
        "new_annual_employee_contrib": r(new_annual_employee),
        "new_per_paycheck_contrib": r(new_per_paycheck),
        "new_employer_match": r(new_employer_match),
        "projected_annual_gain": r(projected_annual_gain),
        "true_up": true_up,
    }
