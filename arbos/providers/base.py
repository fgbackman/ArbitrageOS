"""Common product record every data source fills in."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class ProductData:
    asin: str
    title: str = ""
    brand: str = ""
    category: str = ""
    upc: str = ""
    image: str = ""
    # prices in dollars
    buybox_price: Optional[float] = None
    new_price: Optional[float] = None
    amazon_price: Optional[float] = None
    avg30: Optional[float] = None
    avg90: Optional[float] = None
    min90: Optional[float] = None
    max90: Optional[float] = None
    # demand and competition
    sales_rank: Optional[int] = None
    rank_avg90: Optional[int] = None
    rank_drops30: Optional[int] = None
    rank_drops90: Optional[int] = None
    monthly_sold: Optional[int] = None
    fba_offers: Optional[int] = None
    fbm_offers: Optional[int] = None
    buybox_is_amazon: Optional[bool] = None
    buybox_is_fba: Optional[bool] = None
    amazon_oos_pct90: Optional[float] = None
    # package (inches / pounds)
    length_in: Optional[float] = None
    width_in: Optional[float] = None
    height_in: Optional[float] = None
    weight_lb: Optional[float] = None
    # flags
    hazmat: bool = False
    adult: bool = False
    gated: Optional[bool] = None  # None = unknown (needs SP-API)
    referral_pct: Optional[float] = None
    fba_pick_pack: Optional[float] = None
    source: str = "demo"
    fetched_at: str = ""
    raw: dict = field(default_factory=dict, repr=False)

    def to_dict(self, include_raw: bool = False) -> dict:
        d = asdict(self)
        if not include_raw:
            d.pop("raw", None)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "ProductData":
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known)


class Provider:
    name = "base"

    def get_products(self, asins: list) -> list:
        raise NotImplementedError

    def lookup_codes(self, codes: list) -> dict:
        """UPC/EAN -> list of ProductData."""
        raise NotImplementedError

    def get_product(self, asin: str) -> Optional[ProductData]:
        got = self.get_products([asin])
        return got[0] if got else None
