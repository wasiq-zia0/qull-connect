"""EU261/2004 eligibility engine.

Implements the core Regulation (EC) No 261/2004 rules:
  - Scope: departs an EU/EEA airport (any airline), or arrives in the EU/EEA
    on an EU/EEA-licensed carrier.
  - Qualifying disruption: arrival delay >= 3h (Sturgeon), cancellation with
    <14 days notice (with Art 5 rerouting exceptions), or denied boarding.
  - Exempt: extraordinary circumstances (Art 5(3)); technical faults are
    generally NOT exempt (Wallentin-Hermann).
  - Tiers (Art 7): <=1500 km -> EUR 250; 1500-3500 km -> EUR 400;
    >3500 km -> EUR 600. 50% reduction (Art 7(2)) when rerouted and arrival
    delay is below 2h / 3h / 4h per tier.

This is a template-automation engine, not legal advice.
"""
from __future__ import annotations

import csv
import math
from functools import lru_cache
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
AIRPORTS_CSV = DATA_DIR / "airports.csv"

EARTH_KM = 6371.0088


@lru_cache(maxsize=1)
def _airports() -> dict[str, dict]:
    out: dict[str, dict] = {}
    with open(AIRPORTS_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            out[row["iata"].upper()] = {
                "name": row["name"],
                "city": row["city"],
                "country": row["country"],
                "lat": float(row["lat"]),
                "lon": float(row["lon"]),
                "eu_eea": row["eu_eea"] == "1",
            }
    return out


def lookup_airport(iata: str) -> dict | None:
    return _airports().get((iata or "").strip().upper())


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_KM * math.asin(math.sqrt(a))


def great_circle_km(from_iata: str, to_iata: str) -> float | None:
    a, b = lookup_airport(from_iata), lookup_airport(to_iata)
    if not a or not b:
        return None
    return haversine_km(a["lat"], a["lon"], b["lat"], b["lon"])


def tier_amount(distance_km: float) -> tuple[int, int]:
    """Return (tier_eur, reduction_threshold_hours)."""
    if distance_km <= 1500:
        return 250, 2
    if distance_km <= 3500:
        return 400, 3
    return 600, 4


def verdict(claim: dict) -> dict:
    """Compute the EU261 eligibility verdict for a claim dict. Pure function.

    Expected claim keys:
      from_iata, to_iata, distance_km (optional), disruption ("delay" | "cancellation" | "denied_boarding"),
      scheduled_arrival, actual_arrival (ISO datetimes; for delay), arrival_delay_h (optional override),
      notice_days (optional, cancellation), rerouted (bool), reroute_dep_early_h, reroute_arr_delay_h,
      airline_eu_licensed (bool), extraordinary_circumstances (bool).
    """
    reasons: list[str] = []
    eligible = True
    base_amount = 0
    distance_km: float | None = claim.get("distance_km")

    from_ap = lookup_airport(claim.get("from_iata", ""))
    to_ap = lookup_airport(claim.get("to_iata", ""))
    if not from_ap or not to_ap:
        return _verdict(False, 0, None, ["Missing airport jurisdiction metadata: manual review required."], claim, from_ap, to_ap)
    if from_ap and to_ap:
        distance_km = great_circle_km(claim["from_iata"], claim["to_iata"])
        reasons.append(
            f"Great-circle distance {from_ap['city']} ({claim['from_iata'].upper()}) -> "
            f"{to_ap['city']} ({claim['to_iata'].upper()}): {distance_km:,.0f} km."
        )
    if distance_km is None:
        eligible = False
        reasons.append("Could not determine route distance: unknown airport code(s) "
                       "and no distance_km provided.")
        return _verdict(eligible, 0, None, reasons, claim, from_ap, to_ap)

    tier, reduce_below_h = tier_amount(distance_km)
    if from_ap and to_ap and from_ap["eu_eea"] and to_ap["eu_eea"] and distance_km > 1500:
        tier, reduce_below_h = 400, 3
    reasons.append(f"Distance {distance_km:,.0f} km -> Article 7 tier EUR {tier}.")

    # --- Scope (Article 3) ---
    from_eu = bool(from_ap and from_ap["eu_eea"])
    to_eu = bool(to_ap and to_ap["eu_eea"])
    eu_carrier = bool(claim.get("airline_eu_licensed"))
    if from_eu:
        reasons.append(f"Departure airport {claim['from_iata'].upper()} ({from_ap['country']}) "
                       "is in the EU/EEA: Regulation applies to any airline (Art 3(1)(a)).")
    elif to_eu and eu_carrier:
        reasons.append(f"Arrival airport {claim['to_iata'].upper()} ({to_ap['country']}) is in the "
                       "EU/EEA and the carrier is EU/EEA-licensed: Regulation applies (Art 3(1)(b)).")
    else:
        eligible = False
        if to_eu and not eu_carrier:
            reasons.append(
                "Out of scope: flight arrived in the EU/EEA but the carrier is not EU/EEA-licensed "
                "(Art 3(1)(b) requires a Community carrier).")
        else:
            reasons.append(
                "Out of scope: flight neither departed from an EU/EEA airport nor arrived in the "
                "EU/EEA on an EU/EEA-licensed carrier (Art 3(1)).")

    # --- Extraordinary circumstances (Art 5(3)) ---
    if claim.get("extraordinary_circumstances"):
        eligible = False
        reasons.append("Manual review required: airline attributes the disruption to extraordinary "
                       "circumstances (Art 5(3)). Note: technical faults are generally NOT "
                       "extraordinary (CJEU Wallentin-Hermann); contest this if the cited "
                       "cause is a technical defect.")

    # --- Qualifying disruption ---
    disruption = claim.get("disruption")
    arr_delay_h = claim.get("arrival_delay_h")
    ok_so_far = eligible  # scope + exemption passed?
    if disruption == "delay":
        if arr_delay_h is None:
            eligible = False
            reasons.append("Missing arrival-delay duration; cannot assess the 3-hour rule.")
        elif arr_delay_h >= 3:
            reasons.append(
                f"Arrival delay {arr_delay_h:.1f}h >= 3h at final destination: "
                + ("preliminary delay criteria met (Sturgeon / Art 7)." if ok_so_far
                   else "meets the delay threshold, but the claim is disqualified above."))
        else:
            eligible = False
            reasons.append(f"Arrival delay {arr_delay_h:.1f}h < 3h: no fixed compensation under "
                           "Reg 261/2004 (care/duty obligations may still apply).")
    elif disruption == "cancellation":
        tmp: list[str] = []
        qualifies = _cancellation_qualifies(claim, tmp)
        if qualifies and not ok_so_far:
            tmp[-1] += " However, the claim is disqualified above."
            qualifies = False
        reasons.extend(tmp)
        eligible = eligible and qualifies
    elif disruption == "denied_boarding":
        if claim.get("denied_boarding_involuntary") is not True or claim.get("valid_travel_documents") is not True:
            eligible = False
            reasons.append("Missing involuntary denial and valid-document confirmation: manual review required; health or security grounds may exclude compensation.")
        if eligible:
            reasons.append("Involuntary denial with valid documents was reported. Article 4 eligibility remains subject to check-in and other conditions.")
    else:
        eligible = False
        reasons.append(f"Unknown disruption type: {disruption!r}.")

    # --- 50% reduction (Art 7(2)) when rerouted with a small arrival delay ---
    reduced = False
    if eligible and claim.get("rerouted"):
        r_delay = claim.get("reroute_arr_delay_h", arr_delay_h)
        if r_delay is not None and r_delay <= reduce_below_h:
            reduced = True
            base_amount = tier // 2
            reasons.append(
                f"Rerouted with arrival delay {r_delay:.1f}h <= {reduce_below_h}h threshold: "
                f"Article 7(2) reduction applies -> EUR {base_amount}.")
    if eligible and not reduced:
        base_amount = tier

    if not eligible:
        base_amount = 0

    return _verdict(eligible, base_amount, round(distance_km), reasons, claim, from_ap, to_ap)


def _cancellation_qualifies(claim: dict, reasons: list[str]) -> bool:
    notice = claim.get("notice_days")
    if notice is None:
        reasons.append("Missing cancellation notice period: manual review required before estimating eligibility.")
        return False
    if notice >= 14:
        reasons.append(f"Cancellation notified {notice} days before departure (>= 14 days): "
                       "no compensation (Art 5(1)(c)(i)).")
        return False
    if claim.get("rerouted"):
        early = claim.get("reroute_dep_early_h")
        late = claim.get("reroute_arr_delay_h")
        if early is None or late is None:
            reasons.append("Missing rerouting departure/arrival timing: manual review required.")
            return False
        if notice >= 7 and early <= 2 and late < 4:
            reasons.append("Cancellation notified 7-14 days before, rerouting departs <=2h early "
                           "and arrives <4h late: no compensation (Art 5(1)(c)(ii)).")
            return False
        if notice < 7 and early <= 1 and late < 2:
            reasons.append("Cancellation notified <7 days before, rerouting departs <=1h early "
                           "and arrives <2h late: no compensation (Art 5(1)(c)(iii)).")
            return False
    reasons.append(f"Cancellation notified {notice} days before departure (< 14 days) without a "
                   "qualifying rerouting exception: preliminary compensation criteria met (Art 5(1)(c)).")
    return True


def _verdict(eligible, amount, distance_km, reasons, claim, from_ap, to_ap):
    return {
        "eligible": eligible,
        "assessment": "preliminary_eligible" if eligible else "needs_review" if any("manual review" in r.lower() or "missing" in r.lower() or "could not" in r.lower() for r in reasons) else "preliminary_ineligible",
        "not_a_guarantee": True,
        "official_source_url": "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32004R0261",
        "compensation_eur": amount,
        "distance_km": distance_km,
        "from": {"iata": claim.get("from_iata", "").upper(),
                 "name": from_ap["name"] if from_ap else None,
                 "eu_eea": from_ap["eu_eea"] if from_ap else None},
        "to": {"iata": claim.get("to_iata", "").upper(),
               "name": to_ap["name"] if to_ap else None,
               "eu_eea": to_ap["eu_eea"] if to_ap else None},
        "disruption": claim.get("disruption"),
        "reasons": reasons,
    }
