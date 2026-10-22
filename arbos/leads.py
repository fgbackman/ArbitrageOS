"""Sourcing leads: import retailer deals (CSV from Tactical Arbitrage, a lead
list service, or your own sheet), match them to Amazon listings, and score."""
from __future__ import annotations

import csv
import io
import json
import re
from typing import Optional

from . import config, db, providers, scoring

# header synonyms, lowercase with spaces/punctuation stripped
SYNONYMS = {
    "asin": ["asin", "amazonasin", "asin1"],
    "upc": ["upc", "ean", "gtin", "barcode", "upcean", "sourceupc", "productcode", "code"],
    "buy_cost": ["buycost", "cost", "price", "sourceprice", "retailprice", "buyprice", "storeprice", "saleprice", "costperunit"],
    "retailer": ["retailer", "store", "source", "sourcesite", "sourcestore", "website", "site", "supplier"],
    "retailer_url": ["url", "link", "sourceurl", "producturl", "sourcelink", "retailerurl", "buylink"],
    "title": ["title", "name", "product", "productname", "sourcetitle", "description", "amazontitle"],
    "cashback_pct": ["cashback", "cashbackpct", "cashbackpercent", "rebate"],
    "notes": ["notes", "note", "comments", "coupon", "promo"],
}


def _norm(h: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (h or "").lower())


def map_headers(headers: list) -> dict:
    found = {}
    normed = [_norm(h) for h in headers]
    for field, syns in SYNONYMS.items():
        for s in syns:
            if s in normed and field not in found:
                found[field] = headers[normed.index(s)]
                break
    return found


def _money(v) -> Optional[float]:
    if v is None:
        return None
    s = re.sub(r"[^0-9.\-]", "", str(v))
    try:
        return float(s) if s else None
    except ValueError:
        return None


def _pct(v) -> Optional[float]:
    f = _money(v)
    if f is None:
        return None
    return f / 100.0 if f > 1 else f


def parse_csv(text: str) -> tuple:
    text = text.lstrip("﻿")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    headers = reader.fieldnames or []
    m = map_headers(headers)
    if "buy_cost" not in m or not ({"asin", "upc"} & set(m)):
        raise ValueError("CSV needs a cost column and an ASIN or UPC column. Found: " + ", ".join(headers))
    out = []
    for row in reader:
        rec = {f: (row.get(col) or "").strip() for f, col in m.items()}
        cost = _money(rec.get("buy_cost"))
        if not cost or cost <= 0:
            continue
        out.append({
            "asin": (rec.get("asin") or "").upper() or None,
            "upc": re.sub(r"\D", "", rec.get("upc") or "") or None,
            "buy_cost": cost,
            "retailer": rec.get("retailer") or None,
            "retailer_url": rec.get("retailer_url") or None,
            "title": rec.get("title") or None,
            "cashback_pct": _pct(rec.get("cashback_pct")),
            "notes": rec.get("notes") or None,
        })
    return out, m


def add(leads: list, source: str = "manual") -> list:
    ids = []
    with db.tx() as c:
        for l in leads:
            cur = c.execute(
                "INSERT INTO leads(created_at, source, retailer, retailer_url, title, upc, asin, buy_cost, cashback_pct, notes) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (db.now(), source, l.get("retailer"), l.get("retailer_url"), l.get("title"), l.get("upc"),
                 l.get("asin"), l["buy_cost"], l.get("cashback_pct"), l.get("notes")))
            ids.append(cur.lastrowid)
    return ids


def upsert(l: dict, source: str, scan_id: Optional[int] = None) -> int:
    """Insert a lead, or refresh the open lead for the same item and retailer (repeat scans don't duplicate)."""
    key_col, key = ("asin", l["asin"]) if l.get("asin") else ("upc", l.get("upc"))
    existing = db.one(f"SELECT id FROM leads WHERE {key_col}=? AND COALESCE(retailer,'')=COALESCE(?,'') "
                      "AND status IN ('new','scored') ORDER BY id DESC LIMIT 1", (key, l.get("retailer")))
    with db.tx() as c:
        if existing:
            c.execute("UPDATE leads SET buy_cost=?, retailer_url=COALESCE(?, retailer_url), title=COALESCE(?, title), "
                      "cashback_pct=?, regular_price=?, scan_id=?, flip=?, source=?, status='new' WHERE id=?",
                      (l["buy_cost"], l.get("retailer_url"), l.get("title"), l.get("cashback_pct"), l.get("regular_price"),
                       scan_id, 1 if l.get("flip") else 0, source, existing["id"]))
            return existing["id"]
        cur = c.execute(
            "INSERT INTO leads(created_at, source, retailer, retailer_url, title, upc, asin, buy_cost, cashback_pct, notes, "
            "regular_price, scan_id, flip) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (db.now(), source, l.get("retailer"), l.get("retailer_url"), l.get("title"), l.get("upc"), l.get("asin"),
             l["buy_cost"], l.get("cashback_pct"), l.get("notes"), l.get("regular_price"), scan_id, 1 if l.get("flip") else 0))
        return cur.lastrowid


def import_csv(text: str, source: str = "csv") -> dict:
    parsed, mapping = parse_csv(text)
    ids = add(parsed, source)
    db.log("leads", f"imported {len(ids)} leads from {source}")
    return {"imported": len(ids), "ids": ids, "columns": mapping}


def available_capital(cfg: dict) -> float:
    from . import pnl
    return pnl.position(cfg)["cash"]


def score(ids: Optional[list] = None, cfg: Optional[dict] = None, rescore: bool = False, progress=None) -> dict:
    """Match leads to ASINs (by UPC if needed) and evaluate each one."""
    cfg = cfg or config.load()
    where = "status IN ('new','scored')" if rescore else "status='new'"
    params = ()
    if ids:
        where = f"id IN ({','.join('?' * len(ids))})"
        params = tuple(ids)
    todo = db.rows(f"SELECT * FROM leads WHERE {where}", params)
    if not todo:
        return {"scored": 0}
    # resolve UPCs in one batch
    codes = [l["upc"] for l in todo if not l["asin"] and l["upc"]]
    matches = providers.lookup_codes(codes, cfg) if codes else {}
    asins = set()
    expanded = []
    for l in todo:
        if l["asin"]:
            expanded.append((l, l["asin"]))
            asins.add(l["asin"])
        else:
            hits = matches.get(l["upc"] or "", [])
            if not hits:
                expanded.append((l, None))
            for h in hits[:1]:  # best (first) match; others visible in the product page
                expanded.append((l, h.asin))
                asins.add(h.asin)
    prods = {p.asin: p for p in providers.get_products(sorted(asins), cfg)} if asins else {}
    capital = available_capital(cfg)
    counts = {"BUY": 0, "MAYBE": 0, "PASS": 0, "UNKNOWN": 0, "NO_MATCH": 0}
    with db.tx() as c:
        for l, asin in expanded:
            if not asin or asin not in prods:
                c.execute("UPDATE leads SET status='scored', verdict='NO_MATCH', evaluation=? WHERE id=?",
                          (json.dumps({"reason": "no Amazon listing found for this UPC"}), l["id"]))
                counts["NO_MATCH"] += 1
                continue
            ev = scoring.evaluate(prods[asin], l["buy_cost"], cfg, cashback_pct=l["cashback_pct"], capital=capital,
                                  flip=bool(l.get("flip")))
            counts[ev["verdict"]] = counts.get(ev["verdict"], 0) + 1
            c.execute(
                "UPDATE leads SET asin=?, status='scored', verdict=?, profit=?, roi=?, max_buy_cost=?, monthly_sales=?, "
                "suggested_qty=?, evaluation=?, title=COALESCE(title, ?), margin=?, monthly_profit=? WHERE id=?",
                (asin, ev["verdict"], ev.get("profit"), ev.get("roi"), ev.get("max_buy_cost"), ev.get("monthly_sales_est"),
                 ev.get("suggested_qty"), json.dumps(ev), prods[asin].title, ev.get("margin"), ev.get("monthly_profit"), l["id"]))
    db.log("leads", f"scored {len(expanded)} leads", counts)
    return {"scored": len(expanded), "counts": counts}


def list_leads(status: Optional[str] = None, verdict: Optional[str] = None, limit: int = 500) -> list:
    q, p = "SELECT * FROM leads WHERE 1=1", []
    if status:
        q += " AND status=?"
        p.append(status)
    if verdict:
        q += " AND verdict=?"
        p.append(verdict)
    q += " ORDER BY CASE verdict WHEN 'BUY' THEN 0 WHEN 'MAYBE' THEN 1 WHEN 'PASS' THEN 2 ELSE 3 END, roi DESC LIMIT ?"
    p.append(limit)
    out = db.rows(q, tuple(p))
    for r in out:
        r["evaluation"] = json.loads(r["evaluation"]) if r.get("evaluation") else None
    return out


def set_status(lead_id: int, status: str) -> None:
    with db.tx() as c:
        c.execute("UPDATE leads SET status=? WHERE id=?", (status, lead_id))
