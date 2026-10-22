"""Sales, profit and the road to the monthly goal."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional

from . import config, db


def _month(s: str) -> str:
    return s[:7]


def allocate_cogs(sku: str, qty: int) -> float:
    """FIFO: take units from the oldest live lots of this SKU (or ASIN)."""
    lots = db.rows("SELECT id, qty, qty_sold, qty_lost, landed_unit_cost FROM lots WHERE sku=? "
                   "AND status NOT IN ('removed') ORDER BY created_at, id", (sku,))
    if not lots:
        asin = sku.split("-")[0]
        lots = db.rows("SELECT id, qty, qty_sold, qty_lost, landed_unit_cost FROM lots WHERE asin=? "
                       "AND status NOT IN ('removed') ORDER BY created_at, id", (asin,))
    need, cogs = qty, 0.0
    with db.tx() as c:
        for l in lots:
            left = l["qty"] - l["qty_sold"] - l["qty_lost"]
            if left <= 0:
                continue
            take = min(left, need)
            cogs += take * l["landed_unit_cost"]
            new_sold = l["qty_sold"] + take
            status_sql = ", status='sold_out'" if new_sold + l["qty_lost"] >= l["qty"] else ""
            c.execute(f"UPDATE lots SET qty_sold=?{status_sql} WHERE id=?", (new_sold, l["id"]))
            need -= take
            if need == 0:
                break
    if need and lots:  # oversold vs ledger: cost the rest at the last known cost
        cogs += need * lots[-1]["landed_unit_cost"]
    return round(cogs, 2)


def record_sale(s: dict) -> Optional[int]:
    """s = {order_id, order_item_id?, sold_at, sku, asin, qty, item_price, referral_fee, fba_fee, other_fees, refund, source}"""
    exists = db.one("SELECT id FROM sales WHERE order_id=? AND sku=? AND COALESCE(order_item_id,'')=COALESCE(?, '')",
                    (s["order_id"], s["sku"], s.get("order_item_id")))
    if exists:
        return None
    qty = int(s.get("qty", 1))
    cogs = allocate_cogs(s["sku"], qty)
    fees = float(s.get("referral_fee") or 0) + float(s.get("fba_fee") or 0) + float(s.get("other_fees") or 0)
    profit = round(float(s["item_price"]) - fees - float(s.get("refund") or 0) - cogs, 2)
    with db.tx() as c:
        cur = c.execute(
            "INSERT INTO sales(order_id, order_item_id, sold_at, sku, asin, qty, item_price, referral_fee, fba_fee, other_fees, "
            "refund, cogs, profit, source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (s["order_id"], s.get("order_item_id"), s["sold_at"], s["sku"], s.get("asin"), qty, float(s["item_price"]),
             float(s.get("referral_fee") or 0), float(s.get("fba_fee") or 0), float(s.get("other_fees") or 0),
             float(s.get("refund") or 0), cogs, profit, s.get("source", "manual")))
        return cur.lastrowid


def add_expense(spent_on: str, category: str, vendor: str, amount: float, notes: str = "") -> int:
    with db.tx() as c:
        return c.execute("INSERT INTO expenses(spent_on, category, vendor, amount, notes) VALUES (?,?,?,?,?)",
                         (spent_on, category, vendor, amount, notes)).lastrowid


def add_cash(moved_on: str, kind: str, amount: float, notes: str = "") -> int:
    with db.tx() as c:
        return c.execute("INSERT INTO cash_moves(moved_on, kind, amount, notes) VALUES (?,?,?,?)",
                         (moved_on, kind, amount, notes)).lastrowid


def position(cfg: Optional[dict] = None) -> dict:
    """Where the money is right now."""
    cfg = cfg or config.load()
    start = cfg["business"]["starting_capital"]
    spent_goods = db.scalar("SELECT SUM(l.qty * l.landed_unit_cost) FROM lots l", default=0.0)
    proceeds = db.scalar("SELECT SUM(item_price - referral_fee - fba_fee - other_fees - refund) FROM sales", default=0.0)
    expenses = db.scalar("SELECT SUM(amount) FROM expenses", default=0.0)
    moves = db.scalar("SELECT SUM(CASE WHEN kind IN ('deposit') THEN amount WHEN kind IN ('withdrawal','owner_draw') THEN -amount ELSE 0 END) FROM cash_moves", default=0.0)
    inv_cost = db.scalar("SELECT SUM((qty - qty_sold - qty_lost) * landed_unit_cost) FROM lots WHERE status NOT IN ('removed')", default=0.0)
    units_left = db.scalar("SELECT SUM(qty - qty_sold - qty_lost) FROM lots WHERE status NOT IN ('removed')", default=0)
    cash = start + moves + proceeds - spent_goods - expenses
    by_status = {r["status"]: r for r in db.rows(
        "SELECT status, SUM(qty - qty_sold - qty_lost) AS units, SUM((qty - qty_sold - qty_lost) * landed_unit_cost) AS cost "
        "FROM lots GROUP BY status")}
    return {
        "cash": round(cash, 2),
        "inventory_at_cost": round(inv_cost, 2),
        "units_on_hand": int(units_left or 0),
        "capital": round(cash + inv_cost, 2),
        "by_status": by_status,
        "starting_capital": start,
    }


def monthly(months: int = 12) -> list:
    sales = db.rows(
        "SELECT substr(sold_at,1,7) AS month, SUM(qty) AS units, SUM(item_price) AS revenue, "
        "SUM(referral_fee + fba_fee + other_fees) AS fees, SUM(refund) AS refunds, SUM(cogs) AS cogs, SUM(profit) AS gross_profit "
        "FROM sales GROUP BY month ORDER BY month")
    exp = {r["month"]: r["amount"] for r in db.rows(
        "SELECT substr(spent_on,1,7) AS month, SUM(amount) AS amount FROM expenses GROUP BY month")}
    buys = {r["month"]: r["amount"] for r in db.rows(
        "SELECT substr(p.bought_on,1,7) AS month, SUM(l.qty * l.landed_unit_cost) AS amount "
        "FROM lots l JOIN purchases p ON p.id=l.purchase_id GROUP BY month")}
    by = {r["month"]: r for r in sales}
    all_months = sorted(set(by) | set(exp) | set(buys))[-months:]
    out = []
    for m in all_months:
        r = by.get(m, {})
        gp = r.get("gross_profit") or 0.0
        e = exp.get(m, 0.0)
        cogs = r.get("cogs") or 0.0
        out.append({
            "month": m, "units": int(r.get("units") or 0), "revenue": round(r.get("revenue") or 0, 2),
            "fees": round(r.get("fees") or 0, 2), "refunds": round(r.get("refunds") or 0, 2),
            "cogs": round(cogs, 2), "expenses": round(e, 2), "net_profit": round(gp - e, 2),
            "roi": round(gp / cogs, 4) if cogs else None, "inventory_bought": round(buys.get(m, 0.0), 2),
        })
    return out


def sku_performance(limit: int = 200) -> list:
    return db.rows(
        "SELECT s.sku, s.asin, MAX(l.title) AS title, SUM(s.qty) AS units, SUM(s.item_price) AS revenue, SUM(s.profit) AS profit, "
        "SUM(s.cogs) AS cogs, ROUND(SUM(s.profit) / NULLIF(SUM(s.cogs),0), 4) AS roi, MIN(s.sold_at) AS first_sale, MAX(s.sold_at) AS last_sale "
        "FROM sales s LEFT JOIN lots l ON l.sku = s.sku GROUP BY s.sku ORDER BY profit DESC LIMIT ?", (limit,))


def recent_sales(limit: int = 200) -> list:
    return db.rows("SELECT s.*, (SELECT title FROM lots WHERE sku=s.sku LIMIT 1) AS title FROM sales s "
                   "ORDER BY sold_at DESC LIMIT ?", (limit,))


def turn_rate(days: int = 60) -> Optional[float]:
    """Inventory turns per month: cost of goods sold / average inventory at cost.

    Average inventory is approximated as stock on hand now plus half of what
    sold in the window (that stock was on hand for part of it)."""
    since_s = (date.today() - timedelta(days=days)).isoformat()
    cogs = db.scalar("SELECT SUM(cogs) FROM sales WHERE sold_at >= ?", (since_s,), default=0.0)
    inv = db.scalar("SELECT SUM((qty - qty_sold - qty_lost) * landed_unit_cost) FROM lots WHERE status NOT IN ('removed','sold_out')", default=0.0)
    if cogs <= 0:
        return None
    avg_inv = inv + cogs / 2.0
    monthly_cogs = cogs * 30.0 / days
    return round(min(1.5, max(0.05, monthly_cogs / avg_inv)), 3)


def goal(cfg: Optional[dict] = None) -> dict:
    """Progress to the monthly profit goal and a forecast from your own numbers."""
    cfg = cfg or config.load()
    target = cfg["business"]["monthly_profit_goal"]
    this_month = date.today().isoformat()[:7]
    m = {r["month"]: r for r in monthly(24)}
    cur = m.get(this_month, {"net_profit": 0.0})
    day = date.today().day
    days_in = (date(date.today().year + (date.today().month // 12), date.today().month % 12 + 1, 1) - date.today().replace(day=1)).days
    pace = cur["net_profit"] / day * days_in if day else 0.0
    # observed ROI and turn rate (fallback to plan base case)
    sold = db.one("SELECT SUM(profit) AS p, SUM(cogs) AS c FROM sales")
    roi = (sold["p"] / sold["c"]) if sold and sold["c"] else 0.33
    turns = turn_rate() or 0.70
    pos = position(cfg)
    capital = pos["capital"]
    tools = cfg["costs"]["monthly_tool_costs"]
    months, cap, path = 0, capital, []
    while months < 36:
        months += 1
        buys = min(cap * turns, 12000 if months < 6 else 40000)
        prof = buys * roi - tools
        cap += max(prof, 0)
        path.append({"month": months, "profit": round(prof, 2), "capital": round(cap, 2)})
        if prof >= target:
            break
    reached = path[-1]["profit"] >= target if path else False
    return {
        "target": target, "month": this_month, "profit_this_month": round(cur["net_profit"], 2),
        "pace_this_month": round(pace, 2), "pct_of_goal": round(cur["net_profit"] / target, 4) if target else 0,
        "observed_roi": round(roi, 4), "turns_per_month": round(turns, 3), "capital": capital,
        "months_to_goal": months if reached else None, "forecast": path,
        "capital_needed": round(target / max(roi, 0.01) / max(turns, 0.05), 0),
    }
