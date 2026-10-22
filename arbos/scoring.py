"""Deal scoring: profit, ROI and the plan's buy filter for one product at one buy cost."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional

from . import fees as F
from .providers.base import ProductData
from .providers.demo import DEMO_IP_BRANDS

# Rough rank -> monthly sales curve by category (top-level US), used only when
# Keepa's monthlySold and rank-drop counts are missing. Deliberately conservative.
RANK_CURVES = {
    "default": [(1000, 900), (5000, 300), (20000, 120), (50000, 50), (100000, 25), (250000, 8), (500000, 3)],
    "Home & Kitchen": [(2000, 1200), (10000, 400), (40000, 150), (100000, 60), (250000, 20), (600000, 6)],
    "Toys & Games": [(1000, 1000), (5000, 350), (20000, 120), (60000, 40), (150000, 12), (400000, 4)],
    "Health & Household": [(1000, 1200), (5000, 400), (20000, 130), (60000, 45), (150000, 15), (400000, 4)],
    "Grocery & Gourmet Food": [(500, 1500), (3000, 450), (15000, 140), (50000, 40), (120000, 12)],
    "Office Products": [(500, 900), (3000, 300), (15000, 100), (50000, 30), (120000, 10)],
    "Pet Supplies": [(500, 1000), (3000, 350), (15000, 110), (50000, 35), (120000, 10)],
    "Sports & Outdoors": [(1000, 800), (5000, 250), (25000, 80), (80000, 25), (200000, 8)],
}


def estimate_monthly_sales(p: ProductData) -> tuple:
    """Returns (estimate, method)."""
    if p.monthly_sold:
        return int(p.monthly_sold), "Amazon 'bought in past month'"
    if p.rank_drops30 and p.rank_drops30 > 0:
        # every rank drop is at least one sale; real sales usually run 1.2-2x drops
        return int(p.rank_drops30 * 1.3), "Keepa rank drops (30d)"
    rank = p.rank_avg90 or p.sales_rank
    if not rank:
        return 0, "no demand data"
    curve = RANK_CURVES.get(p.category, RANK_CURVES["default"])
    prev_r, prev_s = 1, curve[0][1] * 2
    for r, s in curve:
        if rank <= r:
            # log-linear interpolation between points
            frac = (rank - prev_r) / max(1, r - prev_r)
            return int(prev_s + (s - prev_s) * frac), "rank curve estimate"
        prev_r, prev_s = r, s
    return 1, "rank curve estimate"


@dataclass
class Check:
    key: str
    label: str
    status: str  # pass | warn | fail | unknown
    detail: str

    def to_dict(self):
        return self.__dict__.copy()


def conservative_price(p: ProductData, rules: dict) -> tuple:
    """Price we expect to sell at: current Buy Box, capped near the 90-day average."""
    current = p.buybox_price or p.new_price
    if not current:
        return None, "no current price"
    if p.avg90 and current > p.avg90 * (1 + rules["max_price_above_avg90"]):
        capped = round(p.avg90 * (1 + rules["max_price_above_avg90"] / 2), 2)
        return capped, f"current ${current:.2f} is a spike over 90-day avg ${p.avg90:.2f}; using ${capped:.2f}"
    return round(current, 2), "current Buy Box"


def package_of(p: ProductData) -> Optional[F.Package]:
    if not all([p.length_in, p.width_in, p.height_in, p.weight_lb]):
        return None
    return F.Package(p.length_in, p.width_in, p.height_in, p.weight_lb)


def evaluate(p: ProductData, buy_cost: float, cfg: dict, *, sell_price: Optional[float] = None,
             cashback_pct: Optional[float] = None, sales_tax_paid: Optional[float] = None,
             on: Optional[date] = None, capital: Optional[float] = None,
             expiry_days: Optional[int] = None, live_fees: Optional[dict] = None, flip: bool = False) -> dict:
    """flip=True: an Amazon price drop bought from Amazon itself, resold once the price recovers
    to its 90-day average; Amazon on the listing is expected, so it is a warning, not a fail."""
    rules, costs, biz = cfg["rules"], cfg["costs"], cfg["business"]
    on = on or date.today()
    checks = []

    if sell_price:
        price, price_note = sell_price, "price you entered"
    elif flip and p.avg90:
        price, price_note = round(p.avg90, 2), f"90-day average ${p.avg90:.2f} (price recovery after the drop)"
    else:
        price, price_note = conservative_price(p, rules)
    pkg = package_of(p)
    if price is None or pkg is None:
        missing = "price" if price is None else "package dimensions"
        return {"asin": p.asin, "verdict": "UNKNOWN", "reason": f"missing {missing}", "checks": [],
                "product": p.to_dict()}

    # landed cost per unit
    cb = costs["default_cashback_pct"] if cashback_pct is None else cashback_pct
    if sales_tax_paid is None:
        sales_tax_paid = 0.0 if biz.get("use_resale_certificate") else buy_cost * biz.get("sales_tax_rate", 0)
    landed_goods = buy_cost + sales_tax_paid - buy_cost * cb

    sales_est, sales_method = estimate_monthly_sales(p)
    sellers = (p.fba_offers or 0) + 1
    my_share = sales_est / sellers if sellers else sales_est
    months_to_sell = min(3.0, max(0.5, 1.0 / max(my_share, 0.01) * 10))  # 10 units horizon
    fb = F.fee_stack(price, pkg, p.category, costs, on, p.referral_pct, months_stored=min(months_to_sell, 2.0))
    if live_fees:  # SP-API estimate overrides referral + fulfillment
        fb.referral = live_fees.get("referral", fb.referral)
        fb.fulfillment = live_fees.get("fulfillment", fb.fulfillment)
        fb.source = "Amazon fee estimate"
    profit = round(price - fb.total - landed_goods, 2)
    roi = profit / landed_goods if landed_goods > 0 else 0.0
    margin = profit / price if price else 0.0

    fast = sales_est >= rules["fast_mover_monthly_sales"]
    roi_floor = rules["min_roi_fast_mover"] if fast else rules["min_roi"]
    checks.append(Check("roi", "ROI after all fees",
                        "pass" if roi >= roi_floor else ("warn" if roi >= roi_floor - 0.05 else "fail"),
                        f"{roi:.0%} (needs {roi_floor:.0%}{' for a fast mover' if fast else ''})"))
    checks.append(Check("profit", "Profit per unit",
                        "pass" if profit >= rules["min_profit"] else ("warn" if profit >= rules["min_profit"] * 0.7 else "fail"),
                        f"${profit:.2f} (needs ${rules['min_profit']:.2f})"))
    if sales_est == 0:
        checks.append(Check("velocity", "Sales velocity", "unknown", "no demand data"))
    else:
        checks.append(Check("velocity", "Sales velocity",
                            "pass" if sales_est >= rules["min_monthly_sales"] else ("warn" if sales_est >= rules["min_monthly_sales"] * 0.6 else "fail"),
                            f"~{sales_est}/month ({sales_method})"))
    # Amazon on listing
    if flip:
        checks.append(Check("amazon", "Amazon on listing", "warn",
                            "flip: you buy from Amazon's price drop; profit needs the price to recover"))
    elif p.amazon_price:
        gap = (p.amazon_price - price) / price
        if rules["allow_amazon_on_listing"] or gap >= rules["amazon_price_gap_ok"]:
            checks.append(Check("amazon", "Amazon on listing", "warn", f"Amazon sells at ${p.amazon_price:.2f} ({gap:+.0%} vs your price)"))
        else:
            checks.append(Check("amazon", "Amazon on listing", "fail", f"Amazon sells at ${p.amazon_price:.2f} and will hold the Buy Box"))
    elif p.buybox_is_amazon:
        checks.append(Check("amazon", "Amazon on listing", "fail", "Amazon holds the Buy Box"))
    else:
        oos = f" (Amazon out of stock {p.amazon_oos_pct90:.0f}% of 90 days)" if p.amazon_oos_pct90 is not None else ""
        checks.append(Check("amazon", "Amazon on listing", "pass", "Amazon not selling" + oos))
    # competition
    if p.fba_offers is None:
        checks.append(Check("sellers", "FBA sellers", "unknown", "offer count unavailable"))
    else:
        checks.append(Check("sellers", "FBA sellers",
                            "pass" if p.fba_offers <= rules["max_fba_sellers"] else ("warn" if p.fba_offers <= rules["max_fba_sellers"] + 4 else "fail"),
                            f"{p.fba_offers} FBA offers (limit {rules['max_fba_sellers']})"))
    # price stability
    if p.avg90 and p.buybox_price:
        drift = (p.buybox_price - p.avg90) / p.avg90
        st = "pass" if abs(drift) <= rules["max_price_above_avg90"] else "warn"
        if p.min90 and p.min90 < landed_goods + fb.total:
            st = "warn"
            extra = f"; 90-day low ${p.min90:.2f} would lose money"
        else:
            extra = ""
        checks.append(Check("price", "Price history (90 days)", st, f"now {drift:+.0%} vs 90-day avg ${p.avg90:.2f}{extra}"))
    else:
        checks.append(Check("price", "Price history (90 days)", "unknown", "no history"))
    # eligibility
    if p.gated is None:
        checks.append(Check("gated", "Can you sell it?", "unknown", "connect Seller Central to check gating"))
    else:
        checks.append(Check("gated", "Can you sell it?", "fail" if p.gated else "pass", "approval required" if p.gated else "ungated for your account"))
    # brand / IP risk
    brand = (p.brand or "").strip()
    wl = cfg["watchlists"]
    if brand and any(brand.lower() == b.lower() for b in wl["blocked_brands"]):
        checks.append(Check("ip", "Brand risk", "fail", f"{brand} is on your blocked list"))
    elif brand and (any(brand.lower() == b.lower() for b in wl["ip_risk_brands"])
                    or (p.source == "demo" and brand in DEMO_IP_BRANDS)):
        checks.append(Check("ip", "Brand risk", "fail", f"{brand} has a history of IP complaints"))
    else:
        checks.append(Check("ip", "Brand risk", "pass", "no known complaint history" if brand else "brand unknown"))
    checks.append(Check("hazmat", "Hazmat", "fail" if p.hazmat else "pass", "dangerous goods review" if p.hazmat else "none flagged"))
    checks.append(Check("size", "Size tier", "pass" if fb.tier in rules["allowed_size_tiers"] else "fail", fb.tier.replace("_", " ")))
    if expiry_days is not None:
        checks.append(Check("expiry", "Shelf life", "pass" if expiry_days >= rules["min_days_to_expiry"] + 30 else "fail",
                            f"{expiry_days} days (Amazon needs {rules['min_days_to_expiry']}+ on arrival)"))

    fails = [c for c in checks if c.status == "fail"]
    warns = [c for c in checks if c.status == "warn"]
    if fails:
        verdict = "PASS"
    elif warns or any(c.status == "unknown" for c in checks if c.key in ("velocity", "gated")):
        verdict = "MAYBE"
    else:
        verdict = "BUY"

    # max cost that still hits the ROI floor and min profit
    net_before_goods = price - fb.total
    max_by_roi = net_before_goods / (1 + roi_floor)
    max_by_profit = net_before_goods - rules["min_profit"]
    max_landed = max(0.0, min(max_by_roi, max_by_profit))
    factor = (1 - cb) + (0 if biz.get("use_resale_certificate") else biz.get("sales_tax_rate", 0))
    max_buy = round(max_landed / factor, 2) if factor > 0 else 0.0

    # quantity: share of monthly sales x months of stock, capped by capital rule
    qty = int(my_share * rules["months_of_stock"]) if sales_est else 0
    if capital and landed_goods > 0:
        qty = min(qty, int(capital * rules["max_share_of_capital_per_sku"] / landed_goods))
    qty = max(qty, 0) if verdict != "PASS" else 0

    return {
        "asin": p.asin,
        "verdict": verdict,
        "sell_price": price,
        "price_note": price_note,
        "buy_cost": round(buy_cost, 2),
        "landed_cost": round(landed_goods, 2),
        "fees": fb.to_dict(),
        "profit": profit,
        "roi": round(roi, 4),
        "margin": round(margin, 4),
        "max_buy_cost": max_buy,
        "monthly_sales_est": sales_est,
        "monthly_profit": round(profit * my_share, 2),
        "sales_method": sales_method,
        "your_monthly_share": round(my_share, 1),
        "suggested_qty": qty,
        "checks": [c.to_dict() for c in checks],
        "product": p.to_dict(),
    }


def min_price_for(p_cost_landed: float, pkg: F.Package, category: Optional[str], cfg: dict,
                  on: Optional[date] = None, referral_pct: Optional[float] = None) -> float:
    """Lowest sale price that keeps the repricer floor ROI and profit."""
    rules, costs = cfg["rules"], cfg["costs"]
    lo, hi = 0.5, max(10.0, p_cost_landed * 6 + 30)
    target_roi, target_profit = rules["reprice_floor_roi"], rules["reprice_floor_profit"]
    # prep/inbound are already sunk in landed cost for owned inventory
    sunk = dict(costs, prep_per_unit=0.0, inbound_per_lb=0.0, inbound_min_per_unit=0.0, placement_option="amazon_optimized")
    for _ in range(60):
        mid = (lo + hi) / 2
        fb = F.fee_stack(mid, pkg, category, sunk, on, referral_pct, months_stored=0.5)
        profit = mid - fb.total - p_cost_landed
        if profit >= max(target_profit, p_cost_landed * target_roi):
            hi = mid
        else:
            lo = mid
    return round(hi + 0.005, 2)
