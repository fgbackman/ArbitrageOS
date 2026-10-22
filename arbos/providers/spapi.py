"""Amazon Selling Partner API client for your own seller account.

Auth: Login with Amazon refresh token (self-authorized private app). No AWS
signing is needed. Create the app in Seller Central > Apps and Services >
Develop Apps, authorize it for your account, and paste the client ID,
client secret, refresh token and your seller ID into Settings.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

LWA_URL = "https://api.amazon.com/auth/o2/token"


class SPAPIError(RuntimeError):
    def __init__(self, msg, status=None, body=None):
        super().__init__(msg)
        self.status, self.body = status, body


class SPAPI:
    def __init__(self, cfg: dict, timeout: int = 60):
        s = cfg["spapi"]
        missing = [k for k in ("lwa_client_id", "lwa_client_secret", "refresh_token", "seller_id") if not s.get(k)]
        if missing:
            raise SPAPIError("Selling Partner API not configured: missing " + ", ".join(missing))
        self.s, self.timeout = s, timeout
        self.endpoint = s.get("endpoint", "https://sellingpartnerapi-na.amazon.com").rstrip("/")
        self.mkt = s.get("marketplace_id", "ATVPDKIKX0DER")
        self.seller = s["seller_id"]
        self._token, self._exp = None, 0.0

    # ---------------------------------------------------------------- auth
    def token(self) -> str:
        if self._token and time.time() < self._exp - 60:
            return self._token
        body = urllib.parse.urlencode({
            "grant_type": "refresh_token", "refresh_token": self.s["refresh_token"],
            "client_id": self.s["lwa_client_id"], "client_secret": self.s["lwa_client_secret"],
        }).encode()
        req = urllib.request.Request(LWA_URL, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                d = json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise SPAPIError(f"Login with Amazon refused the refresh token (HTTP {e.code})", e.code, e.read()[:500])
        self._token, self._exp = d["access_token"], time.time() + int(d.get("expires_in", 3600))
        return self._token

    def call(self, method: str, path: str, params: dict = None, body=None, retries: int = 5) -> dict:
        url = self.endpoint + path
        if params:
            url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None}, doseq=True)
        data = json.dumps(body).encode() if body is not None else None
        for attempt in range(retries + 1):
            req = urllib.request.Request(url, data=data, method=method, headers={
                "x-amz-access-token": self.token(), "Content-Type": "application/json",
                "User-Agent": "ArbitrageOS/1.0 (Language=Python)",
            })
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    raw = r.read()
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as e:
                raw = e.read()
                if e.code in (429, 500, 503) and attempt < retries:
                    time.sleep(min(30, 2 ** attempt))
                    continue
                try:
                    errs = json.loads(raw).get("errors") or []
                    msg = "; ".join(x.get("message", "") for x in errs) or f"HTTP {e.code}"
                except ValueError:
                    msg = f"HTTP {e.code}"
                raise SPAPIError(f"{method} {path}: {msg}", e.code, raw[:1000])
            except urllib.error.URLError as e:
                if attempt < retries:
                    time.sleep(2 * (attempt + 1))
                    continue
                raise SPAPIError(f"cannot reach Amazon: {e.reason}")
        raise SPAPIError("request failed")

    # ------------------------------------------------------- listing checks
    def restrictions(self, asin: str, condition: str = "new_new") -> dict:
        """Gating check: empty restrictions list = you can list it."""
        d = self.call("GET", "/listings/2021-08-01/restrictions", {
            "asin": asin, "sellerId": self.seller, "marketplaceIds": self.mkt, "conditionType": condition})
        restr = d.get("restrictions") or []
        reasons = []
        for r in restr:
            for rs in r.get("reasons") or []:
                reasons.append({"code": rs.get("reasonCode"), "message": rs.get("message"),
                                "approval_link": next((l.get("resource") for l in rs.get("links") or [] if l.get("verb") == "GET"), None)})
        return {"gated": bool(restr), "reasons": reasons}

    def fee_estimate(self, asin: str, price: float, fba: bool = True) -> dict:
        body = {"FeesEstimateRequest": {
            "MarketplaceId": self.mkt, "IsAmazonFulfilled": fba, "Identifier": f"arbos-{asin}-{price}",
            "PriceToEstimateFees": {"ListingPrice": {"CurrencyCode": "USD", "Amount": round(price, 2)}},
        }}
        d = self.call("POST", f"/products/fees/v0/items/{asin}/feesEstimate", body=body)
        res = (d.get("payload") or {}).get("FeesEstimateResult") or {}
        est = res.get("FeesEstimate") or {}
        out = {"total": None, "referral": 0.0, "fulfillment": 0.0, "other": 0.0}
        if est:
            out["total"] = (est.get("TotalFeesEstimate") or {}).get("Amount")
            for f in est.get("FeeDetailList") or []:
                amt = (f.get("FinalFee") or f.get("FeeAmount") or {}).get("Amount", 0.0)
                t = f.get("FeeType", "")
                if t == "ReferralFee":
                    out["referral"] += amt
                elif t in ("FBAFees", "FBAPerUnitFulfillmentFee", "FulfillmentFee"):
                    out["fulfillment"] += amt
                else:
                    out["other"] += amt
        return out

    def catalog_by_upc(self, upc: str) -> list:
        d = self.call("GET", "/catalog/2022-04-01/items", {
            "identifiers": upc, "identifiersType": "UPC" if len(upc) == 12 else "EAN",
            "marketplaceIds": self.mkt, "includedData": "summaries,salesRanks"})
        return [i.get("asin") for i in d.get("items") or [] if i.get("asin")]

    def offers(self, asin: str) -> dict:
        return self.call("GET", f"/products/pricing/v0/items/{asin}/offers",
                         {"MarketplaceId": self.mkt, "ItemCondition": "New"})

    # ----------------------------------------------------------- inventory
    def inventory_summaries(self) -> list:
        out, token = [], None
        while True:
            d = self.call("GET", "/fba/inventory/v1/summaries", {
                "details": "true", "granularityType": "Marketplace", "granularityId": self.mkt,
                "marketplaceIds": self.mkt, "nextToken": token})
            p = d.get("payload") or {}
            out.extend(p.get("inventorySummaries") or [])
            token = (d.get("pagination") or {}).get("nextToken")
            if not token:
                return out

    # -------------------------------------------------------------- orders
    def orders(self, since: datetime) -> list:
        out, token = [], None
        while True:
            params = {"MarketplaceIds": self.mkt, "NextToken": token} if token else {
                "MarketplaceIds": self.mkt, "LastUpdatedAfter": since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
            d = self.call("GET", "/orders/v0/orders", params)
            p = d.get("payload") or {}
            out.extend(p.get("Orders") or [])
            token = p.get("NextToken")
            if not token:
                return out
            time.sleep(1)

    def order_items(self, order_id: str) -> list:
        d = self.call("GET", f"/orders/v0/orders/{order_id}/orderItems")
        return (d.get("payload") or {}).get("OrderItems") or []

    def financial_events(self, since: datetime, until: datetime = None) -> dict:
        """Merged shipment and refund events since a date (exact fees per order)."""
        merged = {"ShipmentEventList": [], "RefundEventList": [], "ServiceFeeEventList": [], "AdjustmentEventList": []}
        token = None
        until = until or (datetime.now(timezone.utc) - timedelta(minutes=3))
        while True:
            params = {"NextToken": token} if token else {
                "PostedAfter": since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "PostedBefore": until.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "MaxResultsPerPage": 100}
            d = self.call("GET", "/finances/v0/financialEvents", params)
            p = d.get("payload") or {}
            ev = p.get("FinancialEvents") or {}
            for k in merged:
                merged[k].extend(ev.get(k) or [])
            token = p.get("NextToken")
            if not token:
                return merged
            time.sleep(2)

    # ------------------------------------------------------------- pricing
    def set_price_bounds(self, sku: str, product_type: str, price: float, min_price: float, max_price: float) -> dict:
        """Update your offer price plus min/max allowed prices (used by Automate Pricing)."""
        offer = {
            "marketplace_id": self.mkt, "currency": "USD",
            "our_price": [{"schedule": [{"value_with_tax": round(price, 2)}]}],
            "minimum_seller_allowed_price": [{"schedule": [{"value_with_tax": round(min_price, 2)}]}],
            "maximum_seller_allowed_price": [{"schedule": [{"value_with_tax": round(max_price, 2)}]}],
        }
        body = {"productType": product_type or "PRODUCT",
                "patches": [{"op": "replace", "path": "/attributes/purchasable_offer", "value": [offer]}]}
        return self.call("PATCH", f"/listings/2021-08-01/items/{self.seller}/{urllib.parse.quote(sku, safe='')}",
                         {"marketplaceIds": self.mkt}, body)

    def ping(self) -> dict:
        self.token()
        d = self.call("GET", "/sellers/v1/marketplaceParticipations")
        mk = [m.get("marketplace", {}).get("name") for m in d.get("payload") or []]
        return {"ok": True, "marketplaces": mk}
