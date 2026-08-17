"""Infer the brand of a catalogue item from its NAME.

Neither the Blinkit export nor a typical scraped DMart list carries a brand
column, but a gap analysis is meaningless without one. Brands sit at the front
of retail product names ("Amul Butter 500 g"), so we learn them from the shape
of the catalogue itself rather than a hardcoded list:

  * count how many items start with each 1..4-token prefix, and how many
    DISTINCT words follow it (its branching factor)
  * a real brand root has many SKUs AND many different continuations —
    "tata" is followed by 10 different words, so it is a brand
  * "dot" is followed by only one word ever, so it is not a brand on its own;
    extend to "dot & key"
  * generic openers ("the", "organic", "whole", "fresh") are never brands
    alone, so they always extend — "whole farm", "the bakers dozen"
  * an explicit trailing " - Brandname" (1,785 items in the Blinkit export)
    wins outright, since it is the seller stating the brand

The result is a brand ROOT, the granularity a category buyer thinks in: Tata,
not Tata Sampann. sub_brand keeps the longer form when one exists.
"""

import re
from collections import Counter, defaultdict

MAX_BRAND_TOKENS = 4
MIN_SKUS = 3          # a brand root needs this many items behind it
MIN_BRANCH = 3        # ...and this many different products under it

# Words that open a product name without naming its brand. A prefix starting
# with one of these must extend before it can be called a brand.
GENERIC_OPENERS = {
    "the", "organic", "organically", "whole", "fresh", "premium", "natural",
    "pure", "raw", "real", "best", "super", "extra", "daily", "double",
    "indian", "desi", "homemade", "classic", "original", "special", "mini",
    "big", "small", "large", "new", "gourmet", "farm", "grown", "healthy",
    "assorted", "mixed", "imported", "local", "loose", "combo", "pack",
    "set", "kit", "refill", "value", "family", "jumbo", "regular",
    # honorifics/initials: "dr" alone merges Dr. Oetker with Dr. Morepen
    "dr", "mr", "mrs", "ms", "st", "prof", "shri", "sri",
}

_TRAILING_BRAND = re.compile(r"\s+-\s+([A-Za-z][\w&'.\- ]{1,28})$")


def norm(s: str) -> str:
    """Normalise for brand comparison: '&' -> 'and', drop apostrophes/punctuation."""
    s = (s or "").lower()
    s = re.sub(r"[‘’']", "", s)
    s = s.replace("&", " and ")
    s = re.sub(r"[^a-z0-9.%]+", " ", s)
    s = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _tokens(name: str) -> list[str]:
    return norm(name).split()


class BrandExtractor:
    """Learns prefix statistics over one catalogue, then labels its items."""

    def __init__(self, names, min_skus=MIN_SKUS, min_branch=MIN_BRANCH):
        self.min_skus, self.min_branch = min_skus, min_branch
        self.count: Counter = Counter()
        self.cont: dict[str, set] = defaultdict(set)
        self.trailing: Counter = Counter()
        for name in names:
            t = _tokens(name)
            for k in range(1, min(MAX_BRAND_TOKENS, len(t)) + 1):
                p = " ".join(t[:k])
                self.count[p] += 1
                if k < len(t):
                    self.cont[p].add(t[k])
            m = _TRAILING_BRAND.search(name or "")
            if m:
                self.trailing[norm(m.group(1))] += 1

    def _is_brand_root(self, prefix: str) -> bool:
        head = prefix.split()[0]
        if head in GENERIC_OPENERS and " " not in prefix:
            return False          # "the", "organic" alone is never a brand
        return (self.count[prefix] >= self.min_skus
                and len(self.cont.get(prefix, ())) >= self.min_branch)

    def extract(self, name: str) -> tuple[str, str]:
        """Return (brand_root, sub_brand). sub_brand is '' when there is no
        longer brand-ish form. Falls back to the first token when nothing in the
        catalogue supports a confident answer — a one-off import still has a
        brand, we just can't corroborate it."""
        m = _TRAILING_BRAND.search(name or "")
        if m and self.trailing[norm(m.group(1))] >= self.min_skus:
            # "Reading Light (White) - Flyngo" — the seller named the brand, and
            # it recurs as a trailing brand elsewhere. Without that recurrence
            # check, "Farmley Makha Shaka - Achaari Stix" would name a FLAVOUR
            # as the brand instead of Farmley.
            return norm(m.group(1)), ""

        t = _tokens(name)
        if not t:
            return "", ""

        root = ""
        for k in range(1, min(MAX_BRAND_TOKENS, len(t)) + 1):
            p = " ".join(t[:k])
            if self._is_brand_root(p):
                root = p
                break
        if not root:
            # no prefix qualified: take the longest prefix that at least repeats,
            # else the leading token(s) past any generic opener
            for k in range(min(MAX_BRAND_TOKENS, len(t)), 0, -1):
                p = " ".join(t[:k])
                if self.count[p] >= 2:
                    root = p
                    break
            if not root:
                root = t[0] if t[0] not in GENERIC_OPENERS else " ".join(t[:2])

        # sub-brand: the SHORTEST longer prefix that is itself well-populated —
        # "tata sampann", not "tata sampann unpolished" (which is a product line)
        sub = ""
        nroot = len(root.split())
        for k in range(nroot + 1, min(MAX_BRAND_TOKENS, len(t)) + 1):
            p = " ".join(t[:k])
            if self.count[p] >= self.min_skus and len(self.cont.get(p, ())) >= self.min_branch:
                sub = p
                break
        return root, sub


def brand_vocab(items, brand_key="brand", name_key="name") -> set[str]:
    """Our side's brand vocabulary: the BRAND/Mfr column where populated, plus
    brands inferred from our own item names (the column is often blank or holds
    a manufacturer rather than the consumer brand)."""
    vocab = set()
    for it in items:
        b = norm(it.get(brand_key, ""))
        if b:
            vocab.add(b)
    ex = BrandExtractor([it.get(name_key, "") for it in items])
    for it in items:
        root, sub = ex.extract(it.get(name_key, ""))
        if root:
            vocab.add(root)
        if sub:
            vocab.add(sub)
    vocab.discard("")
    return vocab
