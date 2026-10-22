"""Purchases, inventory lots and FBA shipments.

Every unit you buy is a lot row tied to its purchase and receipt, so you can
answer an Amazon invoice request for any ASIN in seconds.
"""
from __future__ import annotations

import base64
import json
import re
from datetime import date
from pathlib import Path
from typing import Optional

from . import config, db, fees as F, providers, scoring

STATUSES = ["bought", "prepped", "shipped", "live", "sold_out", "removed"]


def make_sku(asin: str, unit_cost: float, bought_on: str) -> str:
    """SKU encodes date and cost so it reads at a glance: B0XXXX-1015-1299."""
    d = re.sub(r"\D", "", bought_on)[4:8] or date.today().strftime("%m%d")
    base = f"{asin}-{d}-{int(round(unit_cost * 100)):04d}"
    n = db.scalar("SELECT COUNT(*) FROM lots WHERE sku LIKE ?", (base + "%",))
    return base if n == 0 else f"{base}-{n + 1}"


def save_receipt(purchase_id: int, filename: str, content_b64: str) -> str:
    config.ensure_dirs()
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", filename or "receipt")[-80:]
    path = config.RECEIPTS_DIR / f"p{purchase_id:05d}_{safe}"
    path.write_bytes(base64.b64decode(content_b64))
    with db.tx() as c:
        c.execute("UPDATE purchases SET receipt_path=? WHERE id=?", (str(path), purchase_id))
    return str(path)


def landed_unit_cost(unit_cost: float, qty: int, tax: float, cashback: float, shipping: float,
                     pkg: Optional[F.Package], cfg: dict) -> float:
    costs = cfg["costs"]
    per_unit_extra = (tax - cashback + shipping) / max(qty, 1)
    inbound = costs["inbound_min_per_unit"]
    if pkg:
        tier = F.size_tier(pkg)
        inbound = max(costs["inbound_min_per_unit"], costs["inbound_per_lb"] * F.shipping_weight(pkg, tier))
    return round(unit_cost + per_unit_extra + costs["prep_per_unit"] + inbound, 4)


def record_purchase(p: dict, cfg: Optional[dict] = None) -> dict:
    """p = {bought_on, retailer, order_number, receipt_kind, sales_tax, cashback, shipping, payment, notes,
            items: [{asin, qty, unit_cost, upc?, title?, expiry?, lead_id?}]}"""
    cfg = cfg or config.load()
    items = p.get("items") or []
    if not items:
        raise ValueError("a purchase needs at least one item")
    bought_on = p.get("bought_on") or date.today().isoformat()
    total_units = sum(int(i["qty"]) for i in items)
    total_goods = sum(float(i["unit_cost"]) * int(i["qty"]) for i in items)
    tax, cb, ship = float(p.get("sales_tax") or 0), float(p.get("cashback") or 0), float(p.get("shipping") or 0)
    with db.tx() as c:
        cur = c.execute(
            "INSERT INTO purchases(bought_on, retailer, order_number, receipt_kind, sales_tax, cashback, shipping, payment, notes) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (bought_on, p.get("retailer"), p.get("order_number"), p.get("receipt_kind") or "online_order", tax, cb, ship,
             p.get("payment"), p.get("notes")))
        pid = cur.lastrowid
    lot_ids = []
    for i in items:
        asin = i["asin"].strip().upper()
        qty, cost = int(i["qty"]), float(i["unit_cost"])
        prod = providers.get_product(asin, cfg)
        pkg = scoring.package_of(prod) if prod else None
        share = (cost * qty) / total_goods if total_goods else qty / max(total_units, 1)
        landed = landed_unit_cost(cost, qty, tax * share, cb * share, ship * share, pkg, cfg)
        sku = make_sku(asin, cost, bought_on)
        min_p = scoring.min_price_for(landed, pkg, prod.category if prod else None, cfg) if pkg else None
        target = (prod.buybox_price or prod.avg90) if prod else None
        max_p = round(max(target or 0, (prod.avg90 or 0) * 1.35, (min_p or 0) * 1.8), 2) if prod else None
        with db.tx() as c:
            cur = c.execute(
                "INSERT INTO lots(purchase_id, lead_id, asin, sku, title, upc, qty, unit_cost, landed_unit_cost, target_price, "
                "min_price, max_price, expiry, status, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?, 'bought', ?)",
                (pid, i.get("lead_id"), asin, sku, i.get("title") or (prod.title if prod else ""), i.get("upc") or (prod.upc if prod else ""),
                 qty, cost, landed, target, min_p, max_p, i.get("expiry"), db.now()))
            lot_ids.append(cur.lastrowid)
            if i.get("lead_id"):
                c.execute("UPDATE leads SET status='bought' WHERE id=?", (i["lead_id"],))
    db.log("purchase", f"bought {total_units} units from {p.get('retailer') or 'unknown'} for ${total_goods + tax - cb + ship:,.2f}",
           {"purchase_id": pid, "lots": lot_ids})
    return {"purchase_id": pid, "lot_ids": lot_ids}


def lots(status: Optional[str] = None, active_only: bool = False) -> list:
    q = ("SELECT l.*, p.retailer, p.order_number, p.receipt_path, p.receipt_kind, p.bought_on "
         "FROM lots l LEFT JOIN purchases p ON p.id = l.purchase_id WHERE 1=1")
    params = []
    if status:
        q += " AND l.status=?"
        params.append(status)
    if active_only:
        q += " AND l.status NOT IN ('sold_out','removed')"
    q += " ORDER BY l.created_at DESC"
    out = db.rows(q, tuple(params))
    for r in out:
        r["qty_left"] = r["qty"] - r["qty_sold"] - r["qty_lost"]
        r["cost_left"] = round(r["qty_left"] * r["landed_unit_cost"], 2)
    return out


def set_lot(lot_id: int, **fields) -> None:
    allowed = {"status", "fnsku", "min_price", "max_price", "target_price", "expiry", "notes", "qty_lost", "shipment_id"}
    sets = {k: v for k, v in fields.items() if k in allowed}
    if not sets:
        return
    if sets.get("status") and sets["status"] not in STATUSES:
        raise ValueError("unknown status")
    if sets.get("status") == "live":
        sets["live_at"] = db.now()
    cols = ", ".join(f"{k}=?" for k in sets)
    with db.tx() as c:
        c.execute(f"UPDATE lots SET {cols} WHERE id=?", (*sets.values(), lot_id))


def create_shipment(lot_ids: list, name: Optional[str] = None, placement_option: Optional[str] = None) -> int:
    cfg = config.load()
    name = name or f"FBA {date.today().isoformat()} ({len(lot_ids)} SKUs)"
    with db.tx() as c:
        cur = c.execute("INSERT INTO shipments(name, created_at, status, placement_option) VALUES (?,?, 'draft', ?)",
                        (name, db.now(), placement_option or cfg["costs"]["placement_option"]))
        sid = cur.lastrowid
        c.executemany("UPDATE lots SET shipment_id=?, status=CASE WHEN status='bought' THEN 'prepped' ELSE status END WHERE id=?",
                      [(sid, i) for i in lot_ids])
    db.log("shipment", f"created shipment {name}", {"shipment_id": sid, "lots": lot_ids})
    return sid


def set_shipment(sid: int, status: str, amazon_shipment_id: Optional[str] = None, carrier_cost: Optional[float] = None) -> None:
    with db.tx() as c:
        c.execute("UPDATE shipments SET status=?, amazon_shipment_id=COALESCE(?, amazon_shipment_id), "
                  "carrier_cost=COALESCE(?, carrier_cost) WHERE id=?", (status, amazon_shipment_id, carrier_cost, sid))
        lot_status = {"shipped": "shipped", "receiving": "shipped", "closed": "live"}.get(status)
        if lot_status:
            c.execute("UPDATE lots SET status=?, live_at=CASE WHEN ?='live' THEN ? ELSE live_at END "
                      "WHERE shipment_id=? AND status NOT IN ('sold_out','removed')",
                      (lot_status, lot_status, db.now(), sid))


def shipments() -> list:
    out = db.rows("SELECT * FROM shipments ORDER BY created_at DESC")
    for s in out:
        s["lots"] = db.rows("SELECT id, sku, asin, title, qty, fnsku, status FROM lots WHERE shipment_id=?", (s["id"],))
        s["units"] = sum(l["qty"] for l in s["lots"])
    return out


def invoice_pack(asin: str) -> dict:
    """Everything Amazon asks for in an authenticity review, for one ASIN."""
    rows = db.rows(
        "SELECT p.*, l.qty, l.unit_cost, l.sku, l.title FROM lots l JOIN purchases p ON p.id=l.purchase_id "
        "WHERE l.asin=? ORDER BY p.bought_on DESC", (asin.upper(),))
    total = sum(r["qty"] for r in rows)
    with_docs = [r for r in rows if r["receipt_path"]]
    return {"asin": asin.upper(), "purchases": rows, "units_bought": total,
            "documented_units": sum(r["qty"] for r in with_docs),
            "missing_receipts": [r["id"] for r in rows if not r["receipt_path"]],
            "has_invoice": any(r["receipt_kind"] == "invoice" for r in rows)}
