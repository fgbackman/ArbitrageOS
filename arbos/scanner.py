"""One-click deal finder.

Pulls discounted products from every connected deal source, matches each
one to its Amazon listing, runs the full fee stack and buy filter, and ranks
the results. Runs in a background thread with progress and cancel.

Sources
  demo      fictional retailer deals (demo mode only)
  bestbuy   Best Buy Products API: on-sale and clearance items with UPCs (free key)
  flips     Keepa deals: Amazon's own price drops to buy now and resell when
            the price recovers to its 90-day average (needs Keepa key)
  feeds     CSV lead-list URLs you subscribe to (any format leads.py reads)
"""
from __future__ import annotations

import hashlib
import json
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from typing import Callable, Optional

from . import config, db, leads, providers
from .providers.demo import CATALOG, DEMO_LEADS

_lock = threading.Lock()
STATE: dict = {"status": "idle"}
KEEPA_TOKENS_PER_ITEM = 3  # UPC lookup with stats + Buy Box


class Cancelled(Exception):
    pass


# ------------------------------------------------------------------ sources
def sources(cfg: Optional[dict] = None) -> list:
    cfg = cfg or config.load()
    live = cfg["mode"] == "live"
    keepa = bool(cfg["keepa"]["api_key"])
    bb = bool(cfg.get("deal_sources", {}).get("bestbuy_api_key"))
    feeds = [u for u in cfg.get("deal_sources", {}).get("feed_urls", []) if u.strip()]
    out = []
    if not live:
        out.append({"key": "demo", "name": "Demo retailer deals", "ready": True,
                    "note": "fictional deals so you can try the finder"})
    out.append({"key": "bestbuy", "name": "Best Buy sale and clearance", "ready": live and bb and keepa,
                "note": "needs a free Best Buy API key and Keepa" if not (bb and keepa) else ("switch to live mode" if not live else "on-sale items with UPCs")})
    out.append({"key": "flips", "name": "Amazon price drops (flips)", "ready": live and keepa,
                "note": "needs Keepa and live mode" if not (live and keepa) else "buy from Amazon's drop, resell at the 90-day average"})
    out.append({"key": "feeds", "name": f"Your lead feeds ({len(feeds)})", "ready": live and keepa and bool(feeds),
                "note": "add CSV feed URLs in Settings" if not feeds else ("needs Keepa and live mode" if not (live and keepa) else "lead-list CSVs")})
    return out


def _demo_candidates(n: int, min_disc: float) -> list:
    """Fictional retailer deals: catalog items at a rotating discount per day."""
    rng = random.Random(int(hashlib.md5(date.today().isoformat().encode()).hexdigest()[:8], 16))
    stores = ["Walmart", "Target", "Best Buy", "Kohl's", "Walgreens", "Home Depot", "CVS", "Lowe's"]
    out = []
    for p in CATALOG.values():
        for _ in range(rng.choice([1, 1, 2])):
            disc = rng.uniform(0.25, 0.72)
            regular = round(p.buybox_price * rng.uniform(0.95, 1.1), 2)
            cost = round(regular * (1 - disc), 2)
            if disc < min_disc:
                continue
            store = rng.choice(stores)
            out.append({"upc": p.upc, "buy_cost": cost, "regular_price": regular, "retailer": store,
                        "retailer_url": next((u for r, u, *_ in DEMO_LEADS if r == store), "https://www.example.com/"),
                        "title": p.title, "cashback_pct": None})
    for k in range(3):  # a few that will not match an Amazon listing
        out.append({"upc": f"99999990000{k}", "buy_cost": 9.99, "regular_price": 19.99, "retailer": "Target",
                    "retailer_url": "https://www.target.com/", "title": "Store-brand item (no Amazon match)"})
    rng.shuffle(out)
    return out[:n]


def _bestbuy_candidates(cfg: dict, n: int, min_disc: float, max_price: float, log: Callable) -> list:
    key = cfg["deal_sources"]["bestbuy_api_key"]
    filt = f"onSale=true&percentSavings>={int(min_disc * 100)}&salePrice>=8&salePrice<={int(max_price)}&onlineAvailability=true"
    show = "sku,upc,name,salePrice,regularPrice,percentSavings,url,manufacturer,clearance"
    out, page = [], 1
    while len(out) < n and page <= 10:
        q = urllib.parse.urlencode({"apiKey": key, "format": "json", "show": show, "pageSize": 100, "page": page,
                                    "sort": "percentSavings.dsc"})
        url = f"https://api.bestbuy.com/v1/products({urllib.parse.quote(filt, safe='=&<>')})?{q}"
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "ArbitrageOS/1.0"}), timeout=30) as r:
                data = json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"Best Buy API refused the request (HTTP {e.code}); check the API key in Settings")
        except urllib.error.URLError as e:
            raise RuntimeError(f"cannot reach Best Buy: {e.reason}")
        for p in data.get("products") or []:
            if not p.get("upc"):
                continue
            out.append({"upc": str(p["upc"]), "buy_cost": float(p["salePrice"]), "regular_price": p.get("regularPrice"),
                        "retailer": "Best Buy", "retailer_url": p.get("url"), "title": p.get("name"), "cashback_pct": None,
                        "notes": "clearance" if p.get("clearance") else None})
        if page >= (data.get("totalPages") or 1):
            break
        page += 1
        time.sleep(0.25)  # Best Buy allows 5 requests a second
    log(f"Best Buy: {len(out)} on-sale items with UPCs")
    return out[:n]


def _flip_candidates(cfg: dict, n: int, min_disc: float, max_price: float, log: Callable) -> list:
    kp = providers.catalog_provider(cfg)
    deals = kp.deals(min_drop_pct=max(10, int(min_disc * 100)), max_rank=150000, price_type=0,
                     price_range=(800, int(max_price * 100)))
    out = []
    for d in deals[: n]:
        cur = (d.get("current") or [None])[0]
        if not cur or cur <= 0:
            continue
        out.append({"asin": d["asin"], "buy_cost": round(cur / 100.0, 2), "retailer": "Amazon",
                    "retailer_url": f"https://www.amazon.com/dp/{d['asin']}", "title": d.get("title"),
                    "cashback_pct": 0.0, "flip": True, "notes": "Amazon price drop"})
    log(f"Keepa: {len(out)} Amazon price drops")
    return out


def _feed_candidates(cfg: dict, n: int, log: Callable) -> list:
    out = []
    for url in cfg["deal_sources"].get("feed_urls", []):
        url = url.strip()
        if not url:
            continue
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "ArbitrageOS/1.0"}), timeout=60) as r:
                text = r.read().decode("utf-8-sig", errors="replace")
            rows, _ = leads.parse_csv(text)
            out.extend(rows)
            log(f"feed {urllib.parse.urlparse(url).netloc}: {len(rows)} leads")
        except Exception as e:  # one bad feed shouldn't stop the scan
            log(f"feed {url[:60]} failed: {e}")
    return out[:n]


# --------------------------------------------------------------------- scan
def _set(**kw):
    with _lock:
        STATE.update(kw)


def status() -> dict:
    with _lock:
        s = dict(STATE)
    s.pop("_cancel", None)
    return s


def cancel() -> None:
    _set(_cancel=True, stage="Stopping…")


def _check_cancel():
    if STATE.get("_cancel"):
        raise Cancelled()


def run(opts: Optional[dict] = None, cfg: Optional[dict] = None) -> dict:
    """Blocking scan. opts: sources [keys], max_items, min_discount (0-1), max_price."""
    cfg = cfg or config.load()
    opts = opts or {}
    avail = {s["key"]: s for s in sources(cfg)}
    want = [k for k in (opts.get("sources") or [k for k, s in avail.items() if s["ready"]]) if avail.get(k, {}).get("ready")]
    if not want:
        raise RuntimeError("no deal source is ready: add a Best Buy key or lead feed and Keepa in Settings, or use demo mode")
    max_items = int(opts.get("max_items") or cfg.get("deal_sources", {}).get("max_items", 60))
    min_disc = float(opts.get("min_discount") or cfg.get("deal_sources", {}).get("min_discount", 0.30))
    max_price = float(opts.get("max_price") or cfg.get("deal_sources", {}).get("max_price", 150))
    with db.tx() as c:
        scan_id = c.execute("INSERT INTO scans(started_at, status, sources) VALUES (?, 'running', ?)",
                            (db.now(), json.dumps(want))).lastrowid
    logs = []

    def log(m):
        logs.append(m)
        _set(log=logs[-8:])

    _set(status="running", scan_id=scan_id, stage="Collecting deals", progress=0.02, checked=0, total=0, matched=0,
         buys=0, maybes=0, log=[], error=None, sources=want, started_at=db.now(), _cancel=False,
         est_minutes=None)
    try:
        cands = []
        per = max(10, max_items // len(want))
        for k in want:
            _check_cancel()
            _set(stage=f"Collecting deals: {avail[k]['name']}")
            if k == "demo":
                got = _demo_candidates(per, min_disc)
                log(f"demo feed: {len(got)} retailer deals")
            elif k == "bestbuy":
                got = _bestbuy_candidates(cfg, per, min_disc, max_price, log)
            elif k == "flips":
                got = _flip_candidates(cfg, per, min_disc, max_price, log)
            else:
                got = _feed_candidates(cfg, per, log)
            for g in got:
                g["_source"] = k
            cands.extend(got)
        cands = [c for c in cands if c.get("buy_cost") and (c.get("asin") or c.get("upc"))][:max_items]
        if cfg["mode"] == "live":
            tpm = 20
            _set(est_minutes=round(len(cands) * KEEPA_TOKENS_PER_ITEM / tpm, 1))
        ids = [leads.upsert(c, source=f"scan:{c['_source']}", scan_id=scan_id) for c in cands]
        _set(total=len(ids), stage="Matching to Amazon and scoring", progress=0.1)
        step = 5 if cfg["mode"] != "live" else 20
        counts = {"BUY": 0, "MAYBE": 0, "PASS": 0, "NO_MATCH": 0, "UNKNOWN": 0}
        for i in range(0, len(ids), step):
            _check_cancel()
            res = leads.score(ids[i:i + step], cfg)
            for k, v in (res.get("counts") or {}).items():
                counts[k] = counts.get(k, 0) + v
            done = min(len(ids), i + step)
            _set(checked=done, progress=0.1 + 0.88 * done / max(1, len(ids)), buys=counts["BUY"], maybes=counts["MAYBE"],
                 matched=done - counts["NO_MATCH"])
            if cfg["mode"] != "live":
                time.sleep(0.15)  # demo: let the progress bar be seen
        msg = f"{counts['BUY']} BUY and {counts['MAYBE']} MAYBE deals from {len(ids)} checked"
        with db.tx() as c:
            c.execute("UPDATE scans SET finished_at=?, status='done', checked=?, matched=?, buys=?, maybes=?, message=? WHERE id=?",
                      (db.now(), len(ids), len(ids) - counts["NO_MATCH"], counts["BUY"], counts["MAYBE"], msg, scan_id))
        db.log("scan", "deal scan: " + msg, {"scan_id": scan_id, "sources": want})
        _set(status="done", stage=msg, progress=1.0, finished_at=db.now())
        return {"scan_id": scan_id, "counts": counts, "checked": len(ids)}
    except Cancelled:
        with db.tx() as c:
            c.execute("UPDATE scans SET finished_at=?, status='cancelled', message='stopped' WHERE id=?", (db.now(), scan_id))
        _set(status="cancelled", stage="Stopped. Deals found so far are below.", progress=STATE.get("progress", 0))
        return {"scan_id": scan_id, "cancelled": True}
    except Exception as e:
        with db.tx() as c:
            c.execute("UPDATE scans SET finished_at=?, status='error', message=? WHERE id=?", (db.now(), str(e)[:300], scan_id))
        _set(status="error", error=str(e), stage="Scan failed")
        raise


def start(opts: Optional[dict] = None) -> dict:
    with _lock:
        if STATE.get("status") == "running":
            return {"started": False, "error": "a scan is already running"}
        STATE.update(status="running", stage="Starting", progress=0.0, _cancel=False, error=None)

    def work():
        try:
            run(opts)
        except Exception:
            pass  # state already holds the error

    threading.Thread(target=work, daemon=True, name="deal-scan").start()
    return {"started": True}


SORTS = {
    "margin": "margin DESC",
    "roi": "roi DESC",
    "profit": "profit DESC",
    "monthly": "monthly_profit DESC",
}


def results(scan_id: Optional[int] = None, sort: str = "margin", include: str = "good", limit: int = 200) -> dict:
    if scan_id is None:
        last = db.one("SELECT id FROM scans WHERE status IN ('done','cancelled','running') ORDER BY id DESC LIMIT 1")
        scan_id = last["id"] if last else None
    scan = db.one("SELECT * FROM scans WHERE id=?", (scan_id,)) if scan_id else None
    if not scan:
        return {"scan": None, "deals": []}
    verdicts = ("BUY",) if include == "buy" else ("BUY", "MAYBE") if include == "good" else ("BUY", "MAYBE", "PASS")
    q = (f"SELECT * FROM leads WHERE scan_id=? AND status IN ('new','scored','bought') AND verdict IN ({','.join('?' * len(verdicts))}) "
         f"ORDER BY CASE verdict WHEN 'BUY' THEN 0 WHEN 'MAYBE' THEN 1 ELSE 2 END, {SORTS.get(sort, SORTS['margin'])} LIMIT ?")
    rows = db.rows(q, (scan_id, *verdicts, limit))
    for r in rows:
        r["evaluation"] = json.loads(r["evaluation"]) if r.get("evaluation") else None
    return {"scan": scan, "deals": rows}
