"""Settings: buying rules from the plan, cost assumptions and API keys.

Stored as JSON in data/config.json next to the database. Keys never leave the
machine except in calls to Keepa and Amazon.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("ARBOS_DATA", ROOT / "data"))
CONFIG_PATH = DATA_DIR / "config.json"
DB_PATH = DATA_DIR / "arbos.db"
RECEIPTS_DIR = DATA_DIR / "receipts"
EXPORTS_DIR = DATA_DIR / "exports"

DEFAULTS = {
    "mode": "demo",  # "demo" or "live"
    "business": {
        "name": "My Arbitrage Store",
        "starting_capital": 3800.0,
        "monthly_profit_goal": 10000.0,
        "state": "CA",
        "sales_tax_rate": 0.0725,
        "use_resale_certificate": True,
    },
    "rules": {
        # The buy filter from the plan ("What to sell").
        "min_roi": 0.30,
        "min_roi_fast_mover": 0.25,
        "fast_mover_monthly_sales": 150,
        "min_profit": 3.00,
        "min_monthly_sales": 30,
        "max_fba_sellers": 9,
        "max_price_above_avg90": 0.15,
        "allow_amazon_on_listing": False,
        "amazon_price_gap_ok": 0.15,  # OK if Amazon sells 15%+ above our price
        "allowed_size_tiers": ["small_standard", "large_standard"],
        "max_share_of_capital_per_sku": 0.10,
        "months_of_stock": 1.5,
        "min_days_to_expiry": 105,
        "reprice_floor_roi": 0.10,  # repricer never goes below this ROI
        "reprice_floor_profit": 1.00,
    },
    "costs": {
        "prep_per_unit": 0.75,
        "inbound_per_lb": 0.45,
        "inbound_min_per_unit": 0.25,
        "placement_option": "amazon_optimized",  # amazon_optimized | partial | minimal
        "returns_reserve_pct": 0.02,
        "expected_months_in_storage": 1.0,
        "default_cashback_pct": 0.02,
        "fuel_surcharge_pct": 0.035,
        "monthly_tool_costs": 150.0,
    },
    "keepa": {"api_key": "", "domain": 1},
    "deal_sources": {
        "bestbuy_api_key": "",
        "feed_urls": [],
        "max_items": 60,
        "min_discount": 0.30,
        "max_price": 150,
        "daily_scan": True,
    },
    "spapi": {
        "lwa_client_id": "",
        "lwa_client_secret": "",
        "refresh_token": "",
        "seller_id": "",
        "marketplace_id": "ATVPDKIKX0DER",
        "endpoint": "https://sellingpartnerapi-na.amazon.com",
    },
    "watchlists": {
        # Brands that often file IP complaints against resellers. Edit freely.
        "ip_risk_brands": [
            "Nike", "Apple", "Samsung", "Sony", "Bose", "Disney", "LEGO", "Otterbox",
            "Under Armour", "The North Face", "Yeti", "Hydro Flask", "Stanley",
            "Michael Kors", "Coach", "Ugg", "Crocs", "Microsoft", "Nintendo", "Dyson",
        ],
        "blocked_brands": [],
        "preferred_retailers": [
            "Walmart", "Target", "Best Buy", "Home Depot", "Lowe's", "Kohl's",
            "Walgreens", "CVS", "Brand site",
        ],
    },
    "server": {"host": "127.0.0.1", "port": 8787},
}

SECRET_PATHS = [("keepa", "api_key"), ("spapi", "lwa_client_secret"), ("spapi", "refresh_token"),
                ("deal_sources", "bestbuy_api_key")]


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def ensure_dirs() -> None:
    for d in (DATA_DIR, RECEIPTS_DIR, EXPORTS_DIR):
        d.mkdir(parents=True, exist_ok=True)


def load() -> dict:
    ensure_dirs()
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            return _merge(DEFAULTS, json.load(f))
    return copy.deepcopy(DEFAULTS)


def save(cfg: dict) -> None:
    ensure_dirs()
    tmp = CONFIG_PATH.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, CONFIG_PATH)
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except OSError:
        pass


def update(patch: dict) -> dict:
    """Merge a partial settings dict. Masked secrets ("****") are ignored."""
    cfg = load()
    patch = copy.deepcopy(patch)
    for section, key in SECRET_PATHS:
        val = patch.get(section, {}).get(key)
        if isinstance(val, str) and val.startswith("****"):
            patch[section].pop(key)
    cfg = _merge(cfg, patch)
    save(cfg)
    return cfg


def public(cfg: dict) -> dict:
    """Config safe to send to the browser: secrets masked."""
    out = copy.deepcopy(cfg)
    for section, key in SECRET_PATHS:
        val = out.get(section, {}).get(key, "")
        if val:
            out[section][key] = "****" + val[-4:]
    out["keepa_ready"] = bool(cfg["keepa"]["api_key"])
    out["spapi_ready"] = all(cfg["spapi"][k] for k in ("lwa_client_id", "lwa_client_secret", "refresh_token", "seller_id"))
    return out
