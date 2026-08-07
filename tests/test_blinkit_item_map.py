"""Accuracy + unit checks for scripts/blinkit_item_map.py.

We have no copy of the real GOFRUGAL item master here, so the accuracy test
synthesises one: take N random Blinkit items and mangle their names the way a
retail POS master actually writes them — UPPERCASE, abbreviated units ("75GM"),
dropped marketing words, truncation — then check the matcher recovers the
original item_id. Ground truth is exact, so precision/recall are real numbers,
not eyeballing.
"""

import csv
import random
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "blinkit_item_map.py"
CATALOG = ROOT / "data" / "blinkit_full_catalog.csv"
sys.path.insert(0, str(SCRIPT.parent))

from blinkit_item_map import parse_size, norm_text, size_score  # noqa: E402

N_SAMPLE = 400
SEED = 7


def test_parse_size():
    assert parse_size("75 g") == (75.0, "g", 1)
    assert parse_size("3 x 125 g") == (375.0, "g", 3)
    assert parse_size("125 g x 3") == (375.0, "g", 3)
    assert parse_size("1 ltr") == (1000.0, "ml", 1)
    assert parse_size("1.5 kg") == (1500.0, "g", 1)
    assert parse_size("", "MYSORE SANDAL SOAP 75GM") == (75.0, "g", 1)
    assert parse_size("1 unit") == (1.0, "pcs", 1)
    assert parse_size("no size here") is None
    print("  parse_size OK")


def test_size_score():
    assert size_score((75, "g", 1), (75, "g", 1))[0] == 1.0
    assert size_score((75, "g", 1), (125, "g", 1))[0] == 0.0
    assert size_score(None, (75, "g", 1))[0] == 0.5
    assert size_score((500, "g", 1), (500, "ml", 1))[0] == 0.9
    print("  size_score OK")


def test_norm_text():
    assert norm_text("Ching's Secret Green Chilli Sauce") == "chings secret green chilli sauce"
    assert norm_text("Dove & Co.  Soap") == "dove and co soap"
    assert norm_text("Tata Salt 1.5 kg") == "tata salt 1.5 kg"
    print("  norm_text OK")


# --- POS-style manglers -----------------------------------------------------

ABBREV = {" g": "GM", " ml": "ML", " kg": "KG", " ltr": "LTR", " l": "LTR"}
DROP = {"premium", "natural", "with", "for", "advanced", "pure", "fresh",
        "daily", "care", "new", "original"}


def mangle(name: str, variant: str, rng: random.Random) -> str:
    words = [w for w in name.replace(",", " ").split()
             if w.lower() not in DROP or rng.random() < 0.4]
    if len(words) > 5 and rng.random() < 0.5:      # POS names get truncated
        words = words[:rng.randint(4, len(words))]
    out = " ".join(words).upper()
    v = variant.strip()
    if v and rng.random() < 0.85:
        for src, dst in ABBREV.items():           # "75 g" -> "75GM"
            if v.lower().endswith(src):
                v = v[: -len(src)].strip() + dst
                break
        out = f"{out} {v.upper()}"
    return " ".join(out.split())


def build_fixture(tmp: Path) -> dict[str, str]:
    rng = random.Random(SEED)
    rows = list(csv.DictReader(CATALOG.open(encoding="utf-8")))
    picked = rng.sample([r for r in rows if r["name"].strip()], N_SAMPLE)
    truth, out = {}, []
    for i, r in enumerate(picked, 1):
        code = f"T{i:05d}"
        truth[code] = r["item_id"]
        out.append({
            "Item code": code,
            "Item Name": mangle(r["name"], r["variant"], rng),
            "Mfr Name": r["name"].split()[0].upper(),   # POS brand ~ first word
            "Major Category": r["category_path"].split(" > ")[0],
        })
    path = tmp / "ours_fixture.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0]))
        w.writeheader()
        w.writerows(out)
    return truth, path


def test_accuracy(tmp: Path):
    truth, ours = build_fixture(tmp)
    out = tmp / "map.csv"
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--ours", str(ours),
         "--blinkit", str(CATALOG), "--out", str(out)],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr[-2000:]
    print(r.stdout[-900:])

    rows = list(csv.DictReader(out.open(encoding="utf-8")))
    assert len(rows) == len(truth)

    # The catalogue contains genuine duplicate rows — same name AND same variant
    # under two item_ids. Landing on either is the same product, so identity is
    # (name, variant), not item_id alone.
    cat = {r["item_id"]: (r["name"].strip(), r["variant"].strip())
           for r in csv.DictReader(CATALOG.open(encoding="utf-8"))}

    by_tier = {}
    hits = 0
    for row in rows:
        want = cat[truth[row["our_code"]]]
        got = cat.get(row["blinkit_item_id"])
        ok = got == want
        hits += ok
        t = by_tier.setdefault(row["tier"], [0, 0])
        t[0] += ok
        t[1] += 1

    print(f"\n  overall top-1 accuracy: {hits}/{len(rows)} = {hits/len(rows):.1%}")
    for t in ("exact", "strong", "likely", "weak", "none"):
        if t in by_tier:
            ok, n = by_tier[t]
            print(f"    {t:8s} n={n:4d}  precision={ok/n:6.1%}")

    # accepted tiers are what a user would trust without review
    trusted = [(cat.get(row["blinkit_item_id"]) == cat[truth[row["our_code"]]])
               for row in rows if row["tier"] in ("exact", "strong")]
    prec = sum(trusted) / max(1, len(trusted))
    print(f"  exact+strong precision: {prec:.1%} over {len(trusted)} rows")

    assert hits / len(rows) >= 0.80, "top-1 accuracy regressed below 80%"
    assert prec >= 0.95, "exact+strong precision regressed below 95%"


if __name__ == "__main__":
    tmp = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp/blinkit_test")
    tmp.mkdir(parents=True, exist_ok=True)
    test_parse_size()
    test_size_score()
    test_norm_text()
    if not CATALOG.exists():
        print(f"\nSKIP accuracy test — put the Blinkit catalog at {CATALOG}")
        sys.exit(0)
    test_accuracy(tmp)
    print("\nALL TESTS PASSED")
