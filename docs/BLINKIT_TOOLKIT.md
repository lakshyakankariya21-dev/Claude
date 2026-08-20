# Blinkit / competitor catalogue toolkit — handoff notes

Context for an AI or engineer picking this up cold. Everything here lives in
`lakshyakankariya21-dev/Claude`, branch `claude/blinkit-item-mapping-9uyjmw`.

---

## 1. Business context

Shubham K Mart is an Indian retail chain running **GoFrugal** as its POS/ERP,
with stores in three regions: `RJ` (Rajasthan), `GJ` (Gujarat), `CG`
(Chhattisgarh). A separate script (`daily_update.py`, not in this repo) pulls
GoFrugal reports nightly and maintains a rolling 14-month tree:

```
~/Documents/stock analysis/rolling/
    raw/all_stores/YYYY-MM-DD_2065.csv    raw all-stores sales download
    raw/<REGION>/daily/YYYY-MM-DD_2065.csv
    raw/<REGION>/monthly/sales_YYYY-MM_2065.csv
    raw/<REGION>/stock/current_stock_3434.csv
    stock/YYYY-MM-DD_1248.csv             stock snapshots
    stock/latest_13081.csv                item master
    masters/brand_master.csv
```

GoFrugal report numbers referenced throughout:

| Report | Contents | Key columns |
|--------|----------|-------------|
| `2065` | Item-wise sales | `Outlet Name`, `Item Code`, `Item Name`, `DEPARTMENT`, `CATEGORY`, `GRUPS`, `SUBGROUP`, `BRAND`, `Net Sales Qty`, `Net Sales Amt` |
| `1248` | Current stock snapshot | `Outlet Name`, `Item Code`, `Item Name`, `Current Stock`, `Stock Value(Selling)`, `Location` |
| `13081` | Item master | `Item code`, `Item Name`, `Mfr Name`, `Major Category` |
| `3434` | Stock, enriched | `Outlet&Itemcode`, `DEPARTMENT`, `CATEGORY`, `GRUPS`, `SUBGROUP`, `BRAND`, `Itemcode`, `Closing Qty` |

**GoFrugal CSV quirk:** exports carry 2–3 preamble lines above the real header
row (chain name, report title, `DATE : from DD-MM-YYYY DD-MM-YYYY`). Every
reader here seeks to the header token before parsing. Do not assume row 0 is
the header.

The goal of this toolkit: compare our assortment and pricing against
quick-commerce competitors (Blinkit; DMart and Zepto/Instamart intended).

---

## 2. The competitor data

`data/blinkit_full_catalog.csv` — 42,570 rows, scraped elsewhere (the scraper
is **not** in this repo):

```
item_id,name,variant,image_url,price,mrp,category_path
19934,Mysore Sandal Soap 75 g,75 g,https://cdn.grofers.com/...png,38.0,,Beauty & Personal Care > Bath & Body > Bathing Soaps
```

- `category_path` is a 3-level `L1 > L2 > L3` string; 301 distinct leaves.
- `variant` is the reliable pack-size field. `name` sometimes repeats it.
- `mrp` is frequently blank; `price` is the selling price.
- **No brand column** and **no barcode/EAN.** Both facts drive the design below.
- The file does not record which city/pincode it was pulled against. The
  request that prompted it said Mumbai; that is unverified.

Gitignored (`data/*.csv`) — it is 9 MB and not committed.

---

## 3. The central problem

Our catalogue and the competitor catalogue **share no join key.** No barcode on
their side, no competitor ID on ours. Names are the only bridge, and they are
written in different dialects:

```
ours (POS):      MYSORE SANDAL SOAP 75GM
Blinkit:         Mysore Sandal Soap 75 g          variant: "75 g"
```

Everything in `scripts/` is machinery for crossing that gap without
manufacturing false confidence.

---

## 4. Files

### `scripts/item_sources.py` — loaders

Two ways to get *our* item universe:

- **Sales tree (preferred).** Point at a directory; it walks for 2065 files,
  dedupes by `Item Code` across regions and months, keeps the longest `Item
  Name` seen (POS exports truncate inconsistently), and sums `Net Sales
  Qty`/`Amt`. Output is sorted by sales value, so a truncated review pass
  covers the money first.
  - **Critical:** the rolling tree holds the same sales three times over
    (`all_stores/`, `<region>/daily/`, `<region>/monthly/`). `find_sales_files()`
    picks monthly first, then daily, then anything — globbing all three would
    triple-count every item. Do not "simplify" this to `**/*2065*.csv`.
- **Single master/stock export.** 13081, 3434 or 1248; columns auto-detected,
  overridable with `--code-col/--name-col/--brand-col`.

Both return `[{code, name, brand, dept, sales_qty, sales_amt}]`.

### `scripts/blinkit_item_map.py` — item-level matcher

Maps our items to Blinkit items (or the reverse, `--direction blinkit`).

Pipeline:

1. **Normalise** — lowercase, `&`→`and`, apostrophes *deleted* not spaced
   (`ching's`→`chings`, never `ching s`), periods kept only between digits
   (`1.5` survives, `co.`→`co`).
2. **Parse pack size** into base units via the `UNITS` table: `g`/`gm`/`gms`→g,
   `kg`→g×1000, `ml`, `l`/`ltr`/`litre`→ml×1000, `pc`/`pcs`/`unit`/`n`→pcs.
   Handles multipacks in both orders — `3 x 125 g` and `125 g x 3` → 375 g,
   count 3. Blinkit's `variant` is parsed before its `name`.
3. **Block candidates** — `Index` is an IDF-weighted inverted token index.
   Tokens appearing in >5% of the catalogue (`oil`, `soap`) are skipped as
   index keys: they select thousands of rows without narrowing anything. This
   is what makes 20k×42k tractable (~1 min) instead of an 850M-comparison cross
   product. **Blocking only decides what gets compared, never what wins.**
4. **Score** each candidate 0–100:
   - name: `0.7 × max(token_set_ratio, token_sort_ratio) + 0.3 × WRatio`.
     `token_set_ratio` alone rates "amul butter" vs "amul butter cookies" 100,
     so `WRatio` tempers it by punishing extra words.
   - size agreement (`size_score`): exact 1.0, within 5% 0.8, g↔ml with equal
     magnitude 0.9, mismatch 0.0, unknown 0.5 (neutral — cannot confirm *or*
     deny). A confirmed mismatch also subtracts a flat 12 points.
   - brand agreement (`brand_score`): our brand words present in their name.
     Returns `None` when we have no brand, and the weights redistribute.
   - Weights: with brand `0.66·name + 18·size + 16·brand`; without,
     `0.78·name + 22·size`.
5. **Tier** — `exact ≥92`, `strong ≥82`, `likely ≥70`, `weak ≥58`, else `none`.
6. **Ambiguity demotion** — if more than one *distinct* catalogue item scores
   within 2.0 points of the winner, the tier drops one level and `size_note`
   records `ambiguous(N near-identical)`. This catches shade/colour/flavour
   siblings (`Nail Paint 40-Lunar` vs `46-Uranus`) and outright duplicate
   catalogue rows, which a POS name that never carried the shade genuinely
   cannot resolve. Without this the report presents a coin-flip as an exact
   match.

Output columns: our code/name/brand/dept/sales, their id/name/variant/price/
mrp/category, `score`, `tier`, `near_ties`, `size_note`. Because their price and
MRP ride along, the file doubles as a price-comparison sheet. A closing summary
reports coverage as a **share of net sales**, not just item count — the
unmatched tail is mostly slow movers, so item-count coverage understates it.

**Measured:** 94.8% top-1 accuracy, 96.3% precision on `exact`+`strong`.

### `scripts/brand_infer.py` — brand inference from names

Competitor exports have no brand column, and a gap analysis is meaningless
without one. Brands are learned from the catalogue's own shape rather than a
hardcoded list. For every 1–4 token prefix: how many SKUs start with it, and
how many **distinct words follow it** (branching factor).

- A brand root has many SKUs *and* many continuations. `tata` → 297 SKUs,
  10 continuations → brand.
- `dot` → 67 SKUs but only ever one continuation → not a brand alone → extend
  to `dot and key`.
- `GENERIC_OPENERS` (`the`, `organic`, `whole`, `fresh`, `premium`…) are never
  brands alone and always extend → `whole farm`, `the bakers dozen`.
- Honorifics (`dr`, `mr`, `st`) are in that set too, which is what keeps
  **Dr. Oetker** from merging with **Dr. Morepen**.
- An explicit trailing `" - Brandname"` (1,785 Blinkit items) wins — but **only
  if that trailing phrase recurs ≥3 times** across the catalogue. Without the
  recurrence check, `Farmley Makha Shaka - Achaari Stix` names a *flavour* as
  the brand instead of Farmley.
- Returns `(brand_root, sub_brand)`. Root is the granularity a category buyer
  thinks in: `tata`, with `tata sampann` as sub-brand. `sub_brand` takes the
  *shortest* qualifying longer prefix, else it drifts to `tata sampann
  unpolished`, which is a product line, not a brand.

Yields 6,153 roots over the Blinkit catalogue; 2,436 have ≥3 SKUs.

`brand_vocab()` builds our side's vocabulary from the `BRAND`/`Mfr Name` column
**plus** brands inferred from our own item names, because that column is often
blank or holds a manufacturer.

### `scripts/catalog_gap.py` — assortment gap report

What a competitor stocks that we don't. Two outputs: `brand_gap.csv`,
`category_gap.csv`.

**The rule that matters:** a brand is called missing only when *both* checks
fail —

1. its name is absent from our brand vocabulary (fuzzy ≥88, so `DOT AND KEY`
   still finds `Dot & Key`, while `Nivea` and `Niine` stay apart); **and**
2. none of its SKUs matches one of our items by name (≥82 via the matcher
   above; probes the 8 highest-priced SKUs per brand).

Check 2 exists because our `BRAND` column often holds a **manufacturer**
(`HINDUSTAN UNILEVER`) where the competitor names the **consumer brand**
(`Dove`). On names alone the report would tell a buyer to onboard a brand
already sitting on the shelf — the most expensive error this report can make.

**Category coverage is deliberately not taxonomy alignment.** Their
`Grocery & Kitchen > Dairy > Butter` and our `DEPARTMENT/CATEGORY/GRUPS/SUBGROUP`
tree do not correspond, and forcing a mapping would invent precision. Instead
each competitor category is scored by how many of *its own* items we carry;
categories at ≤25% coverage are reported.

`load_competitor()` detects column names (`name`/`product_name`/`title`,
`category_path`/`sub_category`/`breadcrumb`, `price`/`selling_price`…), so a
scraped DMart or Zepto export needs no reshaping. `--catalog` is repeatable;
`--both-only` reports brands missing across every catalogue supplied.

---

## 5. Running it

```bash
pip install pandas rapidfuzz

# item mapping (defaults to the rolling sales tree)
python3 scripts/blinkit_item_map.py \
    --blinkit data/blinkit_full_catalog.csv \
    --out     data/blinkit_item_map.csv
    # --ours <dir|glob|master.csv>  --direction blinkit  --top-k 3  --min-score 70

# assortment gap
python3 scripts/catalog_gap.py \
    --catalog blinkit=data/blinkit_full_catalog.csv \
    --catalog dmart=data/dmart_mumbai.csv \
    --out-dir data/gap/            # --both-only  --min-skus 3

# tests (need data/blinkit_full_catalog.csv present; they skip without it)
python3 tests/test_blinkit_item_map.py /tmp/work
python3 tests/test_catalog_gap.py      /tmp/work
```

### How the tests work

No real item master is available, so both suites **synthesise ground truth**:
they take real Blinkit rows and mangle the names into POS dialect (uppercase,
`75 g`→`75GM`, marketing words dropped, truncation), then check the code
recovers the original. `test_catalog_gap.py` additionally carves the catalogue
into carried/not-carried brands and plants 10 brands whose `Mfr Name` is a
distributor, verifying the item-level fallback rescues them — currently 0/80
false positives and 10/10 rescued. `test_blinkit_item_map.py` also builds a
fake rolling tree (2 regions × 2 months, preamble, per-outlet duplicate rows,
decoy `daily/` and `all_stores/` copies) to prove the deduper does not
triple-count.

Accuracy assertions are regression gates: ≥80% top-1, ≥95% exact+strong
precision. Scoring-weight changes should be validated against them.

---

## 6. State, gaps, and traps

**Not present / unresolved:**

- **Our item data has never been loaded.** No item master or sales files exist
  in the working environment, so every accuracy figure above comes from
  synthetic fixtures. No real mapping has been produced yet.
- **DMart was never fetched.** `dmart.in` is blocked by the cloud environment's
  network egress policy (gateway 403 on CONNECT). `cdn.grofers.com` is blocked
  too, so image work cannot run there either. Both need a permitted environment
  or a local run.
- **No image-extraction code exists in this repo.** The catalogue's `image_url`
  column was populated by a scraper written elsewhere; it is not here, in any
  branch or commit. Zepto and Instamart have no code here at all.
- **City is unverified.** Nothing in the Blinkit file identifies a pincode.

**Traps for anyone editing:**

- Don't glob all 2065 files together (triple-counts). See §4.
- Don't drop the GoFrugal preamble skip.
- Don't treat unknown pack size as a mismatch — it is neutral (0.5) on purpose.
- Don't remove ambiguity demotion to make the tier distribution look better.
- Don't widen `_TRAILING_BRAND` acceptance without the recurrence check.
- Keep files under 500 lines (repo convention); that is why the loaders sit in
  `item_sources.py` and brand logic in `brand_infer.py`.
