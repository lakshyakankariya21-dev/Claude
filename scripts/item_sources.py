"""Loaders for the two sides of the Blinkit mapping.

Our item universe can come from either:
  * an item master  (13081) / stock export (3434, 1248) — one file, one row/item
  * the rolling 2065 SALES files — a whole directory tree, many rows per item

The sales route is usually the better one: it only contains items that actually
sold, it already carries BRAND + DEPARTMENT, and summing Net Sales gives each
item a weight so the mapping can be reviewed top-seller first.
"""

import csv
import io
from collections import defaultdict
from pathlib import Path

SALES_NUM = ("Net Sales Qty", "Net Sales Amt")


def read_csv_skip_preamble(path: Path, header_hints: list[str]) -> list[dict]:
    """Read a CSV whose real header row may sit below a GOFRUGAL preamble."""
    txt = path.read_text(encoding="utf-8", errors="replace")
    pos = -1
    for hint in header_hints:
        pos = txt.find(hint)
        if pos >= 0:
            break
    if pos > 0:
        txt = txt[pos:]
    rows = list(csv.DictReader(io.StringIO(txt)))
    return [r for r in rows if any((v or "").strip() for v in r.values())]


def pick_col(cols, *candidates):
    low = {c.strip().lower(): c for c in cols if c}
    for cand in candidates:
        if cand.lower() in low:
            return low[cand.lower()]
    return None


def _num(v) -> float:
    try:
        return float(str(v).replace(",", "").strip() or 0)
    except ValueError:
        return 0.0


# ---------------------------------------------------------------------------
# 2065 SALES TREE
# ---------------------------------------------------------------------------

def find_sales_files(root: Path) -> list[Path]:
    """Pick ONE non-overlapping set of 2065 files under `root`.

    The rolling tree holds the same sales three times over — raw/all_stores/,
    raw/<region>/daily/ and raw/<region>/monthly/ — so globbing everything would
    triple-count every item. Preference order is monthly (already aggregated,
    widest coverage), then daily, then whatever 2065 files exist."""
    monthly = sorted(root.glob("**/monthly/sales_*_2065.csv"))
    if monthly:
        return monthly
    daily = sorted(root.glob("**/daily/*_2065.csv"))
    if daily:
        return daily
    return sorted(p for p in root.glob("**/*2065*.csv"))


def load_ours_from_sales(paths: list[Path]) -> list[dict]:
    """Aggregate item identity + total Net Sales across every 2065 file given."""
    agg: dict[str, dict] = {}
    totals: dict[str, dict] = defaultdict(lambda: {c: 0.0 for c in SALES_NUM})
    for p in paths:
        rows = read_csv_skip_preamble(p, ["Outlet Name,", "Item Code,"])
        if not rows:
            print(f"  [skip] {p.name}: no data rows")
            continue
        cols = list(rows[0].keys())
        code_c = pick_col(cols, "Item Code", "Item code", "Itemcode")
        name_c = pick_col(cols, "Item Name", "ItemName")
        if not code_c or not name_c:
            print(f"  [skip] {p.name}: no Item Code/Item Name columns")
            continue
        brand_c = pick_col(cols, "BRAND", "Mfr Name")
        dept_c = pick_col(cols, "DEPARTMENT", "Major Category")
        for r in rows:
            code = (r.get(code_c) or "").strip()
            name = (r.get(name_c) or "").strip()
            if not code or not name:
                continue
            cur = agg.get(code)
            # keep the longest name seen — POS exports truncate inconsistently
            if cur is None or len(name) > len(cur["name"]):
                agg[code] = {
                    "code": code, "name": name,
                    "brand": (r.get(brand_c) or "").strip() if brand_c else "",
                    "dept": (r.get(dept_c) or "").strip() if dept_c else "",
                }
            elif brand_c and not cur["brand"]:
                cur["brand"] = (r.get(brand_c) or "").strip()
            for c in SALES_NUM:
                if c in r:
                    totals[code][c] += _num(r[c])
        print(f"  [read] {p.name}: {len(rows):,} rows")

    out = []
    for code, rec in agg.items():
        rec["sales_qty"] = round(totals[code]["Net Sales Qty"], 2)
        rec["sales_amt"] = round(totals[code]["Net Sales Amt"], 2)
        out.append(rec)
    # busiest items first, so a truncated review pass covers the money
    out.sort(key=lambda r: -r["sales_amt"])
    return out


# ---------------------------------------------------------------------------
# SINGLE-FILE MASTER / STOCK EXPORT
# ---------------------------------------------------------------------------

def load_ours_from_master(path: Path, code_col=None, name_col=None,
                          brand_col=None) -> list[dict]:
    rows = read_csv_skip_preamble(path, ["Item code,", "Item Code,", "Itemcode,",
                                         "Outlet&Itemcode,", "Outlet Name,"])
    if not rows:
        raise SystemExit(f"[ours] no data rows in {path}")
    cols = list(rows[0].keys())
    code_col = code_col or pick_col(cols, "Item code", "Item Code", "Itemcode", "code")
    name_col = name_col or pick_col(cols, "Item Name", "ItemName", "name",
                                    "Item Description")
    brand_col = brand_col or pick_col(cols, "BRAND", "Brand", "Mfr Name",
                                      "Manufacturer")
    if not code_col or not name_col:
        raise SystemExit(f"[ours] could not find code/name columns in {cols}\n"
                         f"       pass --code-col / --name-col explicitly")
    dept_col = pick_col(cols, "DEPARTMENT", "Major Category", "Department")

    seen, out = set(), []
    for r in rows:
        code = (r.get(code_col) or "").strip()
        name = (r.get(name_col) or "").strip()
        if not code or not name or code in seen:
            continue
        seen.add(code)
        out.append({
            "code": code, "name": name,
            "brand": (r.get(brand_col) or "").strip() if brand_col else "",
            "dept": (r.get(dept_col) or "").strip() if dept_col else "",
            "sales_qty": "", "sales_amt": "",
        })
    print(f"[ours] {len(out):,} unique items from {path.name} "
          f"(code={code_col!r} name={name_col!r} brand={brand_col!r})")
    return out


def load_ours(target: Path, code_col=None, name_col=None, brand_col=None) -> list[dict]:
    """Dispatch on what `target` is: a directory tree of 2065 sales files,
    a glob pattern, or a single master/stock export."""
    if any(ch in str(target) for ch in "*?["):
        paths = sorted(Path().glob(str(target)))
        if not paths:
            raise SystemExit(f"[ours] no files match {target}")
        print(f"[ours] reading {len(paths)} file(s) matching {target}")
        items = load_ours_from_sales(paths)
    elif target.is_dir():
        paths = find_sales_files(target)
        if not paths:
            raise SystemExit(f"[ours] no 2065 sales files found under {target}")
        print(f"[ours] reading {len(paths)} sales file(s) under {target}")
        items = load_ours_from_sales(paths)
    else:
        return load_ours_from_master(target, code_col, name_col, brand_col)

    amt = sum(i["sales_amt"] for i in items)
    print(f"[ours] {len(items):,} unique items, Rs {amt:,.0f} total net sales")
    return items


# ---------------------------------------------------------------------------
# BLINKIT CATALOGUE
# ---------------------------------------------------------------------------

def load_blinkit(path: Path) -> list[dict]:
    out = []
    for r in csv.DictReader(path.open(encoding="utf-8", errors="replace")):
        name = (r.get("name") or "").strip()
        if not name:
            continue
        out.append({
            "item_id": (r.get("item_id") or "").strip(),
            "name": name,
            "variant": (r.get("variant") or "").strip(),
            "price": (r.get("price") or "").strip(),
            "mrp": (r.get("mrp") or "").strip(),
            "category_path": (r.get("category_path") or "").strip(),
        })
    print(f"[blinkit] {len(out):,} catalog items from {path.name}")
    return out
