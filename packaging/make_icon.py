"""Generate the app icon (MIDNIGHT NEON identity) -> packaging/mc3.ico

A neon rounded-square badge with the magenta->violet->cyan diagonal gradient of the
UI, a vinyl record (the app swaps a game's soundtrack) and a glow. Drawn at 1024px
and downsampled into every size Windows actually uses.

    python packaging/make_icon.py
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

OUT = Path(__file__).resolve().parent / "mc3.ico"
PNG_PREVIEW = Path(__file__).resolve().parent / "mc3_icon_preview.png"
S = 1024  # master size

MAGENTA = (255, 45, 149)
VIOLET = (139, 92, 255)
CYAN = (25, 227, 227)
NIGHT = (7, 5, 16)


def lerp(a, b, t):
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def diagonal_gradient(size, stops):
    """Diagonal (top-left -> bottom-right) gradient through a list of colors."""
    img = Image.new("RGB", (size, size))
    px = img.load()
    n = len(stops) - 1
    for y in range(size):
        for x in range(size):
            t = (x + y) / (2 * (size - 1))          # 0..1 along the diagonal
            seg = min(int(t * n), n - 1)
            local = t * n - seg
            px[x, y] = lerp(stops[seg], stops[seg + 1], local)
    return img


def rounded_mask(size, radius):
    m = Image.new("L", (size, size), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    return m


def build():
    # 1) neon gradient badge
    grad = diagonal_gradient(S, [MAGENTA, VIOLET, CYAN])
    badge = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    badge.paste(grad, (0, 0), rounded_mask(S, radius=int(S * 0.22)))

    # 2) inner night disc + vinyl grooves (the "record" we're re-cutting)
    d = ImageDraw.Draw(badge, "RGBA")
    cx = cy = S // 2
    r_out = int(S * 0.33)
    d.ellipse([cx - r_out, cy - r_out, cx + r_out, cy + r_out], fill=NIGHT + (255,))
    for i, rr in enumerate((0.285, 0.245, 0.205)):
        r = int(S * rr)
        col = (CYAN if i % 2 == 0 else MAGENTA) + (110,)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=col, width=int(S * 0.012))

    # 3) center label + spindle hole
    r_lab = int(S * 0.115)
    d.ellipse([cx - r_lab, cy - r_lab, cx + r_lab, cy + r_lab], fill=MAGENTA + (255,))
    r_hole = int(S * 0.032)
    d.ellipse([cx - r_hole, cy - r_hole, cx + r_hole, cy + r_hole], fill=NIGHT + (255,))

    # 4) neon glow: blur a copy of the rings and screen it back on
    glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse([cx - r_out, cy - r_out, cx + r_out, cy + r_out],
               outline=CYAN + (200,), width=int(S * 0.02))
    glow = glow.filter(ImageFilter.GaussianBlur(int(S * 0.03)))
    badge = Image.alpha_composite(badge, glow)

    # 5) speed streaks (Midnight Club!) — three neon dashes cutting the corner
    sd = ImageDraw.Draw(badge, "RGBA")
    for i, (yy, ln, col) in enumerate((
        (0.30, 0.20, CYAN), (0.42, 0.28, (255, 255, 255)), (0.54, 0.16, MAGENTA),
    )):
        y = int(S * yy)
        x0 = int(S * 0.085)
        sd.rounded_rectangle([x0, y, x0 + int(S * ln), y + int(S * 0.028)],
                             radius=int(S * 0.014), fill=col + (150,))

    badge.save(PNG_PREVIEW)
    sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (24, 24), (16, 16)]
    badge.save(OUT, format="ICO", sizes=sizes)
    print(f"icone: {OUT}  ({OUT.stat().st_size/1024:.1f} KB)")
    print(f"preview: {PNG_PREVIEW}")
    print("tamanhos:", ", ".join(f"{w}x{h}" for w, h in sizes))


if __name__ == "__main__":
    build()
