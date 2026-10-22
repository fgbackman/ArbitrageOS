"""Demo catalog: fictional brands and made-up numbers so every screen works
before you have API keys. Nothing here is real market data."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from .base import ProductData, Provider

# asin, upc, title, brand, category, buybox, avg90, min90, amazon, rank, monthly, fba, L, W, H, lb, flags
_C = [
    ("B0DEMO0001", "000000100017", "Brightline Disinfecting Wipes, 3 x 75 ct", "Brightline", "Health & Household", 18.99, 18.40, 16.90, None, 3200, 600, 6, 8.1, 5.6, 7.9, 2.6, ""),
    ("B0DEMO0002", "000000100024", "Kettle & Oak Cast Iron Skillet 10 in", "Kettle & Oak", "Home & Kitchen", 34.95, 33.80, 31.00, None, 8400, 300, 5, 17.5, 10.9, 2.8, 5.3, ""),
    ("B0DEMO0003", "000000100031", "Paperwell Gel Pens, 24 Pack Assorted", "Paperwell", "Office Products", 15.49, 15.10, 13.99, None, 2100, 900, 8, 7.4, 4.6, 0.7, 0.42, ""),
    ("B0DEMO0004", "000000100048", "Trailhead Insulated Water Bottle 32 oz", "Trailhead", "Sports & Outdoors", 27.99, 26.50, 24.99, None, 15200, 200, 4, 10.8, 3.9, 3.9, 1.05, ""),
    ("B0DEMO0005", "000000100055", "Pawsome Dental Chews, Large, 30 ct", "Pawsome", "Pet Supplies", 29.99, 30.20, 28.50, None, 4600, 400, 7, 9.0, 7.0, 3.0, 2.2, ""),
    ("B0DEMO0006", "000000100062", "BlockWorks Marble Run Starter Set, 120 pc", "BlockWorks", "Toys & Games", 39.99, 36.00, 32.99, None, 6100, 500, 6, 14.0, 11.0, 4.0, 3.4, ""),
    ("B0DEMO0007", "000000100079", "Hearth Coffee Medium Roast Pods, 72 ct", "Hearth Coffee", "Grocery & Gourmet Food", 38.49, 37.90, 35.00, 37.99, 1300, 1500, 12, 11.5, 8.0, 7.0, 2.1, ""),
    ("B0DEMO0008", "000000100086", "Lumina LED Desk Lamp with USB Port", "Lumina", "Office Products", 32.99, 29.50, 25.99, None, 21000, 120, 3, 15.0, 7.0, 4.5, 2.4, ""),
    ("B0DEMO0009", "000000100093", "FreshFold Laundry Pods, 81 ct", "FreshFold", "Health & Household", 24.97, 24.60, 22.80, None, 2500, 800, 9, 9.3, 6.8, 7.4, 4.1, "hazmat"),
    ("B0DEMO0010", "000000100109", "Northpeak Kids' Rain Jacket, Size 8", "Northpeak", "Clothing, Shoes & Jewelry", 44.00, 45.10, 39.00, None, 52000, 40, 2, 12.0, 10.0, 2.0, 0.9, ""),
    ("B0DEMO0011", "000000100116", "Kettle & Oak Silicone Spatula Set, 5 pc", "Kettle & Oak", "Home & Kitchen", 19.99, 19.20, 17.99, None, 11800, 260, 6, 13.5, 4.5, 1.6, 0.68, ""),
    ("B0DEMO0012", "000000100123", "GlowCare Vitamin C Serum 1 oz", "GlowCare", "Beauty & Personal Care", 24.99, 25.40, 22.99, None, 9800, 350, 11, 5.0, 2.0, 2.0, 0.25, "gated"),
    ("B0DEMO0013", "000000100130", "BlockWorks Magnetic Tiles, 60 pc", "BlockWorks", "Toys & Games", 49.99, 44.00, 39.99, 46.99, 3400, 700, 14, 13.0, 9.5, 4.0, 4.0, ""),
    ("B0DEMO0014", "000000100147", "Greenhaven Raised Garden Bed 4x2 ft", "Greenhaven", "Patio, Lawn & Garden", 69.99, 72.00, 64.99, None, 28000, 70, 3, 49.0, 25.0, 6.0, 22.0, ""),
    ("B0DEMO0015", "000000100154", "Glacierline Quencher Tumbler 40 oz", "Glacierline", "Home & Kitchen", 45.00, 44.00, 40.00, None, 900, 3000, 25, 11.0, 4.5, 4.5, 1.4, ""),
    ("B0DEMO0016", "000000100161", "Paperwell Sticky Notes 3x3, 24 Pads", "Paperwell", "Office Products", 12.99, 12.80, 11.49, None, 3900, 700, 10, 7.0, 3.5, 3.5, 1.3, ""),
    ("B0DEMO0017", "000000100178", "Trailhead Camp Lantern, Rechargeable", "Trailhead", "Sports & Outdoors", 29.99, 30.50, 27.99, None, 19500, 160, 5, 8.0, 5.0, 5.0, 1.2, ""),
    ("B0DEMO0018", "000000100185", "Pawsome Cat Litter Deodorizer 20 oz", "Pawsome", "Pet Supplies", 9.49, 9.30, 8.79, None, 7300, 380, 6, 6.5, 3.2, 3.2, 1.35, ""),
    ("B0DEMO0019", "000000100192", "Hearth Coffee Cold Brew Pitcher 2 qt", "Hearth Coffee", "Home & Kitchen", 26.99, 27.20, 24.50, None, 13400, 240, 4, 12.5, 5.5, 5.5, 1.9, ""),
    ("B0DEMO0020", "000000100208", "SnapClick Puzzle 1000 pc, Coastal Town", "SnapClick", "Toys & Games", 21.99, 19.50, 17.99, None, 9100, 330, 5, 10.0, 8.0, 2.4, 1.6, ""),
    ("B0DEMO0021", "000000100215", "Ridgeway Cordless Drill 20V Kit", "Ridgeway", "Tools & Home Improvement", 89.00, 92.00, 84.00, None, 6800, 280, 7, 13.0, 11.0, 4.5, 5.8, ""),
    ("B0DEMO0022", "000000100222", "Brightline Glass Cleaner Refill 2 x 64 oz", "Brightline", "Health & Household", 16.49, 16.10, 14.99, None, 30200, 70, 3, 11.0, 7.0, 4.0, 8.9, ""),
    ("B0DEMO0023", "000000100239", "Lumina Smart Plug, 4 Pack", "Lumina", "Electronics", 25.99, 24.99, 21.99, None, 2400, 1100, 9, 6.2, 5.0, 3.6, 0.9, ""),
    ("B0DEMO0024", "000000100246", "SnapClick Family Board Game Night", "SnapClick", "Toys & Games", 24.99, 24.00, 22.00, None, 4300, 650, 7, 10.5, 10.5, 2.7, 1.9, ""),
]

DEMO_LEADS = [
    # retailer, url, title, upc, buy cost, cashback
    ("Walmart", "https://www.walmart.com/", "Brightline Disinfecting Wipes 3pk", "000000100017", 9.48, 0.01),
    ("Target", "https://www.target.com/", "Kettle & Oak 10in Cast Iron Skillet - Clearance", "000000100024", 11.50, 0.02),
    ("Walgreens", "https://www.walgreens.com/", "Paperwell Gel Pens 24ct", "000000100031", 4.49, 0.03),
    ("Kohl's", "https://www.kohls.com/", "Trailhead 32oz Insulated Bottle", "000000100048", 11.99, 0.04),
    ("Target", "https://www.target.com/", "Pawsome Dental Chews L 30ct", "000000100055", 11.25, 0.02),
    ("Walmart", "https://www.walmart.com/", "BlockWorks Marble Run 120pc - Rollback", "000000100062", 14.99, 0.01),
    ("Home Depot", "https://www.homedepot.com/", "Hearth Coffee Medium Roast 72ct", "000000100079", 22.99, 0.02),
    ("Best Buy", "https://www.bestbuy.com/", "Lumina LED Desk Lamp USB", "000000100086", 11.24, 0.02),
    ("Target", "https://www.target.com/", "FreshFold Laundry Pods 81ct", "000000100093", 13.49, 0.02),
    ("Kohl's", "https://www.kohls.com/", "Northpeak Kids Rain Jacket", "000000100109", 22.00, 0.04),
    ("Walmart", "https://www.walmart.com/", "Kettle & Oak Spatula Set 5pc", "000000100116", 6.97, 0.01),
    ("CVS", "https://www.cvs.com/", "GlowCare Vit C Serum", "000000100123", 11.99, 0.03),
    ("Target", "https://www.target.com/", "BlockWorks Magnetic Tiles 60pc", "000000100130", 27.99, 0.02),
    ("Lowe's", "https://www.lowes.com/", "Greenhaven Raised Bed 4x2", "000000100147", 34.98, 0.02),
    ("Target", "https://www.target.com/", "Glacierline Tumbler 40oz", "000000100154", 24.00, 0.02),
    ("Walgreens", "https://www.walgreens.com/", "Paperwell Sticky Notes 24pk", "000000100161", 6.99, 0.03),
    ("Best Buy", "https://www.bestbuy.com/", "Trailhead Rechargeable Lantern", "000000100178", 14.99, 0.02),
    ("Walmart", "https://www.walmart.com/", "Pawsome Litter Deodorizer", "000000100185", 4.12, 0.01),
    ("Kohl's", "https://www.kohls.com/", "Hearth Cold Brew Pitcher", "000000100192", 11.24, 0.04),
    ("Target", "https://www.target.com/", "SnapClick 1000pc Coastal Town", "000000100208", 6.49, 0.02),
    ("Home Depot", "https://www.homedepot.com/", "Ridgeway 20V Drill Kit", "000000100215", 49.00, 0.02),
    ("Walmart", "https://www.walmart.com/", "Brightline Glass Cleaner Refill 2pk", "000000100222", 7.94, 0.01),
    ("Best Buy", "https://www.bestbuy.com/", "Lumina Smart Plug 4pk", "000000100239", 12.99, 0.02),
    ("Target", "https://www.target.com/", "SnapClick Board Game Night", "000000100246", 9.99, 0.02),
]


def _record(row) -> ProductData:
    (asin, upc, title, brand, cat, bb, avg90, min90, amz, rank, monthly, fba, L, W, H, lb, flags) = row
    h = int(hashlib.md5(asin.encode()).hexdigest()[:6], 16)
    return ProductData(
        asin=asin, title=title, brand=brand, category=cat, upc=upc, image="",
        buybox_price=bb, new_price=bb, amazon_price=amz, avg30=round((bb + avg90) / 2, 2), avg90=avg90,
        min90=min90, max90=round(max(bb, avg90) * 1.08, 2), sales_rank=rank, rank_avg90=int(rank * 1.05),
        rank_drops30=int(monthly * 0.7), rank_drops90=int(monthly * 2.1), monthly_sold=monthly,
        fba_offers=fba, fbm_offers=h % 5, buybox_is_amazon=bool(amz and amz <= bb), buybox_is_fba=True,
        amazon_oos_pct90=None if amz else 100.0, length_in=L, width_in=W, height_in=H, weight_lb=lb,
        hazmat="hazmat" in flags, adult=False, gated="gated" in flags, referral_pct=None,
        source="demo", fetched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )


CATALOG = {r[0]: _record(r) for r in _C}
BY_UPC = {r[1]: r[0] for r in _C}
# fictional brand that stands in for an IP-complaint brand in demo mode
DEMO_IP_BRANDS = {"Glacierline"}


class DemoProvider(Provider):
    name = "demo"

    def get_products(self, asins: list) -> list:
        return [CATALOG[a.strip().upper()] for a in asins if a and a.strip().upper() in CATALOG]

    def lookup_codes(self, codes: list) -> dict:
        out = {}
        for c in codes:
            c = (c or "").strip()
            a = BY_UPC.get(c) or BY_UPC.get(c.zfill(12))
            if a:
                out[c] = [CATALOG[a]]
        return out
