"""Min/max price rules for every live SKU, exported for Amazon Automate
Pricing, BQool or Aura, or pushed straight to Amazon when the SP-API is connected."""
from __future__ import annotations

import csv
import io
from datetime import date
from typing import Optional

from . import config, db, ledger, providers, scoring


def plan(cfg: Optional[dict] = None, refresh_market: bool = False) -> list:
    cfg = cfg or config.load()
    active = [l for l in ledger.lots(active_only=True) if l["qty_left"] > 0]
    asins = sorted({l["asin"] for l in active})
    prods = {p.asin: p for p in providers.get_products(asins, cfg, max_age_hours=0 if refresh_market else 6)} if asins else {}
    out = []
    for l in active:
        p = prods.get(l["asin"])
        pkg = scoring.package_of(p) if p else None
        floor = l["min_price"] or (scoring.min_price_for(l["landed_unit_cost"], pkg, p.category, cfg) if pkg else None)
        market = (p.buybox_price or p.new_price) if p else None
        ceiling = l["max_price"] or (round(max(market or 0, (p.avg90 or 0) * 1.35, (floor or 0) * 1.8), 2) if p else None)
        # starting price: market, but never under the floor
        price = max(floor or 0, market or l["target_price"] or ceiling or 0)
        status = "ok"
        if market and floor and market < floor:
            status = "market below floor"
        elif p and p.amazon_price and market and p.amazon_price <= market:
            status = "Amazon on listing"
        out.append({
            "lot_id": l["id"], "sku": l["sku"], "asin": l["asin"], "title": l["title"], "qty_left": l["qty_left"],
            "landed_cost": round(l["landed_unit_cost"], 2), "min_price": floor, "max_price": ceiling,
            "market_price": market, "price": round(price, 2) if price else None, "status": status,
            "avg90": p.avg90 if p else None, "lot_status": l["status"],
        })
    return out


def export_csv(rows: list, kind: str = "amazon") -> str:
    buf = io.StringIO()
    if kind == "amazon":  # Seller Central "Price and Quantity" template columns
        w = csv.writer(buf, delimiter="\t", lineterminator="\n")
        w.writerow(["sku", "price", "minimum-seller-allowed-price", "maximum-seller-allowed-price", "quantity", "fulfillment-channel"])
        for r in rows:
            w.writerow([r["sku"], f"{r['price']:.2f}" if r["price"] else "", f"{r['min_price']:.2f}" if r["min_price"] else "",
                        f"{r['max_price']:.2f}" if r["max_price"] else "", "", "AMAZON_NA"])
    else:  # generic repricer import: BQool, Aura, RepricerExpress all map these
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(["SKU", "ASIN", "Cost", "Min Price", "Max Price", "Price"])
        for r in rows:
            w.writerow([r["sku"], r["asin"], f"{r['landed_cost']:.2f}", r["min_price"] or "", r["max_price"] or "", r["price"] or ""])
    return buf.getvalue()


def save_export(kind: str = "amazon") -> str:
    rows = plan()
    text = export_csv(rows, kind)
    config.ensure_dirs()
    ext = "txt" if kind == "amazon" else "csv"
    path = config.EXPORTS_DIR / f"prices_{kind}_{date.today().isoformat()}.{ext}"
    path.write_text(text)
    return str(path)


def push(rows: Optional[list] = None, cfg: Optional[dict] = None) -> dict:
    cfg = cfg or config.load()
    sp = providers.spapi(cfg)
    if not sp:
        return {"pushed": 0, "error": "connect the Selling Partner API in Settings (live mode) to push prices"}
    rows = rows or plan(cfg)
    ok, errors = 0, []
    for r in rows:
        if not (r["price"] and r["min_price"] and r["max_price"]):
            continue
        try:
            sp.set_price_bounds(r["sku"], "PRODUCT", r["price"], r["min_price"], r["max_price"])
            ok += 1
        except Exception as e:  # keep going; report per SKU
            errors.append({"sku": r["sku"], "error": str(e)})
    db.log("reprice", f"pushed prices for {ok} SKUs", {"errors": errors[:20]})
    return {"pushed": ok, "errors": errors}
