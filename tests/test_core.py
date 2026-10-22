"""Run: python3 -m unittest discover -s tests"""
import json
import os
import shutil
import tempfile
import threading
import unittest
import urllib.request
from datetime import date

TMP = tempfile.mkdtemp(prefix="arbos-test-")
os.environ["ARBOS_DATA"] = TMP

from arbos import config, db, demo_seed, fees as F, labels, ledger, leads, pnl, repricing, scoring, server  # noqa: E402
from arbos.providers.demo import CATALOG  # noqa: E402
from arbos.providers.keepa import KeepaProvider  # noqa: E402

NONPEAK = date(2026, 6, 1)
PEAK = date(2026, 11, 20)


class Fees(unittest.TestCase):
    def test_size_tiers(self):
        self.assertEqual(F.size_tier(F.Package(7.4, 4.6, 0.7, 0.42)), "small_standard")
        self.assertEqual(F.size_tier(F.Package(10, 8, 4, 2.0)), "large_standard")
        self.assertEqual(F.size_tier(F.Package(40, 20, 6, 22)), "large_bulky")
        self.assertEqual(F.size_tier(F.Package(49, 25, 6, 22)), "extra_large")  # dim weight 52.9 lb > 50

    def test_rate_card_spot_values(self):
        cases = [  # (pkg, price, date, expected base)
            (F.Package(7, 4, 0.5, 1.5 / 16), 9.0, NONPEAK, 2.43),
            (F.Package(7, 4, 0.5, 7 / 16), 25.0, NONPEAK, 3.54),
            (F.Package(7, 4, 0.5, 7 / 16), 25.0, PEAK, 3.74),
            (F.Package(10, 6, 3, 1.4), 25.0, NONPEAK, 5.42),
            (F.Package(10, 6, 3, 1.4), 60.0, PEAK, 5.99),
            (F.Package(10, 8, 4, 4.0), 25.0, NONPEAK, 6.97 + 0.08 * 4),
            (F.Package(14, 10, 6, 4.0), 25.0, NONPEAK, 6.97 + 0.08 * 13),  # dim weight 6.04 lb
        ]
        for pkg, price, d, base in cases:
            got = F.fulfillment_fee(pkg, price, d)
            self.assertAlmostEqual(got["base"], round(base, 2), places=2, msg=(pkg, price, d))
            self.assertAlmostEqual(got["fuel_surcharge"], round(base * 0.035, 2), places=2)

    def test_dim_weight_used_for_large_standard(self):
        light_box = F.Package(17, 13, 7, 1.0)  # 1547 / 139 = 11.1 lb dim weight
        self.assertGreater(F.fulfillment_fee(light_box, 30, NONPEAK)["shipping_weight_lb"], 11)

    def test_peak_window(self):
        self.assertFalse(F.is_peak(date(2026, 10, 14)))
        self.assertTrue(F.is_peak(date(2026, 10, 15)))
        self.assertTrue(F.is_peak(date(2027, 1, 14)))
        self.assertFalse(F.is_peak(date(2027, 1, 15)))

    def test_referral(self):
        self.assertEqual(F.referral_fee(35.0, "Home & Kitchen"), 5.25)
        self.assertEqual(F.referral_fee(9.0, "Beauty & Personal Care"), 0.72)
        self.assertEqual(F.referral_fee(12.0, "Beauty & Personal Care"), 1.80)
        self.assertEqual(F.referral_fee(150.0, "Cell Phones & Accessories"), 19.0)  # 15 + 4
        self.assertEqual(F.referral_fee(1.0, "Home & Kitchen"), 0.30)
        self.assertEqual(F.referral_fee(18.0, "Clothing, Shoes & Jewelry"), 1.80)


class Scoring(unittest.TestCase):
    def setUp(self):
        self.cfg = config.load()

    def test_plan_example_deal(self):
        # The plan's waterfall: $35 sale, $15 cost, Q4 peak, about $7 profit.
        p = CATALOG["B0DEMO0004"]  # 1.05 lb large standard
        ev = scoring.evaluate(p, 15.0, self.cfg, sell_price=35.0, cashback_pct=0, on=PEAK)
        self.assertAlmostEqual(ev["fees"]["fulfillment"], round(5.34 * 1.035, 2), places=2)
        self.assertGreater(ev["profit"], 6.5)
        self.assertLess(ev["profit"], 7.8)
        self.assertAlmostEqual(ev["fees"]["referral"], 5.25)

    def test_max_buy_cost_hits_roi_floor(self):
        p = CATALOG["B0DEMO0003"]
        ev = scoring.evaluate(p, 4.0, self.cfg, on=PEAK, cashback_pct=0)
        at_max = scoring.evaluate(p, ev["max_buy_cost"], self.cfg, on=PEAK, cashback_pct=0)
        self.assertGreaterEqual(round(at_max["roi"], 3), 0.25 - 0.001)
        self.assertGreaterEqual(at_max["profit"], self.cfg["rules"]["min_profit"] - 0.01)

    def test_red_flags_fail(self):
        for asin, key in [("B0DEMO0009", "hazmat"), ("B0DEMO0012", "gated"), ("B0DEMO0015", "ip"), ("B0DEMO0014", "size")]:
            ev = scoring.evaluate(CATALOG[asin], 1.0, self.cfg, on=NONPEAK)
            self.assertEqual(ev["verdict"], "PASS", asin)
            self.assertIn(key, [c["key"] for c in ev["checks"] if c["status"] == "fail"], asin)

    def test_amazon_on_listing_fails(self):
        ev = scoring.evaluate(CATALOG["B0DEMO0007"], 5.0, self.cfg, on=NONPEAK)
        self.assertIn("amazon", [c["key"] for c in ev["checks"] if c["status"] == "fail"])

    def test_min_price_is_profitable(self):
        p = CATALOG["B0DEMO0004"]
        pkg = scoring.package_of(p)
        floor = scoring.min_price_for(12.0, pkg, p.category, self.cfg, on=PEAK)
        sunk = dict(self.cfg["costs"], prep_per_unit=0, inbound_per_lb=0, inbound_min_per_unit=0)
        fb = F.fee_stack(floor, pkg, p.category, sunk, PEAK, months_stored=0.5)
        self.assertGreaterEqual(floor - fb.total - 12.0, 1.19)


class Labels(unittest.TestCase):
    def test_code128_known_widths(self):
        # "X00ABC1234": start B + 10 chars + check + stop = 13 symbols; stop has 7 elements
        w = labels.code128b("X00ABC1234")
        self.assertEqual(len(w), 12 * 6 + 7)
        self.assertEqual(sum(w), 12 * 11 + 13)  # every symbol is 11 modules, stop is 13
        self.assertEqual(w[:6], [2, 1, 1, 2, 1, 4])  # start B

    def test_checksum(self):
        # Code 128 B check: (104 + sum(position * value)) mod 103. For "AB": 104 + 1*33 + 2*34 = 205 -> 102
        w = labels.code128b("AB")
        check_widths = w[18:24]
        self.assertEqual(check_widths, [int(c) for c in labels.PATTERNS[102]])

    def test_label_sheet(self):
        html = labels.label_html([{"fnsku": "X00ABCDE12", "title": "Thing", "qty": 31}], "avery5160")
        self.assertEqual(html.count('class="lab"'), 31)
        self.assertEqual(html.count('class="sheet"'), 2)


class KeepaParse(unittest.TestCase):
    def test_parse(self):
        prod = {"asin": "B000TEST01", "title": "T", "brand": "B", "categoryTree": [{"catId": 1, "name": "Home & Kitchen"}],
                "packageLength": 254, "packageWidth": 127, "packageHeight": 51, "packageWeight": 454,
                "upcList": ["012345678905"], "monthlySold": 300, "referralFeePercentage": 15,
                "stats": {"current": [-1, 2599, -1, 4200] + [-1] * 14 + [2499], "avg90": [-1, 2650, -1, 5100] + [-1] * 14 + [2550],
                          "buyBoxPrice": 2499, "offerCountFBA": 4, "salesRankDrops30": 220, "buyBoxIsAmazon": False}}
        p = KeepaProvider("k").parse(prod)
        self.assertEqual(p.buybox_price, 24.99)
        self.assertEqual(p.avg90, 25.50)
        self.assertEqual(p.sales_rank, 4200)
        self.assertEqual(p.fba_offers, 4)
        self.assertAlmostEqual(p.length_in, 10.0, places=1)
        self.assertAlmostEqual(p.weight_lb, 1.0, places=2)
        self.assertEqual(p.category, "Home & Kitchen")


class Ledger(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.reset_connection()
        demo_seed.wipe()

    def test_purchase_sale_fifo(self):
        cfg = config.load()
        r = ledger.record_purchase({"bought_on": "2026-09-01", "retailer": "Target", "sales_tax": 0, "cashback": 0,
                                    "items": [{"asin": "B0DEMO0003", "qty": 3, "unit_cost": 4.0}]}, cfg)
        ledger.record_purchase({"bought_on": "2026-09-05", "retailer": "Target",
                                "items": [{"asin": "B0DEMO0003", "qty": 2, "unit_cost": 6.0}]}, cfg)
        sku1 = db.one("SELECT sku, landed_unit_cost FROM lots WHERE id=?", (r["lot_ids"][0],))
        pnl.record_sale({"order_id": "O1", "sold_at": "2026-09-20", "sku": sku1["sku"], "qty": 2, "item_price": 30,
                         "referral_fee": 4.5, "fba_fee": 7.0})
        s = db.one("SELECT * FROM sales WHERE order_id='O1'")
        self.assertAlmostEqual(s["cogs"], round(2 * sku1["landed_unit_cost"], 2), places=2)
        # duplicate import is ignored
        self.assertIsNone(pnl.record_sale({"order_id": "O1", "sold_at": "2026-09-20", "sku": sku1["sku"], "qty": 2, "item_price": 30}))
        pack = ledger.invoice_pack("B0DEMO0003")
        self.assertEqual(pack["units_bought"], 5)
        self.assertEqual(len(pack["missing_receipts"]), 2)


class Leads(unittest.TestCase):
    def test_csv_import(self):
        with open(os.path.join(os.path.dirname(__file__), "..", "samples", "leads_example.csv")) as f:
            res = leads.import_csv(f.read())
        self.assertEqual(res["imported"], 6)
        sc = leads.score(res["ids"])
        self.assertEqual(sc["counts"]["NO_MATCH"], 1)
        self.assertGreaterEqual(sc["counts"]["PASS"], 2)


class Server(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.reset_connection()
        demo_seed.seed()
        cls.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def req(self, path, body=None, headers=None):
        h = {"Host": f"127.0.0.1:{self.port}"}
        if body is not None:
            h.update({"Content-Type": "application/json", "X-ArbOS": "1"})
        h.update(headers or {})
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=json.dumps(body).encode() if body is not None else None, headers=h)
        try:
            with urllib.request.urlopen(r) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_endpoints(self):
        for path in ["/", "/app.js", "/api/summary", "/api/leads", "/api/lots", "/api/shipments", "/api/sales",
                     "/api/repricing", "/api/settings", "/api/checklist", "/export/prices?kind=amazon"]:
            code, body = self.req(path)
            self.assertEqual(code, 200, path)
        code, body = self.req("/api/check", {"query": "000000100031", "buy_cost": 4.49})
        self.assertEqual(code, 200)
        self.assertIn(json.loads(body)["results"][0]["verdict"], ("BUY", "MAYBE", "PASS"))
        lots = json.loads(self.req("/api/lots?active=1")[1])["lots"]
        code, html = self.req("/labels?lots=" + ",".join(str(l["id"]) for l in lots[:2]) + "&fmt=thermal225")
        self.assertEqual(code, 200)
        self.assertIn(b"<svg", html)

    def test_security(self):
        self.assertEqual(self.req("/api/summary", headers={"Host": "evil.example"})[0], 403)
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/settings", data=b"{}", headers={"Content-Type": "application/json"})
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(r)
        self.assertEqual(cm.exception.code, 403)
        settings = json.loads(self.req("/api/settings")[1])
        self.assertNotIn("refresh_token\": \"A", json.dumps(settings))


def tearDownModule():
    db.reset_connection()
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
