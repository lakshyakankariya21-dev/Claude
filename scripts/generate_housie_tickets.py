"""Generate a set of Sawan-themed housie (tambola) ticket images.

Each ticket has 5 themed sections with 4 numbers each (20 numbers total),
drawn without repetition from the 1-90 range.
"""
import math
import os
import random
import zipfile

from PIL import Image, ImageDraw, ImageFont

W = H = 1000
CREAM = (253, 248, 234)
CARD = (255, 252, 243)
GREEN = (74, 106, 47)
LEAF = (108, 148, 62)
MAROON = (124, 32, 42)
GOLD = (176, 137, 60)
PINK = (232, 138, 162)
PINK_DK = (198, 92, 122)
INK = (60, 42, 30)

FONT_DIR = "/usr/share/fonts/truetype"
SERIF = f"{FONT_DIR}/liberation/LiberationSerif-Regular.ttf"
SERIF_B = f"{FONT_DIR}/liberation/LiberationSerif-Bold.ttf"
SERIF_BI = f"{FONT_DIR}/liberation/LiberationSerif-BoldItalic.ttf"

SECTIONS = [
    "Jhoola Fun", "Sawan Songs", "Mehndi Art", "Teej Treats", "Dil Se Saheliyan",
]
TITLE = "Aastha's Sawan"
NUMBERS_PER_SECTION = 4
NUMBER_RANGE = range(1, 91)


def font(path, size):
    return ImageFont.truetype(path, size)


def centered(draw, box, text, fnt, fill):
    x0, y0, x1, y1 = box
    l, t, r, b = draw.textbbox((0, 0), text, font=fnt)
    draw.text((x0 + (x1 - x0 - (r - l)) / 2 - l,
               y0 + (y1 - y0 - (b - t)) / 2 - t), text, font=fnt, fill=fill)


def flower(draw, cx, cy, r, petal=PINK, edge=PINK_DK, petals=5):
    for i in range(petals):
        a = 2 * math.pi * i / petals - math.pi / 2
        px, py = cx + math.cos(a) * r * 0.62, cy + math.sin(a) * r * 0.62
        draw.ellipse([px - r * 0.55, py - r * 0.55, px + r * 0.55, py + r * 0.55],
                     fill=petal, outline=edge)
    draw.ellipse([cx - r * 0.28, cy - r * 0.28, cx + r * 0.28, cy + r * 0.28],
                 fill=(240, 196, 88), outline=GOLD)


def leaf(draw, cx, cy, r, angle):
    pts = []
    for t in range(0, 181, 12):
        u = math.radians(t)
        x, y = math.sin(u) * r, -math.cos(u) * r * 0.42
        ca, sa = math.cos(angle), math.sin(angle)
        pts.append((cx + x * ca - y * sa, cy + x * sa + y * ca))
    for t in range(180, 361, 12):
        u = math.radians(t)
        x, y = math.sin(u) * r, math.cos(u) * r * 0.42 - r * 0.0
        ca, sa = math.cos(angle), math.sin(angle)
        pts.append((cx + x * ca - y * sa, cy + x * sa + y * ca))
    draw.polygon(pts, fill=LEAF, outline=GREEN)


def vine(draw, rng, pts, scale=1.0):
    draw.line(pts, fill=GREEN, width=int(6 * scale), joint="curve")
    for i in range(2, len(pts) - 2, 2):
        x, y = pts[i]
        ang = math.atan2(pts[i + 1][1] - pts[i - 1][1], pts[i + 1][0] - pts[i - 1][0])
        for side in (-1, 1):
            leaf(draw, x, y, 26 * scale, ang + side * 1.15)
    for i in range(3, len(pts) - 3, 5):
        flower(draw, pts[i][0], pts[i][1], rng.uniform(20, 28) * scale)


def garland(draw, rng, p0, p1, waves, amp, scale=1.0):
    (x0, y0), (x1, y1) = p0, p1
    pts = []
    n = 60
    for i in range(n + 1):
        t = i / n
        x = x0 + (x1 - x0) * t
        y = y0 + (y1 - y0) * t
        nx, ny = -(y1 - y0), (x1 - x0)
        ln = math.hypot(nx, ny) or 1
        off = math.sin(t * math.pi * waves) * amp
        pts.append((x + nx / ln * off, y + ny / ln * off))
    vine(draw, rng, pts, scale)


def draw_border(draw, rng):
    m = 34
    draw.rounded_rectangle([m - 12, m - 12, W - m + 12, H - m + 12], 34,
                           fill=CARD, outline=GOLD, width=5)
    draw.rounded_rectangle([m + 4, m + 4, W - m - 4, H - m - 4], 26,
                           outline=MAROON, width=3)
    i = m + 52
    garland(draw, rng, (i, i), (W - i, i), 4, 14, 0.72)
    garland(draw, rng, (i, H - i), (W - i, H - i), 4, 14, 0.72)
    garland(draw, rng, (i, i), (i, H - i), 4, 14, 0.72)
    garland(draw, rng, (W - i, i), (W - i, H - i), 4, 14, 0.72)
    for cx, cy in [(i, i), (W - i, i), (i, H - i), (W - i, H - i)]:
        flower(draw, cx, cy, 26)


def draw_swing(draw, cx, top, bottom):
    """Simple decorative jhoola (swing) motif under the title."""
    bar_w = 210
    for dx in (-bar_w // 2, bar_w // 2):
        draw.line([(cx + dx, top), (cx + dx, bottom)], fill=GOLD, width=6)
        for y in range(top, bottom, 26):
            draw.ellipse([cx + dx - 7, y - 7, cx + dx + 7, y + 7],
                         fill=(240, 205, 120), outline=GOLD)
    draw.rounded_rectangle([cx - bar_w // 2 - 26, bottom, cx + bar_w // 2 + 26,
                            bottom + 20], 9, fill=MAROON, outline=GOLD, width=3)
    for i in range(-2, 3):
        flower(draw, cx + i * 58, bottom + 34, 17)


def draw_section(draw, box, name, numbers, f_name, f_num):
    x0, y0, x1, y1 = box
    draw.rounded_rectangle([x0, y0, x1, y1], 18, fill=CREAM,
                           outline=MAROON, width=3)
    draw.rounded_rectangle([x0 + 6, y0 + 6, x1 - 6, y1 - 6], 13,
                           outline=GOLD, width=2)
    centered(draw, (x0, y0 + 14, x1, y0 + 62), name, f_name, MAROON)
    n = len(numbers)
    span = (x1 - x0) - 60
    step = span / n
    cy = y1 - 46
    for i, num in enumerate(numbers):
        cx = x0 + 30 + step * (i + 0.5)
        r = 27
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=CARD,
                     outline=GREEN, width=3)
        centered(draw, (cx - r, cy - r, cx + r, cy + r), str(num), f_num, INK)


def make_ticket(path, rng):
    img = Image.new("RGB", (W, H), CREAM)
    d = ImageDraw.Draw(img)
    draw_border(d, rng)

    f_title = font(SERIF_BI, 76)
    f_name = font(SERIF_B, 34)
    f_num = font(SERIF_B, 30)

    centered(d, (0, 96, W, 186), TITLE, f_title, GREEN)
    d.line([(300, 196), (700, 196)], fill=GOLD, width=3)
    draw_swing(d, W // 2, 214, 322)

    picks = rng.sample(list(NUMBER_RANGE), NUMBERS_PER_SECTION * len(SECTIONS))
    groups = [sorted(picks[i * NUMBERS_PER_SECTION:(i + 1) * NUMBERS_PER_SECTION])
              for i in range(len(SECTIONS))]

    bw, bh, gap = 372, 128, 26
    left = (W - (bw * 2 + gap)) // 2
    top = 420
    for i in range(4):
        col, row = i % 2, i // 2
        x0 = left + col * (bw + gap)
        y0 = top + row * (bh + gap)
        draw_section(d, (x0, y0, x0 + bw, y0 + bh), SECTIONS[i], groups[i],
                     f_name, f_num)
    y0 = top + 2 * (bh + gap)
    x0 = (W - bw) // 2
    draw_section(d, (x0, y0, x0 + bw, y0 + bh), SECTIONS[4], groups[4],
                 f_name, f_num)

    img.save(path, "PNG")


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
