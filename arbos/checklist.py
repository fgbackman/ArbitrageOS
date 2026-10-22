"""Launch checklist: the account and setup steps only you can do, in order."""
from __future__ import annotations

from . import db

ITEMS = [
    {"key": "permit", "phase": "Legal and money", "title": "Get a California seller's permit",
     "detail": "Free from CDTFA, often issued same day online. Needed to buy inventory tax-free.",
     "link": "https://onlineservices.cdtfa.ca.gov/"},
    {"key": "resale_cert", "phase": "Legal and money", "title": "Fill out a resale certificate (CDTFA-230)",
     "detail": "One signed form you hand or upload to each retailer. Only for goods you will resell.",
     "link": "https://www.cdtfa.ca.gov/formspubs/cdtfa230.pdf"},
    {"key": "bank", "phase": "Legal and money", "title": "Open a business checking account and an inventory-only cashback card",
     "detail": "Keeps every purchase traceable for taxes and Amazon invoice reviews.", "link": ""},
    {"key": "seller_account", "phase": "Amazon", "title": "Open an Amazon Professional seller account ($39.99/mo)",
     "detail": "Have your ID, a credit card, bank account and phone ready; verification can take days.",
     "link": "https://sell.amazon.com/"},
    {"key": "tax_exempt", "phase": "Amazon", "title": "Register your resale certificate with retailers",
     "detail": "Walmart, Target, Best Buy, Home Depot and Amazon Business all have tax-exempt programs.", "link": ""},
    {"key": "keepa", "phase": "Data", "title": "Subscribe to the Keepa API (EUR 49/mo, 20 tokens/min)",
     "detail": "Paste the key in Settings. Turns on live prices, sales rank and offer counts.",
     "link": "https://keepa.com/#!api"},
    {"key": "spapi", "phase": "Data", "title": "Create a private Selling Partner API app and authorize it",
     "detail": "Seller Central > Apps and Services > Develop Apps. Paste client ID, secret, refresh token and seller ID in Settings.",
     "link": "https://developer-docs.amazon.com/sp-api/docs/self-authorization"},
    {"key": "selleramp", "phase": "Data", "title": "Install SellerAmp SAS on your phone for in-store scanning",
     "detail": "ArbitrageOS covers online sourcing; SAS covers store aisles.", "link": "https://selleramp.com/"},
    {"key": "supplies", "phase": "Prep", "title": "Buy a thermal label printer, poly bags, tape and boxes",
     "detail": "2.25 x 1.25 in thermal labels match the label printer format here.", "link": ""},
    {"key": "first_buys", "phase": "First shipment", "title": "Buy 10-15 deals that pass the filter (~$1,500)",
     "detail": "Import leads, score them, and buy only BUY verdicts at or under max cost.", "link": ""},
    {"key": "first_shipment", "phase": "First shipment", "title": "Send your first FBA shipment with Amazon-optimized splits",
     "detail": "Label every unit with its FNSKU; Amazon-optimized splits avoid placement fees.", "link": ""},
    {"key": "repricer", "phase": "First shipment", "title": "Set min and max prices on every SKU",
     "detail": "Use Repricing here: export to Amazon Automate Pricing or push via the API.", "link": ""},
    {"key": "live_mode", "phase": "First shipment", "title": "Switch ArbitrageOS to live mode",
     "detail": "Settings > Mode. Clears nothing; demo data can be wiped separately.", "link": ""},
]


def get() -> list:
    done = {r["key"]: r for r in db.rows("SELECT * FROM checklist")}
    out = []
    for it in ITEMS:
        d = done.get(it["key"])
        out.append(dict(it, done=bool(d and d["done"]), done_at=d["done_at"] if d else None))
    return out


def set_done(key: str, done: bool) -> None:
    with db.tx() as c:
        c.execute("INSERT INTO checklist(key, done, done_at) VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET done=excluded.done, done_at=excluded.done_at",
                  (key, 1 if done else 0, db.now() if done else None))
