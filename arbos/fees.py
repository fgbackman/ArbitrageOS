"""2026 US Amazon fee engine.

Rates: FBA fulfillment fees effective Jan 15 2026 (non-peak) and holiday peak
rates Oct 15 2026 - Jan 14 2027, both before the 3.5% fuel and logistics
surcharge (from Apr 17 2026). Referral fees unchanged for 2026.
Sources: goatconsulting.com 2026 rate card, amzprep.com peak table,
techmachaw.com referral/size-tier tables. Confirm against Seller Central's
Fee Preview report; when the SP-API is connected, live fee estimates replace
these tables.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from datetime import date
from typing import Optional

# ---------------------------------------------------------------- size tiers

DIM_DIVISOR = 139.0


@dataclass
class Package:
    length_in: float
    width_in: float
    height_in: float
    weight_lb: float

    @property
    def sides(self):
        return sorted([self.length_in, self.width_in, self.height_in], reverse=True)

    @property
    def cubic_feet(self) -> float:
        return self.length_in * self.width_in * self.height_in / 1728.0

    def dim_weight(self, bulky: bool = False) -> float:
        l, m, s = self.sides
        if bulky:  # bulky tiers use a 2 in minimum on width and height
            m, s = max(m, 2.0), max(s, 2.0)
        return l * m * s / DIM_DIVISOR


def size_tier(p: Package) -> str:
    l, m, s = p.sides
    girth = l + 2 * (m + s)
    if l <= 15 and m <= 12 and s <= 0.75 and p.weight_lb <= 1.0:
        return "small_standard"
    ship = max(p.weight_lb, p.dim_weight())
    if l <= 18 and m <= 14 and s <= 8 and ship <= 20:
        return "large_standard"
    bulky_ship = max(p.weight_lb, p.dim_weight(bulky=True))
    if l <= 37 and m <= 28 and s <= 20 and bulky_ship <= 50 and girth <= 130:
        return "small_bulky"
    if l <= 59 and m <= 33 and s <= 33 and bulky_ship <= 50 and girth <= 130:
        return "large_bulky"
    return "extra_large"


def shipping_weight(p: Package, tier: str) -> float:
    if tier == "small_standard":
        return p.weight_lb
    if tier == "large_standard":
        return max(p.weight_lb, p.dim_weight())
    if tier in ("small_bulky", "large_bulky"):
        return max(p.weight_lb, p.dim_weight(bulky=True))
    return p.weight_lb  # extra-large: Amazon uses unit weight above 150 lb; close enough


# ---------------------------------------------------------- fulfillment fees
# Each row: (max shipping weight in lb, [<$10, $10-$50, >$50])

SMALL_STD = {
    "nonpeak": [
        (2 / 16, [2.43, 3.32, 3.58]), (4 / 16, [2.49, 3.42, 3.68]), (6 / 16, [2.56, 3.45, 3.71]),
        (8 / 16, [2.66, 3.54, 3.80]), (10 / 16, [2.77, 3.68, 3.94]), (12 / 16, [2.82, 3.78, 4.04]),
        (14 / 16, [2.92, 3.91, 4.17]), (1.0, [2.95, 3.96, 4.22]),
    ],
    "peak": [
        (2 / 16, [2.62, 3.51, 3.77]), (4 / 16, [2.68, 3.61, 3.87]), (6 / 16, [2.76, 3.65, 3.91]),
        (8 / 16, [2.86, 3.74, 4.00]), (10 / 16, [2.98, 3.89, 4.15]), (12 / 16, [3.03, 3.99, 4.25]),
        (14 / 16, [3.14, 4.13, 4.39]), (1.0, [3.17, 4.18, 4.44]),
    ],
}

LARGE_STD = {
    "nonpeak": [
        (4 / 16, [2.91, 3.73, 3.99]), (8 / 16, [3.13, 3.95, 4.21]), (12 / 16, [3.38, 4.20, 4.46]),
        (1.0, [3.78, 4.60, 4.86]), (1.25, [4.22, 5.04, 5.30]), (1.5, [4.60, 5.42, 5.68]),
        (1.75, [4.75, 5.57, 5.83]), (2.0, [5.00, 5.82, 6.08]), (2.25, [5.10, 5.92, 6.18]),
        (2.5, [5.28, 6.10, 6.36]), (2.75, [5.44, 6.26, 6.52]), (3.0, [5.85, 6.67, 6.93]),
    ],
    "peak": [
        (4 / 16, [3.15, 3.97, 4.23]), (8 / 16, [3.39, 4.21, 4.47]), (12 / 16, [3.66, 4.48, 4.74]),
        (1.0, [4.07, 4.89, 5.15]), (1.25, [4.52, 5.34, 5.60]), (1.5, [4.91, 5.73, 5.99]),
        (1.75, [5.07, 5.89, 6.15]), (2.0, [5.33, 6.15, 6.41]), (2.25, [5.47, 6.29, 6.55]),
        (2.5, [5.67, 6.49, 6.75]), (2.75, [5.84, 6.66, 6.92]), (3.0, [6.26, 7.08, 7.34]),
    ],
}
# 3+ to 20 lb: base + $0.08 per 4 oz above the first 3 lb
LARGE_STD_OVER3 = {"nonpeak": [6.15, 6.97, 7.23], "peak": [6.69, 7.51, 7.77]}

# Bulky: base + per-lb above the first lb (first 50 lb); [<$10, >=$10]
BULKY = {
    "small_bulky": {"nonpeak": [6.78, 7.55], "peak": [7.82, 8.59], "per_lb": 0.38},
    "large_bulky": {"nonpeak": [8.58, 9.35], "peak": [9.62, 10.39], "per_lb": 0.38},
    "extra_large": {"nonpeak": [25.56, 26.33], "peak": [28.29, 29.06], "per_lb": 0.38},
}

FUEL_SURCHARGE_START = date(2026, 4, 17)


def is_peak(d: date) -> bool:
    """Holiday peak fulfillment fees run Oct 15 to Jan 14."""
    return (d.month == 10 and d.day >= 15) or d.month in (11, 12) or (d.month == 1 and d.day <= 14)


def price_band(price: float) -> int:
    if price < 10:
        return 0
    if price <= 50:
        return 1
    return 2


def fulfillment_fee(p: Package, price: float, on: date, fuel_pct: float = 0.035) -> dict:
    tier = size_tier(p)
    sw = shipping_weight(p, tier)
    season = "peak" if is_peak(on) else "nonpeak"
    band = price_band(price)
    if tier == "small_standard":
        base = next(r[band] for w, r in SMALL_STD[season] if sw <= w + 1e-9)
    elif tier == "large_standard":
        rows = LARGE_STD[season]
        if sw <= 3.0 + 1e-9:
            base = next(r[band] for w, r in rows if sw <= w + 1e-9)
        else:
            steps = math.ceil((sw - 3.0) / 0.25 - 1e-9)
            base = LARGE_STD_OVER3[season][band] + 0.08 * steps
    else:
        t = BULKY[tier]
        base = t[season][0 if band == 0 else 1] + t["per_lb"] * max(0.0, math.ceil(sw) - 1)
    fuel = base * fuel_pct if on >= FUEL_SURCHARGE_START else 0.0
    return {
        "tier": tier,
        "shipping_weight_lb": round(sw, 3),
        "season": season,
        "base": round(base, 2),
        "fuel_surcharge": round(fuel, 2),
        "total": round(base + fuel, 2),
    }


# ------------------------------------------------------------- referral fees
# mode "whole": the rate for the bracket the price falls into applies to the whole price.
# mode "marginal": each rate applies to the portion of the price in its bracket.

REFERRAL = {
    "default": {"mode": "flat", "rate": 0.15},
    "Home & Kitchen": {"mode": "flat", "rate": 0.15},
    "Toys & Games": {"mode": "flat", "rate": 0.15},
    "Sports & Outdoors": {"mode": "flat", "rate": 0.15},
    "Tools & Home Improvement": {"mode": "flat", "rate": 0.15},
    "Office Products": {"mode": "flat", "rate": 0.15},
    "Patio, Lawn & Garden": {"mode": "flat", "rate": 0.15},
    "Pet Supplies": {"mode": "flat", "rate": 0.15},
    "Arts, Crafts & Sewing": {"mode": "flat", "rate": 0.15},
    "Video Games": {"mode": "flat", "rate": 0.15},
    "Musical Instruments": {"mode": "flat", "rate": 0.15},
    "Industrial & Scientific": {"mode": "flat", "rate": 0.12},
    "Automotive": {"mode": "flat", "rate": 0.12},
    "Electronics": {"mode": "flat", "rate": 0.08},
    "Computers": {"mode": "flat", "rate": 0.08},
    "Cell Phones & Accessories": {"mode": "marginal", "brackets": [(100, 0.15), (None, 0.08)]},
    "Beauty & Personal Care": {"mode": "whole", "brackets": [(10, 0.08), (None, 0.15)]},
    "Health & Household": {"mode": "whole", "brackets": [(10, 0.08), (None, 0.15)]},
    "Baby": {"mode": "whole", "brackets": [(10, 0.08), (None, 0.15)]},
    "Grocery & Gourmet Food": {"mode": "whole", "brackets": [(15, 0.08), (None, 0.15)]},
    "Clothing, Shoes & Jewelry": {"mode": "whole", "brackets": [(15, 0.05), (20, 0.10), (None, 0.17)]},
    "Furniture": {"mode": "marginal", "brackets": [(200, 0.15), (None, 0.10)]},
    "Jewelry": {"mode": "marginal", "brackets": [(250, 0.20), (None, 0.05)]},
    "Watches": {"mode": "marginal", "brackets": [(1500, 0.16), (None, 0.03)]},
    "Appliances": {"mode": "marginal", "brackets": [(300, 0.15), (None, 0.08)]},
}
REFERRAL_MIN = 0.30
MEDIA_CLOSING_FEE = 1.80
MEDIA_CATEGORIES = {"Books", "Movies & TV", "CDs & Vinyl", "Software"}


def referral_fee(price: float, category: Optional[str], keepa_pct: Optional[float] = None) -> float:
    rule = REFERRAL.get(category or "default")
    if rule is None:
        if keepa_pct:  # Keepa reports the effective percentage for the listing
            return round(max(REFERRAL_MIN, price * keepa_pct / 100.0), 2)
        rule = REFERRAL["default"]
    if rule["mode"] == "flat":
        fee = price * rule["rate"]
    elif rule["mode"] == "whole":
        rate = next(r for cap, r in rule["brackets"] if cap is None or price <= cap)
        fee = price * rate
    else:
        fee, lower = 0.0, 0.0
        for cap, r in rule["brackets"]:
            upper = price if cap is None else min(price, cap)
            if upper > lower:
                fee += (upper - lower) * r
            if cap is None or price <= cap:
                break
            lower = cap
    fee = max(REFERRAL_MIN, fee)
    if category in MEDIA_CATEGORIES:
        fee += MEDIA_CLOSING_FEE
    return round(fee, 2)


# ------------------------------------------------------- inbound and storage
# Inbound placement service fee midpoints (2026) per unit.
PLACEMENT = {
    "small_standard": {"minimal": 0.26, "partial": 0.17},
    "large_standard": [  # (max lb, minimal, partial)
        (0.75, 0.29, 0.19), (1.5, 0.34, 0.22), (3.0, 0.41, 0.26), (20.0, 0.55, 0.36),
    ],
}


def placement_fee(tier: str, ship_lb: float, option: str) -> float:
    if option == "amazon_optimized" or tier not in PLACEMENT:
        return 0.0
    if tier == "small_standard":
        return PLACEMENT[tier][option]
    for cap, minimal, partial in PLACEMENT[tier]:
        if ship_lb <= cap:
            return minimal if option == "minimal" else partial
    return PLACEMENT[tier][-1][1 if option == "minimal" else 2]


def storage_rate(tier: str, on: date) -> float:
    """Monthly storage per cubic foot (base rate, under 22 weeks of supply)."""
    q4 = on.month in (10, 11, 12)
    if tier in ("small_standard", "large_standard"):
        return 2.40 if q4 else 0.78
    return 1.40 if q4 else 0.56


# ------------------------------------------------------------------ the stack

@dataclass
class FeeBreakdown:
    price: float
    referral: float
    fulfillment: float
    fulfillment_base: float
    fuel_surcharge: float
    placement: float
    storage: float
    prep: float
    inbound_shipping: float
    returns_reserve: float
    tier: str
    season: str
    shipping_weight_lb: float
    source: str = "rate-card"
    notes: list = field(default_factory=list)

    @property
    def amazon_fees(self) -> float:
        return round(self.referral + self.fulfillment + self.placement + self.storage, 2)

    @property
    def total(self) -> float:
        return round(self.amazon_fees + self.prep + self.inbound_shipping + self.returns_reserve, 2)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["amazon_fees"] = self.amazon_fees
        d["total"] = self.total
        return d


def fee_stack(price: float, pkg: Package, category: Optional[str], costs: dict, on: Optional[date] = None,
              keepa_referral_pct: Optional[float] = None, months_stored: Optional[float] = None) -> FeeBreakdown:
    on = on or date.today()
    ful = fulfillment_fee(pkg, price, on, costs.get("fuel_surcharge_pct", 0.035))
    tier, sw = ful["tier"], ful["shipping_weight_lb"]
    months = costs.get("expected_months_in_storage", 1.0) if months_stored is None else months_stored
    storage = storage_rate(tier, on) * pkg.cubic_feet * months
    inbound = max(costs.get("inbound_min_per_unit", 0.25), costs.get("inbound_per_lb", 0.45) * sw)
    notes = []
    if tier not in ("small_standard", "large_standard"):
        notes.append(f"{tier.replace('_', ' ')}: oversize fees apply")
    if ful["season"] == "peak":
        notes.append("holiday peak fulfillment rates (Oct 15 - Jan 14)")
    return FeeBreakdown(
        price=round(price, 2),
        referral=referral_fee(price, category, keepa_referral_pct),
        fulfillment=ful["total"],
        fulfillment_base=ful["base"],
        fuel_surcharge=ful["fuel_surcharge"],
        placement=placement_fee(tier, sw, costs.get("placement_option", "amazon_optimized")),
        storage=round(storage, 2),
        prep=round(costs.get("prep_per_unit", 0.75), 2),
        inbound_shipping=round(inbound, 2),
        returns_reserve=round(price * costs.get("returns_reserve_pct", 0.02), 2),
        tier=tier,
        season=ful["season"],
        shipping_weight_lb=sw,
        notes=notes,
    )
