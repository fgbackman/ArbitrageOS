"""Local dashboard server: JSON API plus the single-page web app.

Binds to 127.0.0.1 only. Rejects requests whose Host header is not local
(blocks DNS-rebinding) and requires a custom header on every write (blocks
cross-site form posts).
"""
from __future__ import annotations

import json
import mimetypes
import re
import threading
import traceback
from datetime import date
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import (__version__, alerts, checklist, config, db, demo_seed, labels, ledger, leads, pnl, providers,
               repricing, scanner, scoring, sync)
from .providers import KeepaError, SPAPIError
from .providers.keepa import KeepaProvider
from .providers.spapi import SPAPI

WEB = Path(__file__).resolve().parent / "web"
ROUTES = []


def route(method, pattern):
    rx = re.compile("^" + pattern + "$")

    def deco(fn):
        ROUTES.append((method, rx, fn))
        return fn
    return deco


class ApiError(Exception):
    def __init__(self, msg, status=400):
        super().__init__(msg)
        self.status = status


# ------------------------------------------------------------------ overview
@route("GET", "/api/summary")
def summary(q, body):
    cfg = config.load()
    return {
        "version": __version__, "mode": cfg["mode"], "business": cfg["business"]["name"],
        "position": pnl.position(cfg), "goal": pnl.goal(cfg), "monthly": pnl.monthly(12),
        "alerts": alerts.compute(cfg), "checklist": checklist.get(),
        "events": db.rows("SELECT * FROM events ORDER BY id DESC LIMIT 15"),
        "lead_counts": {r["verdict"] or r["status"]: r["n"] for r in db.rows(
            "SELECT verdict, status, COUNT(*) AS n FROM leads WHERE status IN ('new','scored') GROUP BY verdict, status")},
        "keepa_ready": bool(cfg["keepa"]["api_key"]),
        "spapi_ready": config.public(cfg)["spapi_ready"],
        "today": date.today().isoformat(),
    }


# --------------------------------------------------------------- deal check
@route("POST", "/api/check")
def check(q, body):
    cfg = config.load()
    query = (body.get("query") or "").strip()
    if not query:
        raise ApiError("enter an ASIN, UPC or Amazon link")
    m = re.search(r"/(?:dp|gp/product)/([A-Z0-9]{10})", query, re.I)
    asin = m.group(1).upper() if m else None
    digits = re.sub(r"\D", "", query)
    prods = []
    if asin or (re.fullmatch(r"[A-Z0-9]{10}", query.upper()) and not query.isdigit()):
        prods = providers.get_products([asin or query.upper()], cfg)
    elif len(digits) in (12, 13, 14):
        prods = providers.lookup_codes([digits], cfg).get(digits, [])
    if not prods:
        mode = "demo catalog" if cfg["mode"] != "live" else "Keepa"
        raise ApiError(f"no Amazon listing found for {query} in the {mode}", 404)
    cost = float(body.get("buy_cost") or 0)
    if cost <= 0:
        raise ApiError("enter what it costs you to buy one unit")
    sell = float(body["sell_price"]) if body.get("sell_price") else None
    cb = float(body["cashback_pct"]) / 100 if body.get("cashback_pct") not in (None, "") else None
    exp = int(body["expiry_days"]) if body.get("expiry_days") else None
    capital = pnl.position(cfg)["cash"]
    results = []
    for p in prods[:3]:
        live = None
        sp = providers.spapi(cfg)
        if sp and (sell or p.buybox_price):
            try:
                fe = sp.fee_estimate(p.asin, sell or p.buybox_price)
                if fe["total"] is not None:
                    live = fe
            except SPAPIError:
                pass
        results.append(scoring.evaluate(p, cost, cfg, sell_price=sell, cashback_pct=cb, capital=capital,
                                        expiry_days=exp, live_fees=live))
    return {"results": results}


# --------------------------------------------------------------- find deals
@route("GET", "/api/scan")
def scan_status(q, body):
    cfg = config.load()
    return {"state": scanner.status(), "sources": scanner.sources(cfg), "defaults": cfg["deal_sources"] | {"bestbuy_api_key": None},
            "mode": cfg["mode"], "last": db.one("SELECT * FROM scans ORDER BY id DESC LIMIT 1")}


@route("POST", "/api/scan")
def scan_start(q, body):
    res = scanner.start({k: body.get(k) for k in ("sources", "max_items", "min_discount", "max_price")})
    if not res["started"]:
        raise ApiError(res["error"], 409)
    return res


@route("POST", "/api/scan/cancel")
def scan_cancel(q, body):
    scanner.cancel()
    return {"ok": True}


@route("GET", "/api/deals")
def deals(q, body):
    sid = int(q["scan"]) if q.get("scan", "").isdigit() else None
    return scanner.results(sid, q.get("sort") or "margin", q.get("include") or "good")


# -------------------------------------------------------------------- leads
@route("GET", "/api/leads")
def leads_list(q, body):
    return {"leads": leads.list_leads(q.get("status"), q.get("verdict"))}


@route("POST", "/api/leads")
def leads_add(q, body):
    if not body.get("buy_cost") or not (body.get("asin") or body.get("upc")):
        raise ApiError("a lead needs a cost and an ASIN or UPC")
    ids = leads.add([_lead_from(body)], "manual")
    leads.score(ids)
    return {"ids": ids}


def _lead_from(body):
    d = {k: (body.get(k) or None) for k in ("retailer", "retailer_url", "title", "upc", "asin", "notes")}
    if d["asin"]:
        d["asin"] = d["asin"].strip().upper()
    if d["upc"]:
        d["upc"] = re.sub(r"\D", "", d["upc"])
    d["buy_cost"] = float(body["buy_cost"])
    d["cashback_pct"] = float(body["cashback_pct"]) / 100 if body.get("cashback_pct") not in (None, "") else None
    return d


@route("POST", "/api/leads/import")
def leads_import(q, body):
    try:
        res = leads.import_csv(body.get("csv") or "", body.get("source") or "csv")
    except ValueError as e:
        raise ApiError(str(e))
    res["score"] = leads.score(res["ids"])
    return res


@route("POST", "/api/leads/score")
def leads_score(q, body):
    return leads.score(body.get("ids"), rescore=bool(body.get("rescore")))


@route("POST", r"/api/leads/(\d+)/status")
def lead_status(q, body, lid):
    if body.get("status") not in ("new", "scored", "bought", "skipped"):
        raise ApiError("bad status")
    leads.set_status(int(lid), body["status"])
    return {"ok": True}


# ---------------------------------------------------------------- inventory
@route("GET", "/api/lots")
def lots(q, body):
    return {"lots": ledger.lots(q.get("status"), active_only=q.get("active") == "1")}


@route("POST", "/api/purchases")
def purchase(q, body):
    try:
        return ledger.record_purchase(body)
    except (KeyError, ValueError) as e:
        raise ApiError(f"purchase not saved: {e}")


@route("POST", r"/api/purchases/(\d+)/receipt")
def receipt(q, body, pid):
    if not body.get("content_b64"):
        raise ApiError("no file")
    return {"path": ledger.save_receipt(int(pid), body.get("filename") or "receipt", body["content_b64"])}


@route("POST", r"/api/lots/(\d+)")
def lot_update(q, body, lid):
    try:
        ledger.set_lot(int(lid), **body)
    except ValueError as e:
        raise ApiError(str(e))
    return {"ok": True}


@route("GET", "/api/shipments")
def shipments(q, body):
    return {"shipments": ledger.shipments()}


@route("POST", "/api/shipments")
def shipment_new(q, body):
    ids = [int(i) for i in body.get("lot_ids") or []]
    if not ids:
        raise ApiError("pick at least one lot")
    return {"id": ledger.create_shipment(ids, body.get("name"), body.get("placement_option"))}


@route("POST", r"/api/shipments/(\d+)")
def shipment_update(q, body, sid):
    ledger.set_shipment(int(sid), body.get("status", "draft"), body.get("amazon_shipment_id"),
                        float(body["carrier_cost"]) if body.get("carrier_cost") else None)
    return {"ok": True}


@route("GET", r"/api/invoice/([A-Za-z0-9]{10})")
def invoice(q, body, asin):
    return ledger.invoice_pack(asin)


# -------------------------------------------------------------------- money
@route("GET", "/api/sales")
def sales(q, body):
    return {"sales": pnl.recent_sales(300), "skus": pnl.sku_performance(), "monthly": pnl.monthly(24)}


@route("POST", "/api/sales")
def sale_add(q, body):
    need = ("order_id", "sku", "item_price")
    if not all(body.get(k) for k in need):
        raise ApiError("order ID, SKU and price are required")
    body.setdefault("sold_at", date.today().isoformat())
    return {"id": pnl.record_sale(body)}


@route("POST", "/api/expenses")
def expense(q, body):
    return {"id": pnl.add_expense(body.get("spent_on") or date.today().isoformat(), body.get("category") or "other",
                                  body.get("vendor") or "", float(body["amount"]), body.get("notes") or "")}


@route("POST", "/api/cash")
def cash(q, body):
    if body.get("kind") not in ("deposit", "withdrawal", "owner_draw"):
        raise ApiError("kind must be deposit, withdrawal or owner_draw")
    return {"id": pnl.add_cash(body.get("moved_on") or date.today().isoformat(), body["kind"], float(body["amount"]),
                               body.get("notes") or "")}


@route("GET", "/api/repricing")
def reprice(q, body):
    return {"rows": repricing.plan(refresh_market=q.get("refresh") == "1")}


@route("POST", "/api/repricing/push")
def reprice_push(q, body):
    return repricing.push()


@route("POST", "/api/sync")
def do_sync(q, body):
    try:
        return sync.run()
    except (SPAPIError, RuntimeError) as e:
        raise ApiError(str(e), 502)


# ------------------------------------------------------------------ settings
@route("GET", "/api/settings")
def settings(q, body):
    return config.public(config.load())


@route("POST", "/api/settings")
def settings_save(q, body):
    if body.get("mode") not in (None, "demo", "live"):
        raise ApiError("mode must be demo or live")
    cfg = config.update(body)
    db.log("settings", "settings saved")
    return config.public(cfg)


@route("POST", "/api/test/keepa")
def test_keepa(q, body):
    cfg = config.load()
    try:
        return KeepaProvider(cfg["keepa"]["api_key"], cfg["keepa"].get("domain", 1)).token_status()
    except KeepaError as e:
        raise ApiError(str(e), 502)


@route("POST", "/api/test/spapi")
def test_spapi(q, body):
    try:
        return SPAPI(config.load()).ping()
    except SPAPIError as e:
        raise ApiError(str(e), 502)


@route("GET", "/api/checklist")
def check_get(q, body):
    return {"items": checklist.get()}


@route("POST", "/api/checklist")
def check_set(q, body):
    checklist.set_done(body["key"], bool(body.get("done")))
    return {"ok": True}


@route("POST", "/api/demo/seed")
def demo(q, body):
    if config.load()["mode"] == "live":
        raise ApiError("switch to demo mode before loading demo data")
    return demo_seed.seed()


@route("POST", "/api/demo/wipe")
def wipe(q, body):
    if body.get("confirm") != "WIPE":
        raise ApiError("type WIPE to confirm")
    demo_seed.wipe()
    return {"ok": True}


# ---------------------------------------------------------------- handler
class Handler(BaseHTTPRequestHandler):
    server_version = "ArbitrageOS/" + __version__

    def log_message(self, fmt, *args):
        pass

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").split(":")[0].lower()
        return host in ("127.0.0.1", "localhost", "[::1]")

    def _send(self, status, payload, ctype="application/json; charset=utf-8", headers=None):
        data = payload if isinstance(payload, (bytes, bytearray)) else (
            json.dumps(payload, default=str).encode() if ctype.startswith("application/json") else str(payload).encode())
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _dispatch(self, method):
        if not self._host_ok():
            return self._send(403, {"error": "forbidden host"})
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        path = url.path
        if method == "GET" and not path.startswith("/api/"):
            return self._static_or_special(path, q)
        if method == "POST" and self.headers.get("X-ArbOS") != "1":
            return self._send(403, {"error": "missing X-ArbOS header"})
        body = {}
        if method == "POST":
            n = int(self.headers.get("Content-Length") or 0)
            if n > 25 * 1024 * 1024:
                return self._send(413, {"error": "too large"})
            raw = self.rfile.read(n) if n else b""
            try:
                body = json.loads(raw) if raw else {}
            except ValueError:
                return self._send(400, {"error": "invalid JSON"})
        for m, rx, fn in ROUTES:
            if m == method:
                hit = rx.match(path)
                if hit:
                    try:
                        return self._send(200, fn(q, body, *hit.groups()))
                    except ApiError as e:
                        return self._send(e.status, {"error": str(e)})
                    except (KeepaError, SPAPIError) as e:
                        return self._send(502, {"error": str(e)})
                    except Exception as e:  # surface, don't crash the server
                        traceback.print_exc()
                        return self._send(500, {"error": f"{type(e).__name__}: {e}"})
        return self._send(404, {"error": "not found"})

    def _static_or_special(self, path, q):
        if path == "/labels":
            ids = [int(x) for x in (q.get("lots") or "").split(",") if x.strip().isdigit()]
            rows = [l for l in ledger.lots() if l["id"] in ids] if ids else []
            qty_override = q.get("qty")
            items = [{"fnsku": l["fnsku"], "title": l["title"], "qty": int(qty_override) if qty_override else l["qty"]} for l in rows]
            missing = [l["sku"] for l in rows if not l["fnsku"]]
            if missing and not any(i["fnsku"] for i in items):
                return self._send(400, "No FNSKU yet for: " + ", ".join(missing) +
                                  ". Create the listing in Seller Central (or sync) first.", "text/plain; charset=utf-8")
            fmt = q.get("fmt") if q.get("fmt") in labels.FORMATS else "avery5160"
            return self._send(200, labels.label_html(items, fmt, skip=int(q.get("skip") or 0)), "text/html; charset=utf-8")
        if path.startswith("/export/prices"):
            kind = "amazon" if q.get("kind") != "repricer" else "repricer"
            text = repricing.export_csv(repricing.plan(), kind)
            ext = "txt" if kind == "amazon" else "csv"
            return self._send(200, text, "text/plain; charset=utf-8" if kind == "amazon" else "text/csv; charset=utf-8",
                              {"Content-Disposition": f'attachment; filename="prices_{kind}_{date.today().isoformat()}.{ext}"'})
        m = re.match(r"^/receipt/(\d+)$", path)
        if m:
            p = db.one("SELECT receipt_path FROM purchases WHERE id=?", (int(m.group(1)),))
            fp = Path(p["receipt_path"]) if p and p["receipt_path"] else None
            if not fp or not fp.exists() or config.RECEIPTS_DIR.resolve() not in fp.resolve().parents:
                return self._send(404, "receipt file not found", "text/plain; charset=utf-8")
            ctype = mimetypes.guess_type(fp.name)[0] or "application/octet-stream"
            return self._send(200, fp.read_bytes(), ctype)
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        fp = (WEB / rel).resolve()
        if WEB.resolve() not in fp.parents and fp != WEB.resolve() or not fp.is_file():
            fp = WEB / "index.html"
        ctype = mimetypes.guess_type(fp.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        self._send(200, fp.read_bytes(), ctype,
                   {"Content-Security-Policy": "default-src 'self'; img-src 'self' data: https://m.media-amazon.com; "
                                               "style-src 'self' 'unsafe-inline'; script-src 'self'; frame-src 'self'"})

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")


def serve(host=None, port=None, open_browser=False):
    cfg = config.load()
    host = host or cfg["server"]["host"]
    port = int(port or cfg["server"]["port"])
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise SystemExit("ArbitrageOS only serves on this computer (127.0.0.1)")
    db.get()
    httpd = ThreadingHTTPServer((host, port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"ArbitrageOS {__version__} running at {url}  (mode: {cfg['mode']})  Ctrl+C to stop")
    if open_browser:
        import webbrowser
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
