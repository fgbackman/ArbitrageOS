"""Keepa API client (https://keepa.com/api-docs/).

Token use: 1 token per ASIN or UPC with stats; +2 with buybox. The client
waits when the bucket runs low instead of failing.
"""
from __future__ import annotations

import gzip
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from .base import ProductData, Provider

API = "https://api.keepa.com"
# Keepa price type indices in stats arrays
AMAZON, NEW, USED, SALES, LISTPRICE, NEW_FBA, COUNT_NEW, BUY_BOX = 0, 1, 2, 3, 4, 10, 11, 18
MM_PER_IN = 25.4
G_PER_LB = 453.592


class KeepaError(RuntimeError):
    pass


def _price(v):
    return round(v / 100.0, 2) if isinstance(v, (int, float)) and v > 0 else None


def _at(arr, idx):
    if not isinstance(arr, list) or idx >= len(arr):
        return None
    v = arr[idx]
    if isinstance(v, list):  # min/max entries are [time, value]
        v = v[1] if len(v) > 1 else None
    return v


class KeepaProvider(Provider):
    name = "keepa"

    def __init__(self, api_key: str, domain: int = 1, timeout: int = 60):
        if not api_key:
            raise KeepaError("Keepa API key is not set")
        self.key, self.domain, self.timeout = api_key, domain, timeout
        self.tokens_left = None
        self.refill_rate = None

    # ---------------------------------------------------------------- http
    def _get(self, path: str, params: dict, retries: int = 3) -> dict:
        q = dict(params, key=self.key, domain=self.domain)
        url = f"{API}/{path}?{urllib.parse.urlencode(q)}"
        for attempt in range(retries + 1):
            req = urllib.request.Request(url, headers={"Accept-Encoding": "gzip", "User-Agent": "ArbitrageOS/1.0"})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    body = r.read()
                    if r.headers.get("Content-Encoding") == "gzip":
                        body = gzip.decompress(body)
                    data = json.loads(body)
            except urllib.error.HTTPError as e:
                body = e.read()
                try:
                    body = gzip.decompress(body)
                except OSError:
                    pass
                try:
                    data = json.loads(body)
                except ValueError:
                    raise KeepaError(f"Keepa HTTP {e.code}")
                if e.code == 429 and attempt < retries:  # out of tokens: wait for refill
                    wait = max(5, int(data.get("refillIn", 60000) / 1000) + 1)
                    time.sleep(min(wait, 120))
                    continue
                raise KeepaError(data.get("error", {}).get("message") or f"Keepa HTTP {e.code}")
            except urllib.error.URLError as e:
                if attempt < retries:
                    time.sleep(2 * (attempt + 1))
                    continue
                raise KeepaError(f"cannot reach Keepa: {e.reason}")
            self.tokens_left = data.get("tokensLeft")
            self.refill_rate = data.get("refillRate")
            if data.get("error"):
                raise KeepaError(data["error"].get("message", "Keepa error"))
            return data
        raise KeepaError("Keepa request failed")

    def token_status(self) -> dict:
        d = self._get("token", {})
        return {"tokens_left": d.get("tokensLeft"), "refill_rate": d.get("refillRate"), "refill_in_ms": d.get("refillIn")}

    # ------------------------------------------------------------- parsing
    def parse(self, prod: dict) -> ProductData:
        st = prod.get("stats") or {}
        cur, avg30, avg90 = st.get("current") or [], st.get("avg30") or [], st.get("avg90") or []
        min_iv, max_iv = st.get("minInInterval") or [], st.get("maxInInterval") or []
        bb = _price(st.get("buyBoxPrice"))
        if bb is None:
            bb = _price(_at(cur, BUY_BOX))
        new = _price(_at(cur, NEW))
        tree = prod.get("categoryTree") or []
        cat = tree[0]["name"] if tree else ""
        hz = prod.get("hazardousMaterials") or []
        upc = (prod.get("upcList") or [""])[0] if prod.get("upcList") else ((prod.get("eanList") or [""])[0] if prod.get("eanList") else "")
        L, W, H, G = (prod.get(k) for k in ("packageLength", "packageWidth", "packageHeight", "packageWeight"))
        images = prod.get("images") or []
        img = ""
        if images and isinstance(images[0], dict) and images[0].get("m"):
            img = "https://m.media-amazon.com/images/I/" + images[0]["m"]
        elif prod.get("imagesCSV"):
            img = "https://m.media-amazon.com/images/I/" + prod["imagesCSV"].split(",")[0]
        fba = prod.get("fbaFees") or {}
        oos = st.get("outOfStockPercentage90")
        return ProductData(
            asin=prod.get("asin", ""),
            title=prod.get("title") or "",
            brand=prod.get("brand") or "",
            category=cat,
            upc=upc,
            image=img,
            buybox_price=bb,
            new_price=new,
            amazon_price=_price(_at(cur, AMAZON)),
            avg30=_price(_at(avg30, BUY_BOX)) or _price(_at(avg30, NEW)),
            avg90=_price(_at(avg90, BUY_BOX)) or _price(_at(avg90, NEW)),
            min90=_price(_at(min_iv, BUY_BOX)) or _price(_at(min_iv, NEW)),
            max90=_price(_at(max_iv, BUY_BOX)) or _price(_at(max_iv, NEW)),
            sales_rank=(_at(cur, SALES) if (_at(cur, SALES) or -1) > 0 else None),
            rank_avg90=(_at(avg90, SALES) if (_at(avg90, SALES) or -1) > 0 else None),
            rank_drops30=st.get("salesRankDrops30") if (st.get("salesRankDrops30") or -1) >= 0 else None,
            rank_drops90=st.get("salesRankDrops90") if (st.get("salesRankDrops90") or -1) >= 0 else None,
            monthly_sold=prod.get("monthlySold") or None,
            fba_offers=st.get("offerCountFBA") if (st.get("offerCountFBA") or -1) >= 0 else None,
            fbm_offers=st.get("offerCountFBM") if (st.get("offerCountFBM") or -1) >= 0 else None,
            buybox_is_amazon=st.get("buyBoxIsAmazon"),
            buybox_is_fba=st.get("buyBoxIsFBA"),
            amazon_oos_pct90=(oos[AMAZON] if isinstance(oos, list) and len(oos) > AMAZON and oos[AMAZON] >= 0 else None),
            length_in=round(L / MM_PER_IN, 2) if L and L > 0 else None,
            width_in=round(W / MM_PER_IN, 2) if W and W > 0 else None,
            height_in=round(H / MM_PER_IN, 2) if H and H > 0 else None,
            weight_lb=round(G / G_PER_LB, 3) if G and G > 0 else None,
            hazmat=bool(hz),
            adult=bool(prod.get("isAdultProduct")),
            gated=None,
            referral_pct=prod.get("referralFeePercentage") or prod.get("referralFeePercent"),
            fba_pick_pack=_price(fba.get("pickAndPackFee")),
            source="keepa",
            fetched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            raw={"stats": {k: st.get(k) for k in ("current", "avg90")}},
        )

    # --------------------------------------------------------------- calls
    def get_products(self, asins: list, buybox: bool = True) -> list:
        out = []
        asins = [a.strip().upper() for a in asins if a and a.strip()]
        for i in range(0, len(asins), 100):
            params = {"asin": ",".join(asins[i:i + 100]), "stats": 90, "history": 0}
            if buybox:
                params["buybox"] = 1
            data = self._get("product", params)
            out.extend(self.parse(p) for p in data.get("products") or [])
        return out

    def lookup_codes(self, codes: list) -> dict:
        res = {}
        codes = [c.strip() for c in codes if c and c.strip()]
        for i in range(0, len(codes), 100):
            batch = codes[i:i + 100]
            data = self._get("product", {"code": ",".join(batch), "stats": 90, "history": 0, "buybox": 1, "code-limit": 3})
            for p in data.get("products") or []:
                pd = self.parse(p)
                keys = set((p.get("upcList") or []) + (p.get("eanList") or []))
                matched = [c for c in batch if c in keys or c.lstrip("0") in {k.lstrip("0") for k in keys}]
                if not matched and len(batch) == 1:
                    matched = batch  # single code: the result belongs to it
                for c in matched:
                    res.setdefault(c, []).append(pd)
        return res

    def deals(self, min_drop_pct: int = 30, max_rank: int = 100000, categories=None, price_type: int = 0,
              price_range=(800, 15000), date_range: int = 2) -> list:
        """Recent price drops (default: Amazon's own price, past month). 5 tokens per 150 deals."""
        selection = {
            "page": 0, "domainId": self.domain, "priceTypes": [price_type],
            "deltaPercentRange": [max(10, min_drop_pct), 100], "currentRange": list(price_range),
            "salesRankRange": [1, max_rank], "isFilterEnabled": True, "isRangeEnabled": True,
            "dateRange": date_range, "sortType": 4, "filterErotic": True, "hasReviews": True,
        }
        if categories:
            selection["includeCategories"] = categories
        data = self._get("deal", {"selection": json.dumps(selection)})
        return (data.get("deals") or {}).get("dr") or []
