"""Generate a set of Sawan-themed housie (tambola) ticket images.

Each ticket carries 5 themed sections of 4 numbers each (20 numbers total),
drawn without repetition from the 1-90 range, on an illustrated Sawan card:
a floral garland frame, a jhoola (swing) centrepiece and a motif per section.

Everything is drawn at 2x and downsampled, which keeps the curves smooth.
"""
import math
import os
import random
import zipfile

from PIL import Image, ImageDraw, ImageFilter, ImageFont

W = H = 1000
SS = 2  # supersampling factor

CREAM = (253, 247, 231)
CREAM_DK = (246, 234, 208)
CARD = (255, 252, 243)
GREEN = (72, 104, 46)
GREEN_DK = (48, 72, 32)
LEAF = (110, 150, 63)
LEAF_LT = (148, 182, 95)
MAROON = (122, 30, 40)
MAROON_LT = (168, 52, 62)
GOLD = (176, 137, 60)
GOLD_LT = (233, 199, 116)
PINK = (233, 138, 162)
PINK_DK = (196, 90, 120)
PINK_LT = (247, 190, 205)
SKIN = (226, 178, 138)
SKIN_DK = (186, 138, 100)
HAIR = (48, 32, 26)
INK = (58, 40, 28)
SAFFRON = (232, 152, 58)

FONT_DIR = "/usr/share/fonts/truetype"
SERIF_B = f"{FONT_DIR}/liberation/LiberationSerif-Bold.ttf"
SERIF_BI = f"{FONT_DIR}/liberation/LiberationSerif-BoldItalic.ttf"

SECTIONS = [
    ("Jhoola Fun", "swing"),
    ("Sawan Songs", "dhol"),
    ("Mehndi Art", "mehndi"),
    ("Teej Treats", "ladoo"),
    ("Dil Se Saheliyan", "saheli"),
]
TITLE = "Aastha's Sawan"
NUMBERS_PER_SECTION = 4
NUMBER_RANGE = range(1, 91)


class Canvas:
    """ImageDraw wrapper that scales every coordinate by SS."""

    def __init__(self, img):
        self.d = ImageDraw.Draw(img)

    def _s(self, box):
        if isinstance(box[0], (tuple, list)):
            return [(x * SS, y * SS) for x, y in box]
        return [v * SS for v in box]

    def line(self, box, fill, width=1, joint=None):
        self.d.line(self._s(box), fill=fill, width=max(1, int(width * SS)),
                    joint=joint)

    def ellipse(self, box, fill=None, outline=None, width=1):
        self.d.ellipse(self._s(box), fill=fill, outline=outline,
                       width=max(1, int(width * SS)))

    def polygon(self, pts, fill=None, outline=None):
        self.d.polygon(self._s(pts), fill=fill, outline=outline)

    def rectangle(self, box, fill=None, outline=None, width=1):
        self.d.rectangle(self._s(box), fill=fill, outline=outline,
                         width=max(1, int(width * SS)))

    def rounded_rectangle(self, box, r, fill=None, outline=None, width=1):
        self.d.rounded_rectangle(self._s(box), r * SS, fill=fill,
                                 outline=outline, width=max(1, int(width * SS)))

    def arc(self, box, start, end, fill, width=1):
        self.d.arc(self._s(box), start, end, fill=fill,
                   width=max(1, int(width * SS)))

    def pieslice(self, box, start, end, fill=None, outline=None, width=1):
        self.d.pieslice(self._s(box), start, end, fill=fill, outline=outline,
                        width=max(1, int(width * SS)))

    def font(self, path, size):
        return ImageFont.truetype(path, int(size * SS))

    def text_centered(self, box, text, fnt, fill):
        x0, y0, x1, y1 = self._s(box)
        l, t, r, b = self.d.textbbox((0, 0), text, font=fnt)
        self.d.text((x0 + (x1 - x0 - (r - l)) / 2 - l,
                     y0 + (y1 - y0 - (b - t)) / 2 - t), text, font=fnt, fill=fill)


# --------------------------------------------------------------- background

def background(rng):
    """Warm paper gradient with a faint speckle and a rangoli ring."""
    img = Image.new("RGB", (W * SS, H * SS), CREAM)
    grad = Image.new("RGB", (1, H * SS))
    gd = ImageDraw.Draw(grad)
    for y in range(H * SS):
        t = y / (H * SS - 1)
        gd.point((0, y), tuple(int(a + (b - a) * t)
                               for a, b in zip(CREAM, CREAM_DK)))
    img = grad.resize((W * SS, H * SS))

    speck = Image.effect_noise((W * SS, H * SS), 26).filter(
        ImageFilter.GaussianBlur(1.2))
    img = Image.blend(img, Image.merge("RGB", (speck, speck, speck)), 0.05)
    return img


def rangoli(c, cx, cy, r):
    """Faint concentric petal ring sitting behind the centrepiece."""
    tint = (238, 226, 200)
    c.ellipse([cx - r, cy - r, cx + r, cy + r], outline=tint, width=3)
    c.ellipse([cx - r * .8, cy - r * .8, cx + r * .8, cy + r * .8],
              outline=tint, width=2)
    for i in range(24):
        a = 2 * math.pi * i / 24
        px, py = cx + math.cos(a) * r * .9, cy + math.sin(a) * r * .9
        c.ellipse([px - 11, py - 11, px + 11, py + 11], outline=tint, width=2)


# ------------------------------------------------------------------ florals

def flower(c, cx, cy, r, petal=PINK, edge=PINK_DK, petals=5, heart=GOLD_LT):
    for i in range(petals):
        a = 2 * math.pi * i / petals - math.pi / 2
        px, py = cx + math.cos(a) * r * .62, cy + math.sin(a) * r * .62
        c.ellipse([px - r * .55, py - r * .55, px + r * .55, py + r * .55],
                  fill=petal, outline=edge)
        c.ellipse([px - r * .3, py - r * .3, px + r * .12, py + r * .12],
                  fill=PINK_LT if petal is PINK else petal)
    c.ellipse([cx - r * .3, cy - r * .3, cx + r * .3, cy + r * .3],
              fill=heart, outline=GOLD)


def bud(c, cx, cy, r, angle):
    ca, sa = math.cos(angle), math.sin(angle)
    tip = (cx + ca * r * 1.5, cy + sa * r * 1.5)
    c.polygon([(cx - sa * r * .6, cy + ca * r * .6), tip,
               (cx + sa * r * .6, cy - ca * r * .6)], fill=PINK_LT,
              outline=PINK_DK)


def leaf(c, cx, cy, r, angle, fill=LEAF):
    pts = []
    for t in list(range(0, 181, 10)) + list(range(180, 361, 10)):
        u = math.radians(t)
        x = math.sin(u) * r
        y = (-math.cos(u) if t <= 180 else math.cos(u)) * r * .4
        ca, sa = math.cos(angle), math.sin(angle)
        pts.append((cx + x * ca - y * sa, cy + x * sa + y * ca))
    c.polygon(pts, fill=fill, outline=GREEN_DK)
    c.line([(cx - math.cos(angle) * r, cy - math.sin(angle) * r),
            (cx + math.cos(angle) * r, cy + math.sin(angle) * r)],
           fill=GREEN_DK, width=1)


def vine(c, rng, pts, scale=1.0):
    c.line(pts, fill=GREEN, width=5 * scale, joint="curve")
    for i in range(2, len(pts) - 2, 2):
        x, y = pts[i]
        ang = math.atan2(pts[i + 1][1] - pts[i - 1][1],
                         pts[i + 1][0] - pts[i - 1][0])
        for side in (-1, 1):
            leaf(c, x, y, 22 * scale, ang + side * 1.2,
                 LEAF if (i // 2) % 2 else LEAF_LT)
    for i in range(3, len(pts) - 3, 5):
        x, y = pts[i]
        ang = math.atan2(pts[i + 1][1] - pts[i - 1][1],
                         pts[i + 1][0] - pts[i - 1][0])
        if (i // 5) % 3 == 1:
            bud(c, x, y, 11 * scale, ang - math.pi / 2)
        else:
            flower(c, x, y, rng.uniform(19, 25) * scale)


def garland(c, rng, p0, p1, waves, amp, scale=1.0):
    (x0, y0), (x1, y1) = p0, p1
    pts, n = [], 60
    for i in range(n + 1):
        t = i / n
        x, y = x0 + (x1 - x0) * t, y0 + (y1 - y0) * t
        nx, ny = -(y1 - y0), (x1 - x0)
        ln = math.hypot(nx, ny) or 1
        off = math.sin(t * math.pi * waves) * amp
        pts.append((x + nx / ln * off, y + ny / ln * off))
    vine(c, rng, pts, scale)


def hanging_string(c, x, top, length, beads=5):
    c.line([(x, top), (x, top + length)], fill=GOLD, width=2)
    for i in range(beads):
        y = top + length * (i + 1) / beads
        c.ellipse([x - 5, y - 5, x + 5, y + 5], fill=GOLD_LT, outline=GOLD)
    flower(c, x, top + length + 12, 12)


def frame(c, rng):
    m = 30
    c.rounded_rectangle([m - 10, m - 10, W - m + 10, H - m + 10], 34,
                        fill=CARD, outline=GOLD, width=5)
    c.rounded_rectangle([m + 4, m + 4, W - m - 4, H - m - 4], 26,
                        outline=MAROON, width=3)
    c.rounded_rectangle([m + 12, m + 12, W - m - 12, H - m - 12], 20,
                        outline=GOLD_LT, width=2)
    i = m + 52
    garland(c, rng, (i, i), (W - i, i), 4, 15, .74)
    garland(c, rng, (i, H - i), (W - i, H - i), 4, 15, .74)
    garland(c, rng, (i, i), (i, H - i), 4, 15, .74)
    garland(c, rng, (W - i, i), (W - i, H - i), 4, 15, .74)
    for cx, cy in [(i, i), (W - i, i), (i, H - i), (W - i, H - i)]:
        flower(c, cx, cy, 27)
    # torans hanging from the top rail, kept clear of the title
    for x in range(150, W - 130, 96):
        if 250 < x < 750:
            continue
        hanging_string(c, x, i + 14, rng.randint(18, 34))


# -------------------------------------------------------------- centrepiece

def swing_figure(c, cx, base):
    """A woman on a jhoola: top rail, beaded chains, seat and seated figure."""
    rail_w = 300
    top = base - 200
    c.rounded_rectangle([cx - rail_w / 2 - 40, top - 16,
                         cx + rail_w / 2 + 40, top + 6], 10,
                        fill=MAROON, outline=GOLD, width=3)
    for dx in (-rail_w / 2, rail_w / 2):
        c.line([(cx + dx, top + 4), (cx + dx, base)], fill=GOLD, width=5)
        for k in range(6):
            y = top + 10 + (base - top - 20) * k / 5
            c.ellipse([cx + dx - 7, y - 7, cx + dx + 7, y + 7],
                      fill=GOLD_LT, outline=GOLD)
    # seat
    c.rounded_rectangle([cx - rail_w / 2 - 22, base, cx + rail_w / 2 + 22,
                         base + 20], 8, fill=MAROON, outline=GOLD, width=3)

    # --- seated figure, facing right
    hx, hy = cx - 6, base - 148          # head centre
    # lehenga / skirt
    c.polygon([(cx - 26, base - 78), (cx + 96, base + 4), (cx - 96, base + 4)],
              fill=GREEN, outline=GREEN_DK)
    for k in range(-3, 4):
        c.line([(cx - 20, base - 66), (cx + k * 26, base + 2)],
               fill=LEAF_LT, width=2)
    c.polygon([(cx - 96, base + 4), (cx + 96, base + 4), (cx + 92, base + 14),
               (cx - 92, base + 14)], fill=GOLD, outline=GOLD)
    # torso / choli
    c.polygon([(hx - 4, hy + 24), (cx + 34, base - 86), (cx - 40, base - 74),
               (hx - 22, hy + 26)], fill=MAROON_LT, outline=MAROON)
    # dupatta sweep
    c.polygon([(hx + 6, hy + 30), (cx + 60, base - 40), (cx + 30, base - 34),
               (hx - 6, hy + 40)], fill=PINK, outline=PINK_DK)
    # arms raised to grip the chains, elbows bent
    for dx, sx in ((-rail_w / 2, -1), (rail_w / 2, 1)):
        c.line([(hx + sx * 16, hy + 36), (cx + dx * .55, hy + 6),
                (cx + dx * .88, hy - 40), (cx + dx, top + 44)],
               fill=SKIN, width=11, joint="curve")
        c.ellipse([cx + dx - 10, top + 36, cx + dx + 10, top + 58],
                  fill=SKIN_DK, outline=SKIN_DK)
        for b in range(3):
            bx = cx + dx * (.62 + b * .05)
            by = hy + 2 - b * 12
            c.line([(bx - 8, by - 3), (bx + 8, by + 3)], fill=GOLD, width=3)
    # neck + head
    c.line([(hx, hy + 12), (hx, hy + 30)], fill=SKIN, width=13)
    c.ellipse([hx - 26, hy - 28, hx + 26, hy + 28], fill=SKIN, outline=SKIN_DK)
    # hair: bun, crown and braid down the back
    c.pieslice([hx - 29, hy - 32, hx + 24, hy + 10], 178, 360, fill=HAIR)
    c.ellipse([hx - 44, hy - 12, hx - 12, hy + 20], fill=HAIR)
    c.line([(hx - 32, hy + 12), (hx - 46, hy + 58), (hx - 36, hy + 94)],
           fill=HAIR, width=13, joint="curve")
    c.ellipse([hx - 42, hy + 88, hx - 30, hy + 104], fill=MAROON)
    flower(c, hx + 20, hy - 22, 12, PINK, PINK_DK)
    # bindi + earring
    c.ellipse([hx + 14, hy - 6, hx + 21, hy + 1], fill=MAROON)
    c.ellipse([hx - 4, hy + 14, hx + 6, hy + 26], fill=GOLD_LT, outline=GOLD)
    # petals in the air
    for dx, dy, r in ((-150, -120, 12), (150, -100, 11), (-190, -30, 10),
                      (188, -46, 12), (-118, -186, 10), (128, -178, 11)):
        flower(c, cx + dx, base + dy, r, PINK_LT, PINK_DK)


# ------------------------------------------------------------ section icons

def icon_swing(c, cx, cy, s):
    c.rounded_rectangle([cx - s, cy - s * .8, cx + s, cy - s * .62], 3,
                        fill=MAROON, outline=GOLD, width=1)
    for dx in (-s * .62, s * .62):
        c.line([(cx + dx, cy - s * .62), (cx + dx, cy + s * .3)],
               fill=GOLD, width=2)
    c.rounded_rectangle([cx - s * .8, cy + s * .3, cx + s * .8, cy + s * .5], 3,
                        fill=MAROON, outline=GOLD, width=1)
    for dx in (-s, s):
        c.line([(cx + dx, cy - s * .7), (cx + dx, cy + s * .8)],
               fill=GREEN, width=3)
    flower(c, cx, cy - s * .95, s * .3)
    leaf(c, cx - s * .78, cy + s * .1, s * .35, -0.5)
    leaf(c, cx + s * .78, cy + s * .1, s * .35, 3.6)


def icon_dhol(c, cx, cy, s):
    c.rounded_rectangle([cx - s * .85, cy - s * .5, cx + s * .55, cy + s * .5],
                        s * .22, fill=(176, 106, 54), outline=(120, 66, 30),
                        width=2)
    for x in (cx - s * .85, cx + s * .55):
        c.ellipse([x - s * .18, cy - s * .58, x + s * .18, cy + s * .58],
                  fill=CREAM, outline=(120, 66, 30), width=2)
    for k in range(4):
        x = cx - s * .7 + k * s * .38
        c.line([(x, cy - s * .5), (x + s * .16, cy + s * .5)],
               fill=GOLD, width=2)
    for dx, dy, r in ((s * .95, -s * .75, s * .17), (s * 1.3, -s * .3, s * .13)):
        c.ellipse([cx + dx - r, cy + dy - r, cx + dx + r, cy + dy + r],
                  fill=MAROON)
        c.line([(cx + dx + r * .9, cy + dy), (cx + dx + r * .9, cy + dy - r * 3)],
               fill=MAROON, width=2)


def icon_mehndi(c, cx, cy, s):
    c.rounded_rectangle([cx - s * .5, cy - s * .2, cx + s * .5, cy + s * .8],
                        s * .22, fill=SKIN, outline=SKIN_DK, width=2)
    for k, h in enumerate((.75, .95, .88, .68)):
        x = cx - s * .38 + k * s * .25
        c.rounded_rectangle([x - s * .1, cy - s * h, x + s * .1, cy + s * .1],
                            s * .1, fill=SKIN, outline=SKIN_DK, width=2)
    c.rounded_rectangle([cx + s * .38, cy + s * .05, cx + s * .68, cy + s * .5],
                        s * .12, fill=SKIN, outline=SKIN_DK, width=2)
    c.ellipse([cx - s * .22, cy + s * .06, cx + s * .22, cy + s * .5],
              outline=MAROON, width=2)
    c.ellipse([cx - s * .1, cy + s * .18, cx + s * .1, cy + s * .38],
              fill=MAROON)
    for k in range(6):
        a = 2 * math.pi * k / 6
        px = cx + math.cos(a) * s * .3
        py = cy + s * .28 + math.sin(a) * s * .3
        c.ellipse([px - s * .05, py - s * .05, px + s * .05, py + s * .05],
                  fill=MAROON)


def icon_ladoo(c, cx, cy, s):
    for dx, dy in ((-s * .4, 0), (s * .4, 0), (0, -s * .42)):
        c.ellipse([cx + dx - s * .34, cy + dy - s * .34,
                   cx + dx + s * .34, cy + dy + s * .34],
                  fill=(238, 176, 62), outline=(186, 118, 30), width=2)
        c.ellipse([cx + dx - s * .18, cy + dy - s * .22,
                   cx + dx + s * .02, cy + dy - s * .02],
                  fill=(248, 208, 118))
    c.pieslice([cx - s, cy + s * .2, cx + s, cy + s * .95], 0, 180,
               fill=(214, 214, 220), outline=(150, 150, 158), width=2)
    c.line([(cx - s, cy + s * .3), (cx + s, cy + s * .3)],
           fill=(150, 150, 158), width=2)


def icon_saheli(c, cx, cy, s):
    for dx, col in ((-s * .62, PINK), (0, MAROON_LT), (s * .62, GREEN)):
        x = cx + dx
        c.polygon([(x, cy - s * .1), (x + s * .34, cy + s * .8),
                   (x - s * .34, cy + s * .8)], fill=col, outline=GREEN_DK)
        c.ellipse([x - s * .16, cy - s * .52, x + s * .16, cy - s * .2],
                  fill=SKIN, outline=SKIN_DK)
        c.pieslice([x - s * .18, cy - s * .56, x + s * .18, cy - s * .22],
                   180, 360, fill=HAIR)
        c.line([(x - s * .12, cy - s * .3), (x - s * .18, cy + s * .1)],
               fill=HAIR, width=3)


ICONS = {"swing": icon_swing, "dhol": icon_dhol, "mehndi": icon_mehndi,
         "ladoo": icon_ladoo, "saheli": icon_saheli}


# ----------------------------------------------------------------- sections

def draw_section(c, box, name, kind, numbers, f_name, f_num):
    x0, y0, x1, y1 = box
    c.rounded_rectangle([x0 + 3, y0 + 4, x1 + 3, y1 + 5], 18,
                        fill=(238, 226, 202))
    c.rounded_rectangle([x0, y0, x1, y1], 18, fill=CREAM, outline=MAROON,
                        width=3)
    c.rounded_rectangle([x0 + 6, y0 + 6, x1 - 6, y1 - 6], 13, outline=GOLD,
                        width=2)

    icon_w = 66
    ICONS[kind](c, x0 + 14 + icon_w / 2, (y0 + y1) / 2, 22)
    c.text_centered((x0 + icon_w, y0 + 12, x1 - 10, y0 + 54), name, f_name,
                    MAROON)

    n = len(numbers)
    left = x0 + icon_w + 4
    span = (x1 - left) - 22
    step = span / n
    cy = y1 - 40
    for i, num in enumerate(numbers):
        cx = left + 11 + step * (i + .5)
        r = 24
        c.ellipse([cx - r + 2, cy - r + 2, cx + r + 2, cy + r + 2],
                  fill=(236, 224, 200))
        c.ellipse([cx - r, cy - r, cx + r, cy + r], fill=CARD, outline=GREEN,
                  width=3)
        c.text_centered((cx - r, cy - r, cx + r, cy + r), str(num), f_num, INK)


def make_ticket(path, rng):
    img = background(rng)
    c = Canvas(img)

    rangoli(c, W // 2, 322, 168)
    frame(c, rng)

    f_title = c.font(SERIF_BI, 72)
    f_name = c.font(SERIF_B, 30)
    f_num = c.font(SERIF_B, 27)

    c.text_centered((0, 96, W, 182), TITLE, f_title, GREEN)
    c.line([(322, 190), (678, 190)], fill=GOLD, width=3)
    for x in (302, 698):
        flower(c, x, 190, 13)

    swing_figure(c, W // 2, 430)

    picks = rng.sample(list(NUMBER_RANGE), NUMBERS_PER_SECTION * len(SECTIONS))
    groups = [sorted(picks[i * NUMBERS_PER_SECTION:(i + 1) * NUMBERS_PER_SECTION])
              for i in range(len(SECTIONS))]

    bw, bh, gap = 372, 118, 20
    left = (W - (bw * 2 + gap)) // 2
    top = 508
    for i in range(4):
        x0 = left + (i % 2) * (bw + gap)
        y0 = top + (i // 2) * (bh + gap)
        draw_section(c, (x0, y0, x0 + bw, y0 + bh), *SECTIONS[i], groups[i],
                     f_name, f_num)
    draw_section(c, ((W - bw) // 2, top + 2 * (bh + gap),
                     (W - bw) // 2 + bw, top + 2 * (bh + gap) + bh),
                 *SECTIONS[4], groups[4], f_name, f_num)

    img.resize((W, H), Image.LANCZOS).save(path, "PNG")


def main(count=30, out_dir="output/housie_tickets",
         zip_path="output/housie_tickets.zip"):
    os.makedirs(out_dir, exist_ok=True)
    paths = []
    for i in range(1, count + 1):
        rng = random.Random(1000 + i)
        p = os.path.join(out_dir, f"housie_ticket_{i:02d}.png")
        make_ticket(p, rng)
        paths.append(p)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for p in paths:
            z.write(p, os.path.basename(p))
    print(f"{len(paths)} tickets -> {zip_path}")


if __name__ == "__main__":
    main()
