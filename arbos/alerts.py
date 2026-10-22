"""Things that need your attention today, most urgent first."""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from . import config, db, ledger, pnl, repricing

SEVERITY = {"high": 0, "medium": 1, "low": 2}


def _days_since(iso: Optional[str]) -> Optional[int]:
    if not iso:
        return None
    try:
        d = datetime.fromisoformat(iso[:19]).date()
    except ValueError:
        return None
    return (date.today() - d).days


def calendar(today: Optional[date] = None) -> list:
    """Seller-calendar dates that change what you should buy or ship."""
    t = today or date.today()
    y = t.year
    events = [
        (date(y, 1, 15), "Holiday peak fulfillment fees end; Q4 storage rate ended Dec 31", "low"),
        (date(y, 1, 20), "Post-holiday clearance: best buying of the year through February", "medium"),
        (date(y, 7, 1), "Back-to-school and summer clearance buying window", "low"),
        (date(y, 9, 30), "Last day of base storage rates; Q4 storage triples from Oct 1", "medium"),
        (date(y, 10, 15), "Holiday peak fulfillment fees start (avg +$0.32/unit until Jan 14)", "medium"),
        (date(y, 10, 21), "Black Friday/Cyber Monday inbound deadline, minimal-split shipments", "high"),
        (date(y, 10, 28), "Black Friday/Cyber Monday inbound deadline, Amazon-optimized shipments", "high"),
        (date(y, 11, 15), "Ship Christmas stock by now: FBA receiving slows sharply in late November", "high"),
        (date(y, 12, 10), "Last practical FBA inbound for pre-Christmas sales; consider merchant-fulfilled", "medium"),
    ]
    out = []
    for d, msg, sev in events:
        days = (d - t).days
        if 0 <= days <= 21:
            out.append({"kind": "calendar", "severity": sev, "title": msg, "detail": f"{d.strftime('%b %d')} (in {days} days)" if days else "today"})
    return out


def compute(cfg: Optional[dict] = None) -> list:
    cfg = cfg or config.load()
    rules = cfg["rules"]
    out = []
    active = ledger.lots(active_only=True)

    # 1. prices under your floor or Amazon on the listing
    try:
        for r in repricing.plan(cfg):
            if r["status"] == "market below floor":
                out.append({"kind": "price", "severity": "high", "sku": r["sku"],
                            "title": f"Market ${r['market_price']:.2f} is under your floor ${r['min_price']:.2f}",
                            "detail": f"{r['title'][:60]}: hold price or wait; do not chase it down"})
            elif r["status"] == "Amazon on listing":
                out.append({"kind": "price", "severity": "medium", "sku": r["sku"],
                            "title": "Amazon is now selling this", "detail": r["title"][:80]})
    except Exception as e:  # market data unavailable shouldn't hide the other alerts
        out.append({"kind": "system", "severity": "low", "title": "Could not refresh market prices", "detail": str(e)[:120]})

    # 2. aged and stalled inventory
    for l in active:
        if l["qty_left"] <= 0:
            continue
        age = _days_since(l["live_at"] or l["created_at"])
        if l["status"] == "live" and age is not None and age >= 150:
            out.append({"kind": "aged", "severity": "high", "sku": l["sku"],
                        "title": f"{l['qty_left']} units live {age} days; aged surcharge starts at 181",
                        "detail": f"{l['title'][:60]}: lower to floor, or create a removal/liquidation"})
        elif l["status"] == "live" and age is not None and age >= 90:
            out.append({"kind": "aged", "severity": "medium", "sku": l["sku"],
                        "title": f"Slow seller: {l['qty_left']} units left after {age} days",
                        "detail": l["title"][:80]})
        if l["status"] in ("bought", "prepped"):
            wait = _days_since(l["created_at"])
            if wait is not None and wait >= 7:
                out.append({"kind": "stalled", "severity": "medium", "sku": l["sku"],
                            "title": f"Bought {wait} days ago and not shipped", "detail": l["title"][:80]})
        if l["expiry"]:
            try:
                left = (date.fromisoformat(l["expiry"]) - date.today()).days
                if left < rules["min_days_to_expiry"] + 30:
                    out.append({"kind": "expiry", "severity": "high", "sku": l["sku"],
                                "title": f"Expires in {left} days", "detail": "Amazon needs 105+ days on arrival; sell or remove"})
            except ValueError:
                pass

    # 3. invoice defense: receipts missing
    missing = db.rows("SELECT p.id, p.bought_on, p.retailer, SUM(l.qty) AS units FROM purchases p JOIN lots l ON l.purchase_id=p.id "
                      "WHERE p.receipt_path IS NULL GROUP BY p.id")
    if missing:
        units = sum(m["units"] for m in missing)
        out.append({"kind": "receipts", "severity": "medium",
                    "title": f"{len(missing)} purchases ({units} units) have no receipt attached",
                    "detail": "Amazon can ask for proof within 48 hours; attach the order page or receipt now"})

    # 4. restock winners
    winners = db.rows(
        "SELECT s.asin, MAX(l.title) AS title, SUM(s.profit) AS profit, SUM(s.cogs) AS cogs, SUM(s.qty) AS units, "
        "MAX(p.retailer) AS retailer, MIN(l.unit_cost) AS last_cost "
        "FROM sales s JOIN lots l ON l.sku=s.sku LEFT JOIN purchases p ON p.id=l.purchase_id "
        "GROUP BY s.asin HAVING SUM(s.cogs) > 0")
    live_asins = {l["asin"] for l in active if l["qty_left"] > 0}
    restock = []
    for w in winners:
        roi = w["profit"] / w["cogs"]
        if w["asin"] not in live_asins and roi >= rules["min_roi"] and w["units"] >= 3:
            restock.append((w["profit"], roi, w))
    restock.sort(key=lambda t: -t[0])
    for profit, roi, w in restock[:3]:
        out.append({"kind": "restock", "severity": "low", "asin": w["asin"],
                    "title": f"Restock a winner: {w['units']} sold at {roi:.0%} ROI, ${profit:,.0f} profit",
                    "detail": f"{(w['title'] or '')[:60]} (last bought at {w['retailer'] or '?'} for ${w['last_cost']:.2f})"})
    if len(restock) > 3:
        out.append({"kind": "restock", "severity": "low", "title": f"{len(restock) - 3} more sold-out winners to restock",
                    "detail": "See Sales and profit, best SKUs"})

    # 5. idle cash
    pos = pnl.position(cfg)
    if pos["capital"] > 0 and pos["cash"] > 0.25 * pos["capital"] and pos["cash"] > 500:
        out.append({"kind": "cash", "severity": "low",
                    "title": f"${pos['cash']:,.0f} cash is idle ({pos['cash'] / pos['capital']:.0%} of capital)",
                    "detail": "Profit only compounds while it is in stock; keep about 10% as a buffer"})

    # 6. fresh deals from the latest scan
    last = db.one("SELECT * FROM scans WHERE status='done' ORDER BY id DESC LIMIT 1")
    if last and (_days_since(last["finished_at"]) or 0) <= 1 and last["buys"]:
        open_buys = db.scalar("SELECT COUNT(*) FROM leads WHERE scan_id=? AND verdict='BUY' AND status IN ('new','scored')", (last["id"],))
        if open_buys:
            out.append({"kind": "deals", "severity": "medium", "title": f"{open_buys} BUY deals waiting from the latest deal scan",
                        "detail": "Open Find deals to review them before prices change"})

    out.extend(calendar())
    out.sort(key=lambda a: SEVERITY.get(a["severity"], 9))
    return out
