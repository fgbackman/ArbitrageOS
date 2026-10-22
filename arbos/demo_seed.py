"""Fill the database with three months of made-up history so every screen
has something to show. Wipe it any time from Settings."""
from __future__ import annotations

from datetime import date, timedelta

from . import config, db, labels, ledger, leads, pnl, sync
from .providers.demo import DEMO_LEADS

DEMO_TAG = "demo"


def wipe() -> None:
    with db.tx() as c:
        for t in ("sales", "lots", "purchases", "shipments", "leads", "expenses", "cash_moves", "inventory_snapshot",
                  "price_watch", "events", "products", "upc_map"):
            c.execute(f"DELETE FROM {t}")
        c.execute("DELETE FROM meta WHERE key IN ('demo_sim_until','demo_seeded','last_finance_sync')")
    db.log("system", "all data wiped")


def seed(today: date = None) -> dict:
    cfg = config.load()
    today = today or date.today()
    wipe()
    # 1. leads from the demo retailers, scored
    leads.add([{"retailer": r, "retailer_url": u, "title": t, "upc": upc, "buy_cost": c, "cashback_pct": cb}
               for r, u, t, upc, c, cb in DEMO_LEADS], source=DEMO_TAG)
    leads.score(cfg=cfg)
    good = [l for l in leads.list_leads(status="scored") if l["verdict"] in ("BUY", "MAYBE")]
    # 2. history: three buying rounds, each shipped and live a week later
    rounds = [(today - timedelta(days=84), good[0:4]), (today - timedelta(days=49), good[2:8]),
              (today - timedelta(days=21), good[4:11]), (today - timedelta(days=3), good[8:12])]
    for n, (d, batch) in enumerate(rounds):
        if not batch:
            continue
        items = [{"asin": l["asin"], "qty": max(4, min(18, (l["suggested_qty"] or 6) + 2 * n)), "unit_cost": l["buy_cost"],
                  "title": l["evaluation"]["product"]["title"], "lead_id": l["id"] if n == len(rounds) - 1 else None}
                 for l in batch]
        res = ledger.record_purchase({"bought_on": d.isoformat(), "retailer": batch[0]["retailer"],
                                      "order_number": f"DEMO-{d.strftime('%m%d')}-{n + 1}", "receipt_kind": "online_order",
                                      "cashback": round(sum(i["unit_cost"] * i["qty"] for i in items) * 0.02, 2),
                                      "notes": "demo purchase", "items": items}, cfg)
        live = d + timedelta(days=9)
        with db.tx() as c:
            for lid in res["lot_ids"]:
                status = "live" if live <= today else ("shipped" if d + timedelta(days=3) <= today else "bought")
                c.execute("UPDATE lots SET created_at=?, live_at=?, status=? WHERE id=?",
                          (f"{d.isoformat()}T12:00:00", f"{live.isoformat()}T09:00:00" if status == "live" else None,
                           status, lid))
        for lid in res["lot_ids"]:
            lot = db.one("SELECT asin FROM lots WHERE id=?", (lid,))
            ledger.set_lot(lid, fnsku=labels.demo_fnsku(lot["asin"]))
    # receipts: leave the newest purchase without one so the alert shows
    with db.tx() as c:
        c.execute("UPDATE purchases SET receipt_path='demo-receipt.pdf' WHERE id < (SELECT MAX(id) FROM purchases)")
    # 3. tool subscriptions
    for m in range(3, -1, -1):
        d = (today.replace(day=1) - timedelta(days=30 * m)).replace(day=3)
        pnl.add_expense(d.isoformat(), "subscription", "Amazon Professional plan", 39.99, "demo")
        pnl.add_expense(d.isoformat(), "subscription", "SellerAmp SAS", 19.95, "demo")
        if m < 2:
            pnl.add_expense(d.isoformat(), "subscription", "Keepa API", 53.00, "demo")
    # 4. simulate sales from the first go-live to today
    db.meta_set("demo_sim_until", (rounds[0][0] + timedelta(days=8)).isoformat())
    res = sync.simulate_demo(cfg, until=today)
    db.meta_set("demo_seeded", today.isoformat())
    db.log("system", "demo data loaded", res)
    return {"sales": res["sales_added"], "leads": len(DEMO_LEADS)}
