"""
Map our GOFRUGAL items <-> Blinkit catalog items.

Matching is name-based with pack-size and brand awareness, because the two
catalogues share no common key (no barcode/EAN in the Blinkit export):

  1. Normalise both names (case, punctuation, unit spellings: GM/gm/g, LTR/l, ...).
  2. Parse the pack size out of the name / variant into base units
     (g, ml, pcs) — including multipacks like "3 x 125 g" -> 375 g x3.
  3. Block candidates with an IDF-weighted inverted index (rare tokens first)
     so we never do 20k x 42k full comparisons.
  4. Score each candidate: fuzzy name similarity + brand agreement + size
     agreement. A confirmed size MISMATCH is a hard penalty — 75 g and 125 g
     of the same soap are different items and must not collapse into one.
  5. Emit every our-item with its best Blinkit match and a confidence tier
     (exact / strong / likely / weak / none) so the weak tail can be reviewed
     by hand instead of silently trusted.

--ours accepts either a DIRECTORY of 2065 sales files (default: the rolling
tree) or a single item master / stock export:

  directory / glob    -> every 2065 sales file under it; items are deduped by
                         Item Code and their Net Sales are summed, so the
                         mapping is ranked by what actually sells
  13081 item master   (Item code, Item Name, Mfr Name, Major Category, ...)
  3434 stock export   (Itemcode, Item Name, BRAND, DEPARTMENT, ...)
  1248 stock snapshot (Item Code, Item Name, ...)
  anything else       -> pass --code-col / --name-col / --brand-col
GOFRUGAL preamble lines above the header row are skipped automatically.

Run (default = all regions' monthly sales files in the rolling tree):
  python3 scripts/blinkit_item_map.py \
      --blinkit data/blinkit_full_catalog.csv \
      --out     data/blinkit_item_map.csv

  --ours ~/Documents/"stock analysis"/rolling/raw/RJ   # one region only
  --ours ~/Documents/"stock analysis"/rolling/stock/latest_13081.csv
  --direction blinkit   # one row per BLINKIT item -> best OUR item instead
  --min-score 70        # drop matches below this (default: keep all, tiered)
  --top-k 3             # also write _candidates.csv with the runner-ups
"""

import argparse
import csv
import math
import re
import sys
from collections import defaultdict
from pathlib import Path

from rapidfuzz import fuzz

sys.path.insert(0, str(Path(__file__).parent))
from item_sources import load_blinkit, load_ours  # noqa: E402

# same rolling tree daily_update.py writes its region-split 2065 files into
ROLLING_RAW = Path.home() / "Documents" / "stock analysis" / "rolling" / "raw"

# ---------------------------------------------------------------------------
# NORMALISATION
# ---------------------------------------------------------------------------

# unit spelling -> (canonical base unit, multiplier to that base unit)
UNITS = {
    "g": ("g", 1), "gm": ("g", 1), "gms": ("g", 1), "gram": ("g", 1),
    "grams": ("g", 1), "gr": ("g", 1),
    "kg": ("g", 1000), "kgs": ("g", 1000), "kilo": ("g", 1000),
    "ml": ("ml", 1), "mls": ("ml", 1),
    "l": ("ml", 1000), "lt": ("ml", 1000), "ltr": ("ml", 1000),
    "ltrs": ("ml", 1000), "litre": ("ml", 1000), "liter": ("ml", 1000),
    "litres": ("ml", 1000), "liters": ("ml", 1000),
    "pc": ("pcs", 1), "pcs": ("pcs", 1), "piece": ("pcs", 1),
    "pieces": ("pcs", 1), "unit": ("pcs", 1), "units": ("pcs", 1),
    "n": ("pcs", 1), "no": ("pcs", 1), "nos": ("pcs", 1), "u": ("pcs", 1),
    "pack": ("pcs", 1), "packs": ("pcs", 1), "pkt": ("pcs", 1),
    "tab": ("pcs", 1), "tabs": ("pcs", 1), "tablet": ("pcs", 1),
    "tablets": ("pcs", 1), "cap": ("pcs", 1), "caps": ("pcs", 1),
    "sachet": ("pcs", 1), "sachets": ("pcs", 1),
}

# words that carry no matching signal — dropped before scoring/indexing
STOP = {
    "the", "and", "with", "for", "of", "in", "a", "an", "combo", "pack",
    "packet", "pouch", "bottle", "box", "jar", "tin", "can", "refill",
    "free", "offer", "new", "pc", "pcs", "piece", "pieces", "unit", "units",
}

_SIZE_RE = re.compile(
    r"(?:(?P<count>\d+)\s*[x*]\s*)?"          # leading multipack: "3 x 125 g"
    r"(?P<qty>\d+(?:\.\d+)?)\s*"
    r"(?P<unit>[a-z]+)"
    r"(?:\s*[x*]\s*(?P<count2>\d+))?",        # trailing multipack: "125 g x 3"
)


def norm_text(s: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace, join digit+unit."""
    s = (s or "").lower()
    s = s.replace("&", " and ")
    # apostrophes are removed, not spaced: "ching's" -> "chings", never "ching s"
    s = re.sub(r"[‘’']", "", s)
    s = re.sub(r"[^a-z0-9.%]+", " ", s)
    s = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", s)     # keep 1.5, drop "co."
    s = re.sub(r"\s+", " ", s).strip()
    return s


def parse_size(*texts: str):
    """Return (base_qty, base_unit, count) or None.

    base_qty is the TOTAL content in base units (a 3 x 125 g pack -> 375, 'g'),
    count is the number of units in the pack (3). Only the first parseable size
    in the given texts wins, so pass the most reliable source first (Blinkit's
    'variant' column beats its free-text name)."""
    for t in texts:
        n = norm_text(t)
        if not n:
            continue
        for m in _SIZE_RE.finditer(n):
            unit = m.group("unit")
            if unit not in UNITS:
                continue
            base, mult = UNITS[unit]
            qty = float(m.group("qty")) * mult
            count = int(m.group("count") or m.group("count2") or 1)
            return (round(qty * count, 3), base, count)
    return None


def tokens(name: str) -> list[str]:
    """Matching tokens: normalised words, minus stopwords and bare unit words."""
    out = []
    for w in norm_text(name).split():
        if w in STOP or w in UNITS:
            continue
        if len(w) == 1 and not w.isdigit():
            continue
        out.append(w)
    return out


# ---------------------------------------------------------------------------
# INDEX + SCORING
# ---------------------------------------------------------------------------

class Index:
    """IDF-weighted inverted token index over the target catalogue.

    Candidate generation only — it decides WHICH items are worth a full fuzzy
    comparison, never which one wins. Tokens present in more than `df_cap` of
    the catalogue ('oil', 'soap') are skipped as index keys since they select
    thousands of rows without narrowing anything."""

    def __init__(self, records: list[dict], df_cap_frac: float = 0.05):
        self.records = records
        self.postings: dict[str, list[int]] = defaultdict(list)
        for i, rec in enumerate(records):
            for t in set(rec["_tokens"]):
                self.postings[t].append(i)
        n = max(1, len(records))
        self.df_cap = max(50, int(n * df_cap_frac))
        self.idf = {t: math.log(n / len(p)) for t, p in self.postings.items()}

    def candidates(self, toks: list[str], limit: int = 60) -> list[int]:
        scores: dict[int, float] = defaultdict(float)
        for t in set(toks):
            post = self.postings.get(t)
            if not post or len(post) > self.df_cap:
                continue
            w = self.idf[t]
            for i in post:
                scores[i] += w
        if not scores:
            # every token was too common — fall back to the rarest of them
            rare = sorted((t for t in set(toks) if t in self.postings),
                          key=lambda t: len(self.postings[t]))[:2]
            for t in rare:
                for i in self.postings[t]:
                    scores[i] += self.idf[t]
        return [i for i, _ in sorted(scores.items(), key=lambda kv: -kv[1])[:limit]]


def size_score(a, b) -> tuple[float, str]:
    """Agreement between two parsed sizes -> (0..1, note)."""
    if a is None or b is None:
        return 0.5, "size-unknown"          # neutral: can't confirm or deny
    if a[1] != b[1]:
        # g vs ml is usually the same physical pack described differently
        if {a[1], b[1]} == {"g", "ml"} and abs(a[0] - b[0]) < 1e-6:
            return 0.9, "size-ok-unit-swap"
        return 0.25, f"size-unit-diff({a[1]}/{b[1]})"
    hi, lo = max(a[0], b[0]), min(a[0], b[0])
    if lo <= 0:
        return 0.5, "size-unknown"
    ratio = lo / hi
    if ratio >= 0.995:
        return 1.0, "size-exact"
    if ratio >= 0.95:
        return 0.8, "size-close"
    return 0.0, f"size-mismatch({a[0]:g}{a[1]}/{b[0]:g}{b[1]})"


def brand_score(our_brand: str, blinkit_name: str) -> float | None:
    """1.0 if our brand/manufacturer words appear in the Blinkit name, else 0.
    None when we have no brand on our side (weight is redistributed)."""
    b = [w for w in tokens(our_brand) if len(w) > 2]
    if not b:
        return None
    hay = " " + norm_text(blinkit_name) + " "
    hits = sum(1 for w in b if f" {w} " in hay)
    if hits:
        return 1.0
    # brands are often abbreviated on one side (HUL vs Hindustan Unilever)
    return 1.0 if fuzz.partial_ratio(norm_text(our_brand), hay) >= 90 else 0.0


def score_pair(src: dict, tgt: dict) -> tuple[float, str]:
    """Combined 0..100 confidence that src and tgt are the same product."""
    a, b = src["_norm"], tgt["_norm"]
    name = max(fuzz.token_set_ratio(a, b), fuzz.token_sort_ratio(a, b))
    # token_set_ratio alone rates "amul butter" vs "amul butter cookies" 100,
    # so temper it with a length-aware ratio that punishes the extra words.
    name = 0.7 * name + 0.3 * fuzz.WRatio(a, b)

    ssc, snote = size_score(src["_size"], tgt["_size"])
    bsc = brand_score(src.get("brand", ""), tgt["name"])

    if bsc is None:
        total = 0.78 * name + 22.0 * ssc
    else:
        total = 0.66 * name + 18.0 * ssc + 16.0 * bsc
    if snote.startswith("size-mismatch") or snote.startswith("size-unit-diff"):
        total -= 12.0                        # different pack = different item
    if bsc == 0.0:
        total -= 8.0                         # brand contradicted, not just absent
    return max(0.0, min(100.0, total)), snote


TIERS = ((92, "exact"), (82, "strong"), (70, "likely"), (58, "weak"))
ORDER = ["exact", "strong", "likely", "weak", "none"]

# Two candidates within this many points are not distinguishable by name alone.
AMBIGUOUS_GAP = 2.0


def tier(score: float) -> str:
    for cut, label in TIERS:
        if score >= cut:
            return label
    return "none"


def demote(t: str) -> str:
    """One tier down — used when the winner isn't a clear winner."""
    return ORDER[min(ORDER.index(t) + 1, len(ORDER) - 1)]


def count_ties(scored: list, best_score: float) -> int:
    """How many DIFFERENT catalogue items score within AMBIGUOUS_GAP of the best.

    Shade/colour/flavour siblings ("Nail Paint 40-Lunar" vs "46-Uranus") and
    outright duplicate catalogue rows are indistinguishable from a POS name that
    never carried the shade. Counting them lets us flag the row for review
    instead of presenting a coin-flip as an exact match."""
    ids = {c[2]["item_id"] if "item_id" in c[2] else c[2]["code"]
           for c in scored if best_score - c[0] <= AMBIGUOUS_GAP}
    return len(ids)


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def prep(records: list[dict], size_fields) -> None:
    for r in records:
        r["_norm"] = norm_text(r["name"])
        r["_tokens"] = tokens(r["name"])
        r["_size"] = parse_size(*(r.get(f, "") for f in size_fields))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ours", type=Path, default=ROLLING_RAW,
                    help=f"2065 sales dir, glob, or master file (default: {ROLLING_RAW})")
    ap.add_argument("--blinkit", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--direction", choices=("ours", "blinkit"), default="ours",
                    help="'ours' = one row per our item (default); "
                         "'blinkit' = one row per Blinkit item")
    ap.add_argument("--min-score", type=float, default=0.0,
                    help="drop rows scoring below this (default 0 = keep all)")
    ap.add_argument("--top-k", type=int, default=0,
                    help="also write <out>_candidates.csv with the top K matches")
    ap.add_argument("--limit", type=int, default=0, help="debug: only N source rows")
    ap.add_argument("--code-col"), ap.add_argument("--name-col")
    ap.add_argument("--brand-col")
    args = ap.parse_args()

    ours = load_ours(args.ours, args.code_col, args.name_col, args.brand_col)
    blink = load_blinkit(args.blinkit)

    prep(ours, ("name",))
    prep(blink, ("variant", "name"))     # variant is the reliable size field

    if args.direction == "ours":
        src, tgt = ours, blink
    else:
        src, tgt = blink, ours
    if args.limit:
        src = src[:args.limit]

    print(f"[index] building over {len(tgt):,} target items")
    idx = Index(tgt)

    rows, cand_rows = [], []
    counts = defaultdict(int)
    for n, s in enumerate(src, 1):
        if n % 2000 == 0:
            print(f"  matched {n:,}/{len(src):,}")
        scored = []
        for i in idx.candidates(s["_tokens"]):
            sc, note = score_pair(s, tgt[i]) if args.direction == "ours" \
                else score_pair(tgt[i], s)
            scored.append((sc, note, tgt[i]))
        scored.sort(key=lambda x: -x[0])

        best = scored[0] if scored else (0.0, "no-candidate", None)
        t = tier(best[0])
        ties = count_ties(scored, best[0]) if scored else 0
        note = best[1]
        if ties > 1:
            t = demote(t)
            note = f"{note}; ambiguous({ties} near-identical)"
        counts[t] += 1
        if best[0] < args.min_score:
            continue

        o, b = (s, best[2]) if args.direction == "ours" else (best[2], s)
        rows.append(_row(o, b, best[0], note, t, ties))

        for sc, cnote, cand in scored[:args.top_k]:
            co, cb = (s, cand) if args.direction == "ours" else (cand, s)
            cand_rows.append(_row(co, cb, sc, cnote, tier(sc), ties))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    _write(args.out, rows)
    print(f"\n[out] {len(rows):,} rows -> {args.out}")
    if args.top_k:
        cpath = args.out.with_name(args.out.stem + "_candidates.csv")
        _write(cpath, cand_rows)
        print(f"[out] {len(cand_rows):,} candidate rows -> {cpath}")

    total = max(1, sum(counts.values()))
    print("\n[summary] confidence tiers")
    for _, label in TIERS + ((0, "none"),):
        c = counts.get(label, 0)
        print(f"  {label:8s} {c:7,d}  {c/total:6.1%}")
    print("  review anything below 'strong' before using it for pricing.")

    # When the source was the sales tree, item COUNT understates coverage —
    # what matters is how much of the money has a Blinkit price to compare to.
    amt_all = sum(_f(r["our_sales_amt"]) for r in rows)
    if amt_all > 0:
        good = sum(_f(r["our_sales_amt"]) for r in rows
                   if r["tier"] in ("exact", "strong"))
        print(f"\n[coverage] Rs {good:,.0f} of Rs {amt_all:,.0f} net sales "
              f"({good/amt_all:.1%}) matched at exact/strong confidence")


def _f(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


FIELDS = ["our_code", "our_name", "our_brand", "our_dept",
          "our_sales_qty", "our_sales_amt",
          "blinkit_item_id", "blinkit_name", "blinkit_variant",
          "blinkit_price", "blinkit_mrp", "blinkit_category",
          "score", "tier", "near_ties", "size_note"]


def _row(o, b, score, note, t, ties=0) -> dict:
    if b is None:
        b = {}
    return {
        "our_code": o.get("code", ""), "our_name": o.get("name", ""),
        "our_brand": o.get("brand", ""), "our_dept": o.get("dept", ""),
        "our_sales_qty": o.get("sales_qty", ""),
        "our_sales_amt": o.get("sales_amt", ""),
        "blinkit_item_id": b.get("item_id", ""), "blinkit_name": b.get("name", ""),
        "blinkit_variant": b.get("variant", ""), "blinkit_price": b.get("price", ""),
        "blinkit_mrp": b.get("mrp", ""), "blinkit_category": b.get("category_path", ""),
        "score": f"{score:.1f}", "tier": t, "near_ties": ties, "size_note": note,
    }


def _write(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)


if __name__ == "__main__":
    main()
