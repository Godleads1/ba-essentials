"""Countries a business can be based in, with calling codes.

The country sets the business's pricing region (Terms 5.1):
- Nigeria pays in naira with Paystack.
- The rest of Africa pays the naira price plus a surcharge, in US dollars at the day's rate.
- Everywhere else pays the US dollar list price.
Both dollar regions pay with Flutterwave. Keep this list in step with the website's pricing.js.
"""
from __future__ import annotations

# (ISO 3166-1 alpha-2, name, calling code, in Africa)
COUNTRIES: tuple[tuple[str, str, str, bool], ...] = (
    ("NG", "Nigeria", "234", True),
    ("DZ", "Algeria", "213", True),
    ("AO", "Angola", "244", True),
    ("BJ", "Benin", "229", True),
    ("BW", "Botswana", "267", True),
    ("BF", "Burkina Faso", "226", True),
    ("BI", "Burundi", "257", True),
    ("CV", "Cabo Verde", "238", True),
    ("CM", "Cameroon", "237", True),
    ("CF", "Central African Republic", "236", True),
    ("TD", "Chad", "235", True),
    ("KM", "Comoros", "269", True),
    ("CG", "Congo", "242", True),
    ("CD", "DR Congo", "243", True),
    ("CI", "Côte d’Ivoire", "225", True),
    ("DJ", "Djibouti", "253", True),
    ("EG", "Egypt", "20", True),
    ("GQ", "Equatorial Guinea", "240", True),
    ("ER", "Eritrea", "291", True),
    ("SZ", "Eswatini", "268", True),
    ("ET", "Ethiopia", "251", True),
    ("GA", "Gabon", "241", True),
    ("GM", "Gambia", "220", True),
    ("GH", "Ghana", "233", True),
    ("GN", "Guinea", "224", True),
    ("GW", "Guinea-Bissau", "245", True),
    ("KE", "Kenya", "254", True),
    ("LS", "Lesotho", "266", True),
    ("LR", "Liberia", "231", True),
    ("LY", "Libya", "218", True),
    ("MG", "Madagascar", "261", True),
    ("MW", "Malawi", "265", True),
    ("ML", "Mali", "223", True),
    ("MR", "Mauritania", "222", True),
    ("MU", "Mauritius", "230", True),
    ("MA", "Morocco", "212", True),
    ("MZ", "Mozambique", "258", True),
    ("NA", "Namibia", "264", True),
    ("NE", "Niger", "227", True),
    ("RW", "Rwanda", "250", True),
    ("ST", "São Tomé and Príncipe", "239", True),
    ("SN", "Senegal", "221", True),
    ("SC", "Seychelles", "248", True),
    ("SL", "Sierra Leone", "232", True),
    ("SO", "Somalia", "252", True),
    ("ZA", "South Africa", "27", True),
    ("SS", "South Sudan", "211", True),
    ("SD", "Sudan", "249", True),
    ("TZ", "Tanzania", "255", True),
    ("TG", "Togo", "228", True),
    ("TN", "Tunisia", "216", True),
    ("UG", "Uganda", "256", True),
    ("ZM", "Zambia", "260", True),
    ("ZW", "Zimbabwe", "263", True),
    ("US", "United States", "1", False),
    ("GB", "United Kingdom", "44", False),
    ("CA", "Canada", "1", False),
    ("IE", "Ireland", "353", False),
    ("DE", "Germany", "49", False),
    ("FR", "France", "33", False),
    ("NL", "Netherlands", "31", False),
    ("BE", "Belgium", "32", False),
    ("ES", "Spain", "34", False),
    ("IT", "Italy", "39", False),
    ("PT", "Portugal", "351", False),
    ("CH", "Switzerland", "41", False),
    ("SE", "Sweden", "46", False),
    ("NO", "Norway", "47", False),
    ("DK", "Denmark", "45", False),
    ("FI", "Finland", "358", False),
    ("PL", "Poland", "48", False),
    ("AE", "United Arab Emirates", "971", False),
    ("SA", "Saudi Arabia", "966", False),
    ("QA", "Qatar", "974", False),
    ("IN", "India", "91", False),
    ("CN", "China", "86", False),
    ("SG", "Singapore", "65", False),
    ("MY", "Malaysia", "60", False),
    ("JP", "Japan", "81", False),
    ("AU", "Australia", "61", False),
    ("NZ", "New Zealand", "64", False),
    ("BR", "Brazil", "55", False),
    ("MX", "Mexico", "52", False),
    ("JM", "Jamaica", "1", False),
    ("TT", "Trinidad and Tobago", "1", False),
    ("TR", "Türkiye", "90", False),
)

BY_CODE = {code: (name, dial, africa) for code, name, dial, africa in COUNTRIES}

REGION_NIGERIA = "ng"
REGION_AFRICA = "africa"
REGION_INTERNATIONAL = "intl"


def region_for(code: str) -> str:
    """Pricing region for a country code. Unknown or blank (older accounts) counts as Nigeria."""
    code = (code or "").upper()
    if code == "NG" or code not in BY_CODE:
        return REGION_NIGERIA
    return REGION_AFRICA if BY_CODE[code][2] else REGION_INTERNATIONAL


def choices() -> list[tuple[str, str]]:
    """Nigeria first, then the rest of Africa, then everywhere else, each alphabetical."""
    nigeria = [(c, n) for c, n, _, _ in COUNTRIES if c == "NG"]
    africa = sorted(((c, n) for c, n, _, a in COUNTRIES if a and c != "NG"), key=lambda x: x[1])
    rest = sorted(((c, n) for c, n, _, a in COUNTRIES if not a), key=lambda x: x[1])
    return nigeria + africa + rest
