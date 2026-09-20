"""Bounded full-year 401(k) match illustrations for 2026, not payroll advice.

Sources: IRS Notice 2025-67, https://www.irs.gov/pub/irs-drop/n-25-67.pdf
Excludes catch-up contributions, midyear changes, vesting, other employer
contributions, after-tax contributions, multiple jobs and plan-specific limits.
"""
import math

IRS_ELECTIVE_DEFERRAL_LIMIT = 24500.0
IRS_COMPENSATION_LIMIT = 360000.0
IRS_TOTAL_CONTRIBUTION_LIMIT = 72000.0
IRS_LIMIT_TAX_YEAR = 2026
PAY_PERIODS = {"weekly": 52, "biweekly": 26, "semimonthly": 24, "monthly": 12}
ANNUAL_FEE_DOLLARS = 99.0  # Compatibility name; a one-time purchase, no renewal.
ANNUAL_FEE_CENTS = 9900


def calculate(salary: float, pay_frequency: str, current_contrib_pct: float,
              match_pct: float, match_cap_pct: float, true_up: bool = False) -> dict:
    values = (salary, current_contrib_pct, match_pct, match_cap_pct)
    if not all(math.isfinite(v) for v in values):
        raise ValueError("All financial inputs must be finite")
    if not 0 < salary <= IRS_COMPENSATION_LIMIT:
        raise ValueError("This illustration supports annual compensation up to the 2026 $360,000 cap. Ask your plan administrator about higher compensation.")
    if pay_frequency not in PAY_PERIODS or not (0 <= current_contrib_pct <= 100 and 0 <= match_pct <= 200 and 0 <= match_cap_pct <= 100):
        raise ValueError("Invalid pay frequency or match percentage")
    periods = PAY_PERIODS[pay_frequency]
    gross = salary / periods
    requested_current = salary * current_contrib_pct / 100
    current_employee = min(requested_current, IRS_ELECTIVE_DEFERRAL_LIMIT)
    cap_dollars = salary * match_cap_pct / 100
    matchable = min(current_employee, cap_dollars)
    current_match = min(matchable * match_pct / 100, IRS_TOTAL_CONTRIBUTION_LIMIT - current_employee)
    target_employee = min(cap_dollars, IRS_ELECTIVE_DEFERRAL_LIMIT, IRS_TOTAL_CONTRIBUTION_LIMIT / (1 + match_pct / 100)) if match_pct > 0 else 0
    # Keep four decimal places and round DOWN at a binding contribution limit.
    required_pct = math.floor(target_employee / salary * 100 * 10000 + 1e-9) / 10000
    new_employee = min(salary * required_pct / 100, IRS_ELECTIVE_DEFERRAL_LIMIT)
    new_match = min(min(new_employee, cap_dollars) * match_pct / 100,
                    IRS_TOTAL_CONTRIBUTION_LIMIT - new_employee)
    max_match = min(target_employee * match_pct / 100,
                    IRS_TOTAL_CONTRIBUTION_LIMIT - target_employee)
    r = lambda v: round(v, 2)
    return {
        "salary": r(salary), "pay_frequency": pay_frequency, "pay_periods_per_year": periods,
        "per_paycheck_gross": r(gross), "current_contrib_pct": current_contrib_pct,
        "current_per_paycheck_contrib": r(current_employee / periods),
        "current_annual_employee_contrib": r(current_employee),
        "current_input_exceeds_base_limit": requested_current > IRS_ELECTIVE_DEFERRAL_LIMIT,
        "match_pct": match_pct, "match_cap_pct": match_cap_pct,
        "match_cap_dollars": r(cap_dollars), "matchable_dollars": r(matchable),
        "employer_match_now": r(current_match), "max_annual_employer_match": r(max_match),
        "uncaptured_match_annual": r(max(0, max_match - current_match)),
        "required_contrib_pct": required_pct,
        "irs_elective_deferral_limit": IRS_ELECTIVE_DEFERRAL_LIMIT,
        "irs_compensation_limit": IRS_COMPENSATION_LIMIT,
        "irs_total_contribution_limit": IRS_TOTAL_CONTRIBUTION_LIMIT,
        "irs_limit_tax_year": IRS_LIMIT_TAX_YEAR,
        "irs_limit_binding": cap_dollars > IRS_ELECTIVE_DEFERRAL_LIMIT,
        "new_annual_employee_contrib": r(new_employee),
        "new_per_paycheck_contrib": r(new_employee / periods),
        "new_employer_match": r(new_match),
        "projected_annual_gain": r(max(0, new_match - current_match)), "true_up": true_up,
        "calculation_basis": "Full year at unchanged salary; capped annual contributions are averaged over pay periods, not a simulation of payroll stopping at the annual limit. Figures are illustrations, not guaranteed savings.",
        "scope_limits": ["No age-based catch-up", "No midyear or year-to-date contributions", "No other plans or employer contributions", "No vesting or true-up calculation", "Confirm payroll percentage increments and plan limits with the administrator"],
        "source_url": "https://www.irs.gov/pub/irs-drop/n-25-67.pdf",
    }
