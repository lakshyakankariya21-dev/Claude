"""Checks for scripts/brand_infer.py and scripts/catalog_gap.py.

The gap decision is tested against known ground truth: we carve the Blinkit
catalogue into brands we "carry" and brands we don't, synthesise a POS-style
master from the carried half, and then check the report re-derives that split.

The hard case gets its own fixture — brands whose BRAND column holds a
MANUFACTURER rather than the consumer brand ("HINDUSTAN UNILEVER" for Dove).
Name comparison alone reports those as missing, which is the single most
expensive error this report can make: it tells a buyer to onboard a brand that
is already on the shelf.
"""

import csv
import random
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
CATALOG = ROOT / "data" / "blinkit_full_catalog.csv"
sys.path.insert(0, str(SCRIPTS))

from brand_infer import BrandExtractor, brand_vocab, norm  # noqa: E402

SEED = 11
CARRIED_BRANDS = 80        # brands the fake master stocks
MANUFACTURER_BRANDS = 10   # ...of which these hide behind a manufacturer name


def test_norm():
    assert norm("Dot & Key") == "dot and key"
    assert norm("Ching's Secret") == "chings secret"
    assert norm("Dr. Oetker") == "dr oetker"
    print("  norm OK")


def test_brand_extract():
    names = (["Amul Butter", "Amul Ghee", "Amul Cheese", "Amul Milk", "Amul Dahi"]
             + ["Dot & Key Serum", "Dot & Key Sunscreen", "Dot & Key Gel",
                "Dot & Key Cream"]
             + ["The Bakers Dozen Bread", "The Bakers Dozen Bun",
                "The Bakers Dozen Cookie", "The Bakers Dozen Rusk"]
             + [f"Reading Light {i} - Flyngo" for i in range(4)]
             + ["Farmley Makha Shaka - Achaari Stix", "Farmley Dates",
                "Farmley Cashew", "Farmley Almond"])
    ex = BrandExtractor(names)
    assert ex.extract("Amul Butter")[0] == "amul"
    # a single-word brand that is always followed by the same word is not a brand
    assert ex.extract("Dot & Key Serum")[0] == "dot and key"
    # generic opener must extend
    assert ex.extract("The Bakers Dozen Bread")[0] == "the bakers dozen"
    # explicit trailing brand wins when it recurs
    assert ex.extract("Reading Light 9 - Flyngo")[0] == "flyngo"
    # ...but a one-off trailing phrase is a flavour, not a brand
    assert ex.extract("Farmley Makha Shaka - Achaari Stix")[0] == "farmley"
    print("  brand extraction OK")


def test_brand_vocab_uses_both_sources():
    items = [{"brand": "HINDUSTAN UNILEVER", "name": "DOVE SOAP 100GM"},
             {"brand": "", "name": "SURF EXCEL POWDER 1KG"}]
    v = brand_vocab(items)
    assert "hindustan unilever" in v, "BRAND column ignored"
    assert any("dove" in x for x in v), "brand not inferred from our own names"
    print("  brand vocab OK")


# --- fixtures ---------------------------------------------------------------

MANGLE_DROP = {"premium", "natural", "with", "for", "pure", "fresh", "organic"}


def mangle(name: str, variant: str, rng) -> str:
    words = [w for w in name.replace(",", " ").split()
             if w.lower() not in MANGLE_DROP or rng.random() < 0.5]
    out = " ".join(words[:rng.randint(3, max(3, len(words)))]).upper()
    if variant.strip() and rng.random() < 0.8:
        out += " " + variant.upper().replace(" G", "GM").replace(" ML", "ML")
    return " ".join(out.split())


def build_fixtures(tmp: Path):
    """Returns (carried_brands, manufacturer_brands, ours_csv, dmart_csv)."""
    rng = random.Random(SEED)
    rows = list(csv.DictReader(CATALOG.open(encoding="utf-8")))
    ex = BrandExtractor([r["name"] for r in rows])
    by_brand = defaultdict(list)
    for r in rows:
        b, _ = ex.extract(r["name"])
        if b:
            by_brand[b].append(r)

    # only brands with enough SKUs to be reportable at all
    eligible = sorted(b for b, v in by_brand.items() if len(v) >= 6)
    carried = set(rng.sample(eligible, CARRIED_BRANDS))
    mfr_hidden = set(rng.sample(sorted(carried), MANUFACTURER_BRANDS))

    ours = []
    for i, brand in enumerate(sorted(carried)):
        for j, r in enumerate(by_brand[brand][:6]):
            ours.append({
                "Item code": f"C{i:04d}{j}",
                "Item Name": mangle(r["name"], r["variant"], rng),
                # the expensive case: our column names the MANUFACTURER, so the
                # consumer brand appears nowhere in our brand vocabulary
                "Mfr Name": "SOME DISTRIBUTOR PVT LTD" if brand in mfr_hidden
                            else brand.upper(),
                "Major Category": r["category_path"].split(" > ")[0],
            })
    ours_csv = tmp / "ours_master.csv"
    with ours_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(ours[0]))
        w.writeheader()
        w.writerows(ours)

    # A DMart-shaped catalogue: DIFFERENT column names, real brand column, so the
    # loader's column detection and the given_brand path both get exercised.
    dmart = tmp / "dmart_mumbai.csv"
    picks = rng.sample(rows, 4000)
    with dmart.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["sku", "product_name", "brand_name",
                                          "sub_category", "selling_price",
                                          "list_price", "pack"])
        w.writeheader()
        for r in picks:
            b, _ = ex.extract(r["name"])
            w.writerow({"sku": r["item_id"], "product_name": r["name"],
                        "brand_name": b, "sub_category": r["category_path"],
                        "selling_price": r["price"], "list_price": r["mrp"],
                        "pack": r["variant"]})
    return carried, mfr_hidden, ours_csv, dmart


def test_gap_report(tmp: Path):
    carried, mfr_hidden, ours_csv, dmart_csv = build_fixtures(tmp)
    out_dir = tmp / "gap"
    r = subprocess.run(
        [sys.executable, str(SCRIPTS / "catalog_gap.py"),
         "--catalog", f"blinkit={CATALOG}", "--catalog", f"dmart={dmart_csv}",
         "--ours", str(ours_csv), "--out-dir", str(out_dir)],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr[-3000:]
    print(r.stdout[-1500:])

    gap = list((out_dir / "brand_gap.csv").open(encoding="utf-8"))
    reported = {row["brand"] for row in
                csv.DictReader((out_dir / "brand_gap.csv").open(encoding="utf-8"))}
    assert len(gap) > 1, "brand_gap.csv is empty"

    # FALSE POSITIVES: brands we stock, wrongly reported as missing.
    false_pos = carried & reported
    fp_rate = len(false_pos) / len(carried)
    print(f"\n  carried brands wrongly reported missing: "
          f"{len(false_pos)}/{len(carried)} = {fp_rate:.1%}")
    if false_pos:
        print(f"    e.g. {sorted(false_pos)[:8]}")

    # the manufacturer-name subset specifically — these can only be rescued by
    # the item-level match, so they prove that fallback works
    mfr_missed = mfr_hidden & reported
    print(f"  manufacturer-named brands rescued: "
          f"{len(mfr_hidden) - len(mfr_missed)}/{len(mfr_hidden)}")

    cats = list(csv.DictReader((out_dir / "category_gap.csv").open(encoding="utf-8")))
    print(f"  thin categories reported: {len(cats)}")
    assert cats, "category_gap.csv is empty"
    assert all(float(c["coverage"].rstrip("%")) <= 25.0 for c in cats)

    assert fp_rate <= 0.20, f"too many carried brands reported missing ({fp_rate:.1%})"
    assert len(mfr_missed) <= MANUFACTURER_BRANDS * 0.5, \
        "item-level fallback failed to rescue manufacturer-named brands"


if __name__ == "__main__":
    tmp = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp/gap_test")
    tmp.mkdir(parents=True, exist_ok=True)
    test_norm()
    test_brand_extract()
    test_brand_vocab_uses_both_sources()
    if not CATALOG.exists():
        print(f"\nSKIP gap report test — need the Blinkit catalog at {CATALOG}")
        sys.exit(0)
    test_gap_report(tmp)
    print("\nALL TESTS PASSED")
