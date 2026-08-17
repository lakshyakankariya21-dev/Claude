"""What Blinkit / DMart carry that our item master does not.

Two outputs:
  brand_gap.csv     — brands a competitor stocks and we don't, with SKU counts,
                      price band, and which of their categories they sit in
  category_gap.csv  — competitor categories where our coverage is thin or zero

A brand is only called MISSING when both checks fail:
  1. the brand name is absent from our brand vocabulary (BRAND / Mfr Name column
     plus brands inferred from our own item names), fuzzy-matched so
     "DOT AND KEY" still finds "Dot & Key"; and
  2. none of that brand's competitor SKUs matches one of our items by name.
Check 2 matters because our BRAND column often holds a MANUFACTURER ("HINDUSTAN
UNILEVER") where the competitor names the consumer brand ("Dove") — on name
comparison alone we would report Dove as missing while it sits on our shelves.

Category coverage is deliberately NOT computed by aligning taxonomies: their
"Grocery & Kitchen > Dairy > Butter" and our DEPARTMENT/CATEGORY/GRUPS tree do
not correspond, and forcing a mapping would invent precision. Instead each
competitor category is scored by how many of ITS OWN items we carry.

Run:
  python3 scripts/catalog_gap.py \
      --catalog blinkit=data/blinkit_full_catalog.csv \
      --catalog dmart=data/dmart_mumbai.csv \
      --out-dir data/gap/

  --ours <dir|glob|file>   # default: the rolling 2065 sales tree
  --both-only              # only report gaps present in EVERY catalogue given
  --min-skus 3             # ignore competitor brands thinner than this
"""

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path

from rapidfuzz import fuzz, process

sys.path.insert(0, str(Path(__file__).parent))
from blinkit_item_map import Index, prep, score_pair  # noqa: E402
from brand_infer import BrandExtractor, brand_vocab, norm  # noqa: E402
from item_sources import load_ours  # noqa: E402

ROLLING_RAW = Path.home() / "Documents" / "stock analysis" / "rolling" / "raw"

# A competitor brand counts as "we have it" at or above this fuzzy score against
# our vocabulary. 88 tolerates spelling/spacing drift ("Loreal" / "L'Oreal
# Paris") without merging genuinely different brands ("Nivea" / "Niine").
BRAND_FUZZ = 88
# An item-level match this strong proves we stock the brand even if its name
# never appears in our BRAND column.
ITEM_MATCH = 82
# Categories at or below this coverage are reported as gaps.
THIN_COVERAGE = 0.25


def load_competitor(path: Path, label: str) -> list[dict]:
    """Read a competitor catalogue. Column names are detected, so a scraped DMart
    export does not have to match Blinkit's header exactly."""
    rows = list(csv.DictReader(path.open(encoding="utf-8", errors="replace")))
    if not rows:
        raise SystemExit(f"[{label}] no rows in {path}")
    cols = list(rows[0].keys())

    def col(*cands):
        low = {c.strip().lower(): c for c in cols if c}
        for c in cands:
            if c in low:
                return low[c]
        return None

    name_c = col("name", "product_name", "title", "item_name", "product")
    if not name_c:
        raise SystemExit(f"[{label}] no product-name column in {cols}")
    cat_c = col("category_path", "category", "categories", "breadcrumb", "l3", "sub_category")
    brand_c = col("brand", "brand_name", "manufacturer")
    id_c = col("item_id", "id", "sku", "product_id")
    var_c = col("variant", "pack", "size", "weight", "quantity")
    price_c = col("price", "selling_price", "sp", "offer_price")
    mrp_c = col("mrp", "list_price")

    out = []
    for r in rows:
        name = (r.get(name_c) or "").strip()
        if not name:
            continue
        out.append({
            "source": label,
            "item_id": (r.get(id_c) or "").strip() if id_c else "",
            "name": name,
            "variant": (r.get(var_c) or "").strip() if var_c else "",
            "price": (r.get(price_c) or "").strip() if price_c else "",
            "mrp": (r.get(mrp_c) or "").strip() if mrp_c else "",
            "category_path": (r.get(cat_c) or "").strip() if cat_c else "",
            "given_brand": (r.get(brand_c) or "").strip() if brand_c else "",
        })
    print(f"[{label}] {len(out):,} items from {path.name}"
          f"{' (has brand column)' if brand_c else ' (brand inferred from names)'}")
    return out


def tag_brands(items: list[dict]) -> None:
    """Attach brand_root / sub_brand to every competitor item."""
    ex = BrandExtractor([i["name"] for i in items])
    for i in items:
        if i["given_brand"]:
            i["brand_root"], i["sub_brand"] = norm(i["given_brand"]), ""
        else:
            i["brand_root"], i["sub_brand"] = ex.extract(i["name"])


def _f(v) -> float:
    try:
        return float(str(v).replace(",", "") or 0)
    except ValueError:
        return 0.0


class OurCatalogue:
    """Our items, answering two questions: do we know this brand name, and do we
    stock anything that looks like this product?"""

    def __init__(self, items: list[dict]):
        self.items = items
        self.vocab = brand_vocab(items)
        self.vocab_list = sorted(self.vocab)
        prep(items, ("name",))
        self.index = Index(items)
        print(f"[ours] {len(items):,} items, {len(self.vocab):,} distinct brand names")

    def knows_brand(self, brand: str) -> tuple[bool, str, float]:
        if not brand:
            return False, "", 0.0
        if brand in self.vocab:
            return True, brand, 100.0
        hit = process.extractOne(brand, self.vocab_list, scorer=fuzz.WRatio,
                                 score_cutoff=BRAND_FUZZ)
        if hit:
            return True, hit[0], hit[1]
        return False, "", 0.0

    def best_item_match(self, comp_item: dict) -> float:
        """Top name-match score against our catalogue (0 if nothing close)."""
        best = 0.0
        for i in self.index.candidates(comp_item["_tokens"], limit=25):
            sc, _ = score_pair(self.items[i], comp_item)
            best = max(best, sc)
            if best >= 95:
                break
        return best


def analyse(ours: OurCatalogue, comp: list[dict], min_skus: int):
    """Group a competitor catalogue by brand and decide which brands we lack."""
    by_brand: dict[str, list[dict]] = defaultdict(list)
    for i in comp:
        if i["brand_root"]:
            by_brand[i["brand_root"]].append(i)

    brands = []
    for brand, items in by_brand.items():
        if len(items) < min_skus:
            continue
        known, via, bscore = ours.knows_brand(brand)
        # Probe the brand's biggest SKUs by item name — cheaper than probing all,
        # and enough to prove we stock the brand under another name.
        probe = sorted(items, key=lambda x: -_f(x["price"]))[:8]
        item_best = max((ours.best_item_match(p) for p in probe), default=0.0)
        carried = known or item_best >= ITEM_MATCH
        prices = [_f(i["price"]) for i in items if _f(i["price"]) > 0]
        cats = defaultdict(int)
        for i in items:
            cats[i["category_path"]] += 1
        top_cats = sorted(cats.items(), key=lambda kv: -kv[1])[:3]
        brands.append({
            "brand": brand,
            "source": items[0]["source"],
            "skus": len(items),
            "carried": carried,
            "matched_our_brand": via,
            "brand_score": round(bscore, 1),
            "best_item_match": round(item_best, 1),
            "price_min": f"{min(prices):.0f}" if prices else "",
            "price_max": f"{max(prices):.0f}" if prices else "",
            "top_categories": " | ".join(f"{c} ({n})" for c, n in top_cats),
            "examples": " | ".join(i["name"][:60] for i in items[:3]),
        })
    return brands


def category_coverage(ours: OurCatalogue, comp: list[dict], brands: list[dict]):
    """Per competitor category: what share of its SKUs sit under a brand we carry."""
    carried = {b["brand"] for b in brands if b["carried"]}
    known = {b["brand"] for b in brands}
    cats: dict[str, dict] = defaultdict(
        lambda: {"skus": 0, "have": 0, "thin_brands": defaultdict(int), "source": ""})
    for i in comp:
        cat = i["category_path"] or "(uncategorised)"
        c = cats[cat]
        c["skus"] += 1
        c["source"] = i["source"]
        b = i["brand_root"]
        if b in carried:
            c["have"] += 1
        elif b and b in known:
            c["thin_brands"][b] += 1

    out = []
    for cat, c in cats.items():
        cov = c["have"] / c["skus"] if c["skus"] else 0.0
        missing = sorted(c["thin_brands"].items(), key=lambda kv: -kv[1])[:5]
        out.append({
            "category_path": cat,
            "source": c["source"],
            "competitor_skus": c["skus"],
            "skus_we_carry": c["have"],
            "coverage": f"{cov:.1%}",
            "coverage_num": cov,
            "missing_brands": " | ".join(f"{b} ({n})" for b, n in missing),
        })
    out.sort(key=lambda r: (r["coverage_num"], -r["competitor_skus"]))
    return out


BRAND_FIELDS = ["brand", "source", "skus", "price_min", "price_max",
                "top_categories", "examples", "matched_our_brand",
                "brand_score", "best_item_match"]
CAT_FIELDS = ["category_path", "source", "competitor_skus", "skus_we_carry",
              "coverage", "missing_brands"]


def write(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--catalog", action="append", required=True, metavar="LABEL=PATH",
                    help="competitor catalogue, repeatable (blinkit=..., dmart=...)")
    ap.add_argument("--ours", type=Path, default=ROLLING_RAW)
    ap.add_argument("--out-dir", type=Path, default=Path("data/gap"))
    ap.add_argument("--min-skus", type=int, default=3,
                    help="ignore competitor brands with fewer SKUs (default 3)")
    ap.add_argument("--both-only", action="store_true",
                    help="only report brands missing across EVERY catalogue given")
    args = ap.parse_args()

    sources = {}
    for spec in args.catalog:
        if "=" not in spec:
            raise SystemExit(f"--catalog needs LABEL=PATH, got {spec!r}")
        label, path = spec.split("=", 1)
        sources[label] = Path(path)

    ours = OurCatalogue(load_ours(args.ours))

    all_brands, all_cats = [], []
    per_source: dict[str, set] = {}
    for label, path in sources.items():
        comp = load_competitor(path, label)
        tag_brands(comp)
        prep(comp, ("variant", "name"))
        print(f"[{label}] analysing {len({i['brand_root'] for i in comp}):,} brands")
        brands = analyse(ours, comp, args.min_skus)
        missing = [b for b in brands if not b["carried"]]
        per_source[label] = {b["brand"] for b in missing}
        all_brands += missing
        all_cats += category_coverage(ours, comp, brands)
        print(f"[{label}] {len(missing):,} of {len(brands):,} brands not in our master")

    if args.both_only and len(per_source) > 1:
        common = set.intersection(*per_source.values())
        all_brands = [b for b in all_brands if b["brand"] in common]
        print(f"\n[both-only] {len(common):,} brands missing from ALL "
              f"{len(per_source)} catalogues")

    all_brands.sort(key=lambda b: -b["skus"])
    thin = [c for c in all_cats if c["coverage_num"] <= THIN_COVERAGE
            and c["competitor_skus"] >= args.min_skus]

    write(args.out_dir / "brand_gap.csv", all_brands, BRAND_FIELDS)
    write(args.out_dir / "category_gap.csv", thin, CAT_FIELDS)
    print(f"\n[out] {len(all_brands):,} missing brands -> "
          f"{args.out_dir / 'brand_gap.csv'}")
    print(f"[out] {len(thin):,} thin categories -> {args.out_dir / 'category_gap.csv'}")

    print("\n[top missing brands by SKU count]")
    for b in all_brands[:20]:
        print(f"  {b['skus']:5} SKUs  {b['brand'][:32]:34} "
              f"Rs {b['price_min'] or '?':>6}-{b['price_max'] or '?':<6} "
              f"[{b['source']}]")
    print("\n[thinnest categories]")
    for c in thin[:15]:
        print(f"  {c['coverage']:>6} of {c['competitor_skus']:4} SKUs  "
              f"{c['category_path'][:64]}")


if __name__ == "__main__":
    main()
