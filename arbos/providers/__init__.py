"""Data sources, with a local product cache so repeat lookups cost no Keepa tokens."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Optional

from .. import config, db
from .base import ProductData, Provider
from .demo import DemoProvider
from .keepa import KeepaError, KeepaProvider
from .spapi import SPAPI, SPAPIError

CACHE_HOURS = 6


def catalog_provider(cfg: Optional[dict] = None) -> Provider:
    cfg = cfg or config.load()
    if cfg["mode"] == "live" and cfg["keepa"]["api_key"]:
        return KeepaProvider(cfg["keepa"]["api_key"], cfg["keepa"].get("domain", 1))
    return DemoProvider()


def spapi(cfg: Optional[dict] = None) -> Optional[SPAPI]:
    cfg = cfg or config.load()
    if cfg["mode"] != "live":
        return None
    try:
        return SPAPI(cfg)
    except SPAPIError:
        return None


def _fresh(fetched_at: str, hours: float) -> bool:
    try:
        t = datetime.fromisoformat(fetched_at)
    except (TypeError, ValueError):
        return False
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - t < timedelta(hours=hours)


def _store(p: ProductData) -> None:
    with db.tx() as c:
        c.execute("INSERT INTO products(asin, data, source, fetched_at) VALUES (?,?,?,?) "
                  "ON CONFLICT(asin) DO UPDATE SET data=excluded.data, source=excluded.source, fetched_at=excluded.fetched_at",
                  (p.asin, json.dumps(p.to_dict()), p.source, p.fetched_at))
        if p.upc:
            c.execute("INSERT OR IGNORE INTO upc_map(upc, asin) VALUES (?,?)", (p.upc, p.asin))


def cached(asin: str) -> Optional[ProductData]:
    r = db.one("SELECT data FROM products WHERE asin=?", (asin,))
    return ProductData.from_dict(json.loads(r["data"])) if r else None


def _add_gating(prods: list, cfg: dict) -> None:
    sp = spapi(cfg)
    if not sp:
        return
    for p in prods:
        if p.gated is None:
            try:
                p.gated = sp.restrictions(p.asin)["gated"]
            except SPAPIError:
                pass


def get_products(asins: list, cfg: Optional[dict] = None, max_age_hours: float = CACHE_HOURS) -> list:
    cfg = cfg or config.load()
    prov = catalog_provider(cfg)
    want, out = [], {}
    for a in dict.fromkeys(x.strip().upper() for x in asins if x and x.strip()):
        c = cached(a)
        if c and c.source == prov.name and _fresh(c.fetched_at, max_age_hours):
            out[a] = c
        else:
            want.append(a)
    if want:
        got = prov.get_products(want)
        _add_gating(got, cfg)
        for p in got:
            _store(p)
            out[p.asin] = p
    return [out[a] for a in dict.fromkeys(x.strip().upper() for x in asins if x and x.strip()) if a in out]


def get_product(asin: str, cfg: Optional[dict] = None, max_age_hours: float = CACHE_HOURS) -> Optional[ProductData]:
    got = get_products([asin], cfg, max_age_hours)
    return got[0] if got else None


def lookup_codes(codes: list, cfg: Optional[dict] = None) -> dict:
    cfg = cfg or config.load()
    prov = catalog_provider(cfg)
    res, want = {}, []
    for c in dict.fromkeys(x.strip() for x in codes if x and x.strip()):
        mapped = db.rows("SELECT asin FROM upc_map WHERE upc=?", (c,))
        hits = [cached(m["asin"]) for m in mapped]
        hits = [h for h in hits if h and h.source == prov.name and _fresh(h.fetched_at, CACHE_HOURS)]
        if hits:
            res[c] = hits
        else:
            want.append(c)
    if want:
        got = prov.lookup_codes(want)
        for code, prods in got.items():
            _add_gating(prods, cfg)
            for p in prods:
                _store(p)
                with db.tx() as cx:
                    cx.execute("INSERT OR IGNORE INTO upc_map(upc, asin) VALUES (?,?)", (code, p.asin))
            res[code] = prods
    return res


__all__ = ["ProductData", "KeepaError", "SPAPIError", "catalog_provider", "spapi", "get_products",
           "get_product", "lookup_codes", "cached"]
