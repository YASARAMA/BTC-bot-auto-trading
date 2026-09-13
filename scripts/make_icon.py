"""Generate the app icon and splash art. Run: python scripts/make_icon.py

Draws everything with Pillow so the repository carries no binary source art:
  assets/icon.ico   multi-size Windows icon for the executable
  assets/icon.png   512px icon for the web UI and docs
  assets/splash.png PyInstaller splash screen shown while the exe unpacks
"""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
ORANGE = (247, 147, 26)
ORANGE_LIGHT = (255, 186, 92)
INK = (14, 18, 24)
PANEL = (19, 26, 35)


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for name in ("DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf", "Arial_Bold.ttf"):
        for base in ("/usr/share/fonts/truetype/dejavu/", "/usr/share/fonts/truetype/liberation/", ""):
            try:
                return ImageFont.truetype(base + name, size)
            except OSError:
                continue
    return ImageFont.load_default()


def rounded_gradient(size: int, radius_ratio: float = 0.22) -> Image.Image:
    """A rounded square with a diagonal orange gradient."""
    grad = Image.new("RGB", (size, size), ORANGE)
    px = grad.load()
    for y in range(size):
        for x in range(size):
            t = (x + y) / (2 * size - 2)
            px[x, y] = tuple(round(a + (b - a) * t) for a, b in zip(ORANGE_LIGHT, ORANGE))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size - 1, size - 1], radius=int(size * radius_ratio), fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(grad, (0, 0), mask)
    return out


def draw_symbol(img: Image.Image) -> Image.Image:
    """The Bitcoin mark (a bold B with two vertical strokes) over a rising candle.

    The B is a font glyph, the strokes are drawn: no font on the build machine is
    guaranteed to carry U+20BF, and a missing glyph would render as a box.
    """
    size = img.width
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)

    # rising candle behind the glyph
    cw = max(2, round(size * 0.075))
    cx = round(size * 0.745)
    d.line([(cx, size * 0.20), (cx, size * 0.82)], fill=INK + (60,), width=max(1, cw // 2))
    d.rounded_rectangle([cx - cw, size * 0.34, cx + cw, size * 0.70], radius=cw // 2, fill=INK + (60,))

    font = _font(round(size * 0.60))
    box = d.textbbox((0, 0), "B", font=font)
    bw, bh = box[2] - box[0], box[3] - box[1]
    bx = (size - bw) / 2 - box[0] - size * 0.03
    by = (size - bh) / 2 - box[1]
    d.text((bx, by), "B", font=font, fill=INK)

    # the two vertical strokes of the Bitcoin mark
    stroke = max(2, round(size * 0.055))
    overhang = size * 0.085
    for frac in (0.34, 0.62):
        x = bx + box[0] + bw * frac
        d.rounded_rectangle([x - stroke / 2, by + box[1] - overhang, x + stroke / 2, by + box[1] + bh + overhang],
                            radius=stroke / 2, fill=INK)
    return Image.alpha_composite(img, layer)


def make_icon() -> None:
    base = draw_symbol(rounded_gradient(512))
    ASSETS.mkdir(exist_ok=True)
    base.save(ASSETS / "icon.png")
    sizes = [16, 24, 32, 48, 64, 128, 256]
    # Small sizes: drop the rounded corners a little so the glyph stays readable.
    frames = [draw_symbol(rounded_gradient(s, 0.18 if s <= 32 else 0.22)) for s in sizes]
    frames[-1].save(ASSETS / "icon.ico", format="ICO", sizes=[(s, s) for s in sizes],
                    append_images=frames[:-1])
    print(f"wrote {ASSETS / 'icon.ico'} ({', '.join(str(s) for s in sizes)}) and icon.png")


def make_splash(width: int = 520, height: int = 300) -> None:
    img = Image.new("RGB", (width, height), PANEL)
    d = ImageDraw.Draw(img)
    # soft radial glow behind the logo
    glow = Image.new("RGB", (width, height), PANEL)
    gd = ImageDraw.Draw(glow)
    gd.ellipse([width * 0.18, -height * 0.5, width * 0.82, height * 0.95], fill=(44, 33, 18))
    img = Image.blend(img, glow.filter(ImageFilter.GaussianBlur(38)), 0.85)
    d = ImageDraw.Draw(img)
    logo = draw_symbol(rounded_gradient(96))
    img.paste(logo, (round(width / 2 - 48), 44), logo)
    title = _font(30)
    label = "BTC Bot"
    box = d.textbbox((0, 0), label, font=title)
    d.text(((width - (box[2] - box[0])) / 2 - box[0], 156), label, font=title, fill=(232, 238, 246))
    sub = _font(15)
    note = "starting the trading engine…"
    box = d.textbbox((0, 0), note, font=sub)
    d.text(((width - (box[2] - box[0])) / 2 - box[0], 196), note, font=sub, fill=(159, 176, 196))
    # progress rail with a highlighted segment (static: the splash is shown while the exe unpacks)
    rail = [width * 0.22, height - 58, width * 0.78, height - 52]
    d.rounded_rectangle(rail, radius=3, fill=(36, 48, 64))
    d.rounded_rectangle([rail[0], rail[1], rail[0] + (rail[2] - rail[0]) * 0.42, rail[3]], radius=3, fill=ORANGE)
    warn = _font(12)
    msg = "Spot only · paper mode by default · this software can lose money"
    box = d.textbbox((0, 0), msg, font=warn)
    d.text(((width - (box[2] - box[0])) / 2 - box[0], height - 34), msg, font=warn, fill=(107, 123, 143))
    img.save(ASSETS / "splash.png")
    print(f"wrote {ASSETS / 'splash.png'} ({width}x{height})")


if __name__ == "__main__":
    make_icon()
    make_splash()
