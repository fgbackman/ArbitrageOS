"""Pull sales, fees, refunds and FBA inventory from Amazon into the ledger.

Live mode reads Amazon's financial events, which carry the exact referral and
FBA fee charged per order. Demo mode simulates plausible sales for live lots.
"""
from __future__ import annotations

import hashlib
import random
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from . import config, db, fees as F, labels, ledger, pnl, providers, scoring

FEE_REFERRAL = {"Commission"}
FEE_FBA = {"FBAPerUnitFulfillmentFee", "FBAPerOrderFulfillmentFee", "FBAWeightBasedFee"}


def _amt(x) -> float:
    if not x:
        return 0.0
    return float(x.get("CurrencyAmount", x.get("Amount", 0.0)) or 0.0)


def sync_live(cfg: dict, since: Optional[datetime] = None) -> dict:
    sp = providers.spapi(cfg)
    if not sp:
        raise RuntimeError("Selling Partner API is not configured")
    last = db.meta_get("last_finance_sync")
    since = since or (datetime.fromisoformat(last) if last else datetime.now(timezone.utc) - timedelta(days=30))
    ev = sp.financial_events(since)
    added = 0
    for sh in ev["ShipmentEventList"]:
        oid, posted = sh.get("AmazonOrderId"), sh.get("PostedDate", "")[:19]
        for it in sh.get("ShipmentItemList") or []:
            principal = sum(_amt(c.get("ChargeAmount")) for c in it.get("ItemChargeList") or [] if c.get("ChargeType") == "Principal")
            ref = -sum(_amt(f.get("FeeAmount")) for f in it.get("ItemFeeList") or [] if f.get("FeeType") in FEE_REFERRAL)
            fba = -sum(_amt(f.get("FeeAmount")) for f in it.get("ItemFeeList") or [] if f.get("FeeType") in FEE_FBA)
            other = -sum(_amt(f.get("FeeAmount")) for f in it.get("ItemFeeList") or []
                         if f.get("FeeType") not in FEE_REFERRAL | FEE_FBA)
            if pnl.record_sale({"order_id": oid, "order_item_id": it.get("OrderItemId"), "sold_at": posted,
                                "sku": it.get("SellerSKU"), "asin": None, "qty": it.get("QuantityShipped", 1),
                                "item_price": principal, "referral_fee": ref, "fba_fee": fba, "other_fees": other,
                                "source": "amazon"}):
                added += 1
    refunds = 0
    for rf in ev["RefundEventList"]:
        oid = rf.get("AmazonOrderId")
        for it in rf.get("ShipmentItemAdjustmentList") or []:
            back = -sum(_amt(c.get("ChargeAmount")) for c in it.get("ItemChargeAdjustmentList") or [] if c.get("ChargeType") == "Principal")
            fee_back = sum(_amt(f.get("FeeAmount")) for f in it.get("ItemFeeAdjustmentList") or [])
            with db.tx() as c:
                cur = c.execute("UPDATE sales SET refund = refund + ?, profit = profit - ? WHERE order_id=? AND sku=?",
                                (back, back - fee_back, oid, it.get("SellerSKU")))
                refunds += cur.rowcount
    inv = sp.inventory_summaries()
    with db.tx() as c:
        for s in inv:
            d = s.get("inventoryDetails") or {}
            inbound = sum(d.get(k, 0) or 0 for k in ("inboundWorkingQuantity", "inboundShippedQuantity", "inboundReceivingQuantity"))
            c.execute(
                "INSERT INTO inventory_snapshot(sku, asin, fnsku, fulfillable, inbound, reserved, unfulfillable, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(sku) DO UPDATE SET asin=excluded.asin, fnsku=excluded.fnsku, "
                "fulfillable=excluded.fulfillable, inbound=excluded.inbound, reserved=excluded.reserved, "
                "unfulfillable=excluded.unfulfillable, updated_at=excluded.updated_at",
                (s.get("sellerSku"), s.get("asin"), s.get("fnSku"), d.get("fulfillableQuantity", 0), inbound,
                 (d.get("reservedQuantity") or {}).get("totalReservedQuantity", 0),
                 (d.get("unfulfillableQuantity") or {}).get("totalUnfulfillableQuantity", 0), db.now()))
            if s.get("fnSku"):
                c.execute("UPDATE lots SET fnsku=? WHERE sku=? AND (fnsku IS NULL OR fnsku='')", (s["fnSku"], s.get("sellerSku")))
            if (d.get("fulfillableQuantity") or 0) > 0:
                c.execute("UPDATE lots SET status='live', live_at=COALESCE(live_at, ?) WHERE sku=? AND status IN ('shipped','prepped')",
                          (db.now(), s.get("sellerSku")))
    db.meta_set("last_finance_sync", datetime.now(timezone.utc).isoformat())
    db.log("sync", f"Amazon sync: {added} sale lines, {refunds} refunds, {len(inv)} SKUs")
    return {"sales_added": added, "refunds": refunds, "skus": len(inv)}


def _rng(*parts) -> random.Random:
    return random.Random(int(hashlib.md5("|".join(map(str, parts)).encode()).hexdigest()[:8], 16))


def simulate_demo(cfg: dict, until: Optional[date] = None) -> dict:
    """Demo: shipped lots go live after 5 days; live lots sell at a pace set by the listing's demand."""
    until = until or date.today()
    added = 0
    for l in ledger.lots(active_only=True):
        if not l["fnsku"]:
            ledger.set_lot(l["id"], fnsku=labels.demo_fnsku(l["asin"]))
        if l["status"] == "shipped" and l["created_at"]:
            shipped = datetime.fromisoformat(l["created_at"][:19]).date()
            if (until - shipped).days >= 5:
                ledger.set_lot(l["id"], status="live")
    last = db.meta_get("demo_sim_until")
    start = date.fromisoformat(last) + timedelta(days=1) if last else until - timedelta(days=1)
    lots = [l for l in ledger.lots(status="live") if l["qty_left"] > 0]
    prods = {p.asin: p for p in providers.get_products(sorted({l["asin"] for l in lots}), cfg)} if lots else {}
    d = start
    while d <= until:
        for l in [x for x in ledger.lots(status="live") if x["qty_left"] > 0]:
            p = prods.get(l["asin"])
            if not p:
                continue
            live_since = datetime.fromisoformat((l["live_at"] or l["created_at"])[:19]).date()
            if d < live_since:
                continue
            share = (p.monthly_sold or 30) / ((p.fba_offers or 3) + 1) / 30.0
            r = _rng(l["id"], d.isoformat())
            n = sum(1 for _ in range(4) if r.random() < share / 4)
            n = min(n, l["qty_left"])
            pkg = scoring.package_of(p)
            for k in range(n):
                price = round((p.buybox_price or l["target_price"] or 20) * r.uniform(0.96, 1.01), 2)
                price = max(price, l["min_price"] or 0)
                fb = F.fee_stack(price, pkg, p.category, cfg["costs"], d, p.referral_pct, months_stored=0)
                refund = price if r.random() < 0.015 else 0.0
                if pnl.record_sale({"order_id": f"DEMO-{d.strftime('%y%m%d')}-{l['id']:04d}-{k}", "sold_at": f"{d.isoformat()}T{10 + k:02d}:00:00",
                                    "sku": l["sku"], "asin": l["asin"], "qty": 1, "item_price": price,
                                    "referral_fee": fb.referral, "fba_fee": fb.fulfillment, "other_fees": round(fb.storage, 2),
                                    "refund": refund, "source": "demo"}):
                    added += 1
        d += timedelta(days=1)
    db.meta_set("demo_sim_until", until.isoformat())
    if added:
        db.log("sync", f"demo: simulated {added} sales")
    return {"sales_added": added}


def run(cfg: Optional[dict] = None) -> dict:
    cfg = cfg or config.load()
    if cfg["mode"] == "live" and providers.spapi(cfg):
        return sync_live(cfg)
    return simulate_demo(cfg)
