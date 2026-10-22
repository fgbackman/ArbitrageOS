"""FNSKU labels as print-ready HTML (Code 128 barcodes drawn as SVG).

Since Mar 31 2026 resellers must put an Amazon barcode (FNSKU) on every unit,
covering the manufacturer barcode. Amazon's label needs the barcode, the
FNSKU, the item name and the condition.
"""
from __future__ import annotations

import html
from typing import Optional

# Code 128 bar/space widths for values 0-106 (106 = stop, 7 elements)
PATTERNS = [
    "212222", "222122", "222221", "121223", "121322", "131222", "122213", "122312", "132212", "221213",
    "221312", "231212", "112232", "122132", "122231", "113222", "123122", "123221", "223211", "221132",
    "221231", "213212", "223112", "312131", "311222", "321122", "321221", "312212", "322112", "322211",
    "212123", "212321", "232121", "111323", "131123", "131321", "112313", "132113", "132311", "211313",
    "231113", "231311", "112133", "112331", "132131", "113123", "113321", "133121", "313121", "211331",
    "231131", "213113", "213311", "213131", "311123", "311321", "331121", "312113", "312311", "332111",
    "314111", "221411", "431111", "111224", "111422", "121124", "121421", "141122", "141221", "112214",
    "112412", "122114", "122411", "142112", "142211", "241211", "221114", "413111", "241112", "134111",
    "111242", "121142", "121241", "114212", "124112", "124211", "411212", "421112", "421211", "212141",
    "214121", "412121", "111143", "111341", "131141", "114113", "114311", "411113", "411311", "113141",
    "114131", "311141", "411131", "211412", "211214", "211232", "2331112",
]
START_B, STOP = 104, 106


def code128b(text: str) -> list:
    """Module widths (bar, space, bar, ...) for text in code set B, with checksum."""
    vals = [START_B]
    for ch in text:
        o = ord(ch)
        if o < 32 or o > 126:
            raise ValueError(f"character {ch!r} not in Code 128 set B")
        vals.append(o - 32)
    check = (vals[0] + sum(i * v for i, v in enumerate(vals[1:], start=1))) % 103
    vals += [check, STOP]
    widths = []
    for v in vals:
        widths.extend(int(c) for c in PATTERNS[v])
    return widths


def barcode_svg(text: str, height: float = 40, module: float = 1.0, quiet: int = 10) -> str:
    widths = code128b(text)
    total = sum(widths) + 2 * quiet
    x, bars = quiet, []
    for i, w in enumerate(widths):
        if i % 2 == 0:
            bars.append(f'<rect x="{x * module:.2f}" y="0" width="{w * module:.2f}" height="{height}"/>')
        x += w
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {total * module:.2f} {height}" '
            f'preserveAspectRatio="none" shape-rendering="crispEdges">' + "".join(bars) + "</svg>")


def _short_title(t: str, n: int = 50) -> str:
    t = " ".join((t or "").split())
    if len(t) <= n:
        return t
    head = n // 2 + 6
    return t[:head].rstrip() + "..." + t[-(n - head - 3):].lstrip()


FORMATS = {
    # name: page css, label width/height (in), columns, rows, margins, gaps
    "avery5160": {"label": "30 per sheet (Avery 5160, 2.625 x 1 in)", "w": 2.625, "h": 1.0, "cols": 3, "rows": 10,
                  "top": 0.5, "left": 0.1875, "gap_x": 0.125, "gap_y": 0.0, "page": "letter"},
    "avery5167": {"label": "80 per sheet (Avery 5167, 1.75 x 0.5 in)", "w": 1.75, "h": 0.5, "cols": 4, "rows": 20,
                  "top": 0.5, "left": 0.28, "gap_x": 0.3, "gap_y": 0.0, "page": "letter"},
    "thermal225": {"label": "Thermal roll 2.25 x 1.25 in", "w": 2.25, "h": 1.25, "cols": 1, "rows": 1,
                   "top": 0, "left": 0, "gap_x": 0, "gap_y": 0, "page": "2.25in 1.25in"},
    "thermal2x1": {"label": "Thermal roll 2 x 1 in", "w": 2.0, "h": 1.0, "cols": 1, "rows": 1,
                   "top": 0, "left": 0, "gap_x": 0, "gap_y": 0, "page": "2in 1in"},
}


def label_html(items: list, fmt: str = "avery5160", condition: str = "New", skip: int = 0) -> str:
    """items = [{fnsku, title, qty}]. skip = blank positions to leave on a used sheet."""
    f = FORMATS[fmt]
    small = f["h"] <= 0.6
    labels = [None] * skip
    for it in items:
        if not it.get("fnsku"):
            continue
        labels.extend([it] * int(it.get("qty") or 1))
    per_page = f["cols"] * f["rows"]
    pages = [labels[i:i + per_page] for i in range(0, len(labels), per_page)] or [[]]
    cells = []
    for page in pages:
        cells.append('<section class="sheet">')
        for lab in page:
            if lab is None:
                cells.append('<div class="lab"></div>')
                continue
            fn = html.escape(lab["fnsku"])
            title = html.escape(_short_title(lab.get("title", ""), 28 if small else 50))
            cells.append(
                f'<div class="lab"><div class="bc">{barcode_svg(lab["fnsku"])}</div>'
                f'<div class="fn">{fn}</div>'
                + ("" if small else f'<div class="tt">{title}</div>')
                + f'<div class="cd">{html.escape(condition)}</div></div>')
        cells.append("</section>")
    page_css = "size: letter; margin: 0" if f["page"] == "letter" else f"size: {f['page']}; margin: 0"
    sheet_w = 8.5 if f["page"] == "letter" else f["w"]
    css = f"""
@page {{ {page_css}; }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; font-family: Arial, Helvetica, sans-serif; color: #000; background: #fff; }}
.sheet {{ width: {sheet_w}in; padding: {f['top']}in 0 0 {f['left']}in; display: grid;
  grid-template-columns: repeat({f['cols']}, {f['w']}in); grid-auto-rows: {f['h']}in;
  column-gap: {f['gap_x']}in; row-gap: {f['gap_y']}in; page-break-after: always; }}
.lab {{ width: {f['w']}in; height: {f['h']}in; padding: {0.03 if small else 0.06}in 0.12in; overflow: hidden;
  display: flex; flex-direction: column; justify-content: center; }}
.bc svg {{ width: 100%; height: {0.22 if small else 0.42}in; display: block; }}
.fn {{ font-size: {6 if small else 8}pt; text-align: center; letter-spacing: .5px; margin-top: 1px; }}
.tt {{ font-size: 7pt; line-height: 1.1; text-align: center; white-space: nowrap; overflow: hidden; }}
.cd {{ font-size: {6 if small else 7}pt; text-align: center; font-weight: bold; }}
.bar {{ font: 13px -apple-system, Helvetica, sans-serif; padding: 10px 14px; background: #f3f1ea; border-bottom: 1px solid #ddd; }}
@media print {{ .bar {{ display: none; }} }}
"""
    n = sum(1 for x in labels if x)
    return (f"<!doctype html><html><head><meta charset='utf-8'><title>FNSKU labels</title><style>{css}</style></head><body>"
            f"<div class='bar'>{n} labels &middot; {html.escape(f['label'])} &middot; print at 100% scale, no margins "
            f"<button onclick='window.print()'>Print</button></div>" + "".join(cells) + "</body></html>")


def demo_fnsku(asin: str) -> str:
    import hashlib
    return "X00" + hashlib.sha1(asin.encode()).hexdigest()[:7].upper()
