"""Build data/state_claims.json: official unclaimed-property portals for all 50 states + DC.

Portal URLs are sourced from the FDIC's unclaimed-property state list
(which is derived from missingmoney.com / NAUPA), retrieved 2026-09-19,
with spot corrections (FL, KS) verified against official state sources the
same day. Portal URLs change often; re-verify before any public release.
Run: python3 data/build_states.py
"""
import json
from pathlib import Path

VERIFIED = "2026-09-19"
SOURCE = "FDIC unclaimed-property-by-state list (derived from missingmoney.com / NAUPA), retrieved 2026-09-19; spot corrections verified same day"

DEFAULT_SUMMARY = (
    "Search the state's database by name. If you find property in your name, start a claim "
    "on the official portal, create an account, and upload or mail the required documents. "
    "Simple personal claims are usually completed online; claims for businesses, estates, "
    "deceased owners, or high-value property typically require mailed claim forms plus "
    "additional proof."
)
DEFAULT_DOCS = [
    "Government-issued photo ID",
    "Proof of Social Security number (SSN card, W-2, or tax document)",
    "Proof of address tied to the property (utility bill, bank statement, tax return, "
    "or mail from the company that reported the property)",
]

# (abbr, state name, portal url, note)
STATES = [
    ("AK", "Alaska", "https://unclaimedproperty.alaska.gov/", ""),
    ("AL", "Alabama", "https://treasury.alabama.gov", ""),
    ("AR", "Arkansas", "https://auditor.ar.gov", ""),
    ("AZ", "Arizona", "https://azdor.gov/unclaimed-property", ""),
    ("CA", "California", "https://www.sco.ca.gov/upd_msg.html", ""),
    ("CO", "Colorado", "http://www.colorado.gov/treasury/gcp/index.html", ""),
    ("CT", "Connecticut", "https://www.ctbiglist.com/", ""),
    ("DC", "District of Columbia", "https://cfo.dc.gov/page/unclaimed-property-how-reclaim-property", ""),
    ("DE", "Delaware", "https://unclaimedproperty.delaware.gov", ""),
    ("FL", "Florida", "https://www.fltreasurehunt.gov", "Verified 2026-09-19 against Florida CFO press releases; FDIC list still shows an unclaimed.org redirect."),
    ("GA", "Georgia", "https://dor.georgia.gov/unclaimed-property-program", ""),
    ("HI", "Hawaii", "https://budget.hawaii.gov/finance/unclaimedproperty", ""),
    ("IA", "Iowa", "https://www.greatiowatreasurehunt.gov", ""),
    ("ID", "Idaho", "https://www.accessidaho.org/apps/tax/ucpsearch/", ""),
    ("IL", "Illinois", "https://icash.illinoistreasurer.gov/", ""),
    ("IN", "Indiana", "https://www.indianaunclaimed.com/apps/ag/ucp/index.html", ""),
    ("KS", "Kansas", "https://kansascash.ks.gov", "Verified 2026-09-19 against Kansas State Treasurer releases; FDIC list still shows unclaimedproperty.ks.gov."),
    ("KY", "Kentucky", "https://treasury.ky.gov/unclaimedproperty/Pages/overview.aspx", ""),
    ("LA", "Louisiana", "https://louisiana.findyourunclaimedproperty.com", ""),
    ("MA", "Massachusetts", "http://www.mass.gov/?pageID=trehomepage&L=1&L0=Home&sid=Ctre", ""),
    ("MD", "Maryland", "https://marylandtaxes.gov/unclaimed-property/index.php", ""),
    ("ME", "Maine", "https://www.maineunclaimedproperty.gov", ""),
    ("MI", "Michigan", "https://unclaimedproperty.michigan.gov", ""),
    ("MN", "Minnesota", "https://mn.gov/commerce/money/unclaimed-property/", ""),
    ("MO", "Missouri", "https://www.treasurer.mo.gov/unclaimedproperty", ""),
    ("MS", "Mississippi", "https://unclaimed.org/reporting/mississippi", "FDIC/NAUPA lists a reporting link; confirm the state portal from it before filing."),
    ("MT", "Montana", "https://mtrevenue.gov/2018/08/28/department-of-revenue-urges-montanans-to-check-for-unclaimed-cash-property", "FDIC link appears stale (2018 press post); start at the Montana Department of Revenue unclaimed-property page."),
    ("NC", "North Carolina", "https://unclaimed.nccash.com/app/claim-search", ""),
    ("ND", "North Dakota", "https://unclaimedproperty.nd.gov", ""),
    ("NE", "Nebraska", "https://nebraskalostcash.nebraska.gov", ""),
    ("NH", "New Hampshire", "https://www.nh.gov/treasury/contact-us/index.htm", ""),
    ("NJ", "New Jersey", "http://www.unclaimedproperty.nj.gov/", ""),
    ("NM", "New Mexico", "https://www.tax.newmexico.gov/individuals/what-is-unclaimed-property/search-unclaimed-property", ""),
    ("NV", "Nevada", "https://claims.nevadaunclaimedproperty.gov", ""),
    ("NY", "New York", "https://www.osc.state.ny.us/unclaimed-funds", ""),
    ("OH", "Ohio", "https://www.com.ohio.gov/unfd", ""),
    ("OK", "Oklahoma", "http://www.ok.gov/treasurer/Unclaimed_Property/index.html", ""),
    ("OR", "Oregon", "https://unclaimed.oregon.gov", ""),
    ("PA", "Pennsylvania", "http://www.patreasury.gov/unclaimed-property/", "PA Treasury ran a system conversion 2026-09-04..13; portal reopened 2026-09-14 per Treasury notice."),
    ("RI", "Rhode Island", "https://findrimoney.com/", ""),
    ("SC", "South Carolina", "https://treasurer.sc.gov/what-we-do/for-citizens/unclaimed-property-program/", ""),
    ("SD", "South Dakota", "http://www.sdtreasurer.gov/", ""),
    ("TN", "Tennessee", "http://treasury.tn.gov/unclaim/", ""),
    ("TX", "Texas", "https://comptroller.texas.gov/programs/unclaimed", ""),
    ("UT", "Utah", "https://mycash.utah.gov", ""),
    ("VA", "Virginia", "https://trs.virginia.gov/Unclaimed-Property", "FDIC link carries a leading 'h' typo; stripped here."),
    ("VT", "Vermont", "https://www.vermonttreasurer.gov/content/unclaimed-property", ""),
    ("WA", "Washington", "https://ucp.dor.wa.gov", ""),
    ("WI", "Wisconsin", "https://www.revenue.wi.gov/Pages/UnclaimedProperty/Home.aspx", ""),
    ("WV", "West Virginia", "http://www.wvtreasury.com/Unclaimed-Property/Search-Claim", ""),
    ("WY", "Wyoming", "http://treasurer.state.wy.us/uphome.asp", ""),
]

entries = []
for abbr, name, portal, note in STATES:
    entries.append({
        "abbr": abbr,
        "name": name,
        "portal_url": portal,
        "search_url": portal,
        "filing": "online",
        "claim_summary": DEFAULT_SUMMARY,
        "docs_required": DEFAULT_DOCS,
        "note": note,
        "source_url": SOURCE,
        "last_verified": VERIFIED,
    })

out = Path(__file__).parent / "state_claims.json"
out.write_text(json.dumps({"states": entries, "generated": VERIFIED, "refresh_note":
    "Portal URLs and claim processes change without notice. Re-verify links against "
    "unclaimed.org / missingmoney.com before a public release or when a user reports a dead link."
}, indent=2))
print(f"wrote {out} with {len(entries)} entries")
