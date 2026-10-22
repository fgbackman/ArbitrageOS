"""Command line: python3 -m arbos <command>

  serve [--open]        start the dashboard at http://127.0.0.1:8787
  sync                  pull sales, fees and inventory (demo: simulate)
  import FILE.csv       import and score retail leads
  score [--all]         score new leads (--all rescores open ones)
  check ASIN|UPC COST   score one deal in the terminal
  prices [amazon|repricer]  write a price file to data/exports
  alerts                print today's alerts
  demo                  load demo data (demo mode only)
  deals [N]             find and rank deals from every ready source (N = max items)
  daily                 sync + deal scan + rescore + alerts (what the scheduled job runs)
"""
from __future__ import annotations

import json
import sys

from . import alerts, config, db, demo_seed, leads, providers, repricing, scanner, scoring, server, sync


def _print_eval(ev: dict) -> None:
    if ev.get("verdict") == "UNKNOWN":
        print("UNKNOWN:", ev.get("reason"))
        return
    p = ev["product"]
    print(f"\n{ev['verdict']}  {p['title']}  ({p['asin']})")
    print(f"  sell ${ev['sell_price']:.2f}  landed ${ev['landed_cost']:.2f}  fees ${ev['fees']['total']:.2f}")
    print(f"  profit ${ev['profit']:.2f}  ROI {ev['roi']:.0%}  max buy ${ev['max_buy_cost']:.2f}  qty {ev['suggested_qty']}")
    for c in ev["checks"]:
        print(f"  [{c['status']:>7}] {c['label']}: {c['detail']}")


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv.pop(0) if argv else "serve"
    cfg = config.load()
    if cmd == "serve":
        server.serve(open_browser="--open" in argv)
    elif cmd == "sync":
        print(json.dumps(sync.run(cfg), indent=2))
    elif cmd == "import":
        if not argv:
            print("usage: import FILE.csv")
            return 2
        with open(argv[0], encoding="utf-8-sig") as f:
            res = leads.import_csv(f.read(), source=argv[0])
        print(json.dumps({"imported": res["imported"], "score": leads.score(res["ids"], cfg)}, indent=2))
    elif cmd == "score":
        print(json.dumps(leads.score(cfg=cfg, rescore="--all" in argv), indent=2))
    elif cmd == "check":
        if len(argv) < 2:
            print("usage: check ASIN|UPC COST")
            return 2
        q, cost = argv[0], float(argv[1])
        prods = providers.get_products([q], cfg) if not q.isdigit() else providers.lookup_codes([q], cfg).get(q, [])
        if not prods:
            print("no listing found")
            return 1
        for p in prods:
            _print_eval(scoring.evaluate(p, cost, cfg))
    elif cmd == "prices":
        print(repricing.save_export(argv[0] if argv else "amazon"))
    elif cmd == "alerts":
        for a in alerts.compute(cfg):
            print(f"[{a['severity']:>6}] {a['title']} - {a.get('detail', '')}")
    elif cmd == "demo":
        if cfg["mode"] == "live":
            print("switch to demo mode first")
            return 1
        print(json.dumps(demo_seed.seed(), indent=2))
    elif cmd == "deals":
        res = scanner.run({"max_items": int(argv[0]) if argv else None}, cfg)
        print(json.dumps(res["counts"] if "counts" in res else res, indent=2))
        for d in scanner.results(res["scan_id"], "margin", "good")["deals"][:25]:
            print(f"  {d['verdict']:5} margin {d['margin'] or 0:>5.0%}  ROI {d['roi'] or 0:>5.0%}  ${d['profit'] or 0:>6.2f}  "
                  f"{(d['retailer'] or '')[:10]:10} {d['buy_cost']:>7.2f}  {(d['title'] or '')[:50]}")
    elif cmd == "daily":
        out = {"sync": None, "score": None, "deals": None}
        try:
            out["sync"] = sync.run(cfg)
        except Exception as e:  # keep the scheduled job going
            out["sync"] = {"error": str(e)}
        if cfg.get("deal_sources", {}).get("daily_scan") and any(s["ready"] for s in scanner.sources(cfg)) and cfg["mode"] == "live":
            try:
                out["deals"] = scanner.run(None, cfg).get("counts")
            except Exception as e:
                out["deals"] = {"error": str(e)}
        out["score"] = leads.score(cfg=cfg, rescore=True)
        out["alerts"] = [a["title"] for a in alerts.compute(cfg) if a["severity"] == "high"]
        db.log("daily", "scheduled daily run", out)
        print(json.dumps(out, indent=2, default=str))
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
