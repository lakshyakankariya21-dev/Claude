"""Build housie tickets from the original Sawan ticket artwork.

The source photograph is used as-is; only the number strip inside each of the
five section boxes is repainted, dropping from 5 numbers to 4 and drawing from
the full 1-90 range instead of 1-50.
"""
import os
import random
import zipfile

from PIL import Image, ImageDraw, ImageFilter, ImageFont

SRC = "assets/sawan_ticket_source.jpg"
SCALE = 3               # output upscale factor
FONT = "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf"

NUMBERS_PER_SECTION = 4
NUMBER_RANGE = range(1, 91)

# Circle geometry measured on the 504x506 source, in source pixels.
RADIUS = 11
SPACING = 25
# (centre-y, x of the first of the original five circles)
STRIPS = [
    (331, 131),   # Jhoola Fun
    (331, 329),   # Sawan Songs
    (398, 131),   # Mehndi Art
    (398, 329),   # Teej Treats
    (464, 229),   # Dil Se Saheliyan
]
CIRCLE_STROKE = (176, 152, 108)
DIGIT_INK = (38, 32, 28)


def strip_box(cy, x0, pad_x=16, pad_y=16):
    """Region covering the original five circles, in source pixels."""
    return (x0 - pad_x, cy - pad_y, x0 + SPACING * 4 + pad_x, cy + pad_y)


def erase_strip(img, box, pad=16):
    """Wipe the old numbers with a median-filtered copy of the same paper.

    A wide median removes the thin digit and circle strokes while keeping the
    box's own paper tone and shading, so no synthetic patch is introduced. The
    result is pasted back through a feathered mask to avoid a visible seam.
    """
    x0, y0, x1, y1 = box
    region = (x0 - pad, y0 - pad, x1 + pad, y1 + pad)
    crop = img.crop(region)
    clean = crop.filter(ImageFilter.MedianFilter(19))
    clean = clean.filter(ImageFilter.GaussianBlur(0.8))

    mask = Image.new("L", crop.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [pad - 3, pad - 3, crop.width - pad + 3, crop.height - pad + 3],
        6, fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(3))
    img.paste(clean, (region[0], region[1]), mask)


def draw_numbers(img, cy, x0, numbers):
    """Draw the new circles and digits at output resolution."""
    d = ImageDraw.Draw(img)
    fnt = ImageFont.truetype(FONT, int(15.5 * SCALE))
    r = RADIUS * SCALE
    span_centre = (x0 + SPACING * 2) * SCALE
    step = SPACING * SCALE
    start = span_centre - step * (len(numbers) - 1) / 2
    for i, num in enumerate(numbers):
        cx = start + step * i
        cyy = cy * SCALE
        d.ellipse([cx - r, cyy - r, cx + r, cyy + r], outline=CIRCLE_STROKE,
                  width=max(1, SCALE // 2))
        text = str(num)
        l, t, rr, b = d.textbbox((0, 0), text, font=fnt)
        d.text((cx - (rr - l) / 2 - l, cyy - (b - t) / 2 - t), text,
               font=fnt, fill=DIGIT_INK)


def make_ticket(src, path, rng):
    img = src.copy()
    for cy, x0 in STRIPS:
        erase_strip(img, strip_box(cy, x0))

    big = img.resize((img.width * SCALE, img.height * SCALE), Image.LANCZOS)
    picks = rng.sample(list(NUMBER_RANGE), NUMBERS_PER_SECTION * len(STRIPS))
    for i, (cy, x0) in enumerate(STRIPS):
        group = sorted(picks[i * NUMBERS_PER_SECTION:
                             (i + 1) * NUMBERS_PER_SECTION])
        draw_numbers(big, cy, x0, group)
    big.save(path, "JPEG", quality=93, subsampling=0)


def main(count=30, out_dir="output/housie_tickets",
         zip_path="output/housie_tickets.zip"):
    src = Image.open(SRC).convert("RGB")
    os.makedirs(out_dir, exist_ok=True)
    for f in os.listdir(out_dir):
        os.remove(os.path.join(out_dir, f))
    paths = []
    for i in range(1, count + 1):
        p = os.path.join(out_dir, f"housie_ticket_{i:02d}.jpg")
        make_ticket(src, p, random.Random(2000 + i))
        paths.append(p)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for p in paths:
            z.write(p, os.path.basename(p))
    print(f"{len(paths)} tickets -> {zip_path}")


if __name__ == "__main__":
    main()
