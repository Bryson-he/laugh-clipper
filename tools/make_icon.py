"""Draws the app icon (assets/icon.png and assets/icon.ico). Needs Pillow."""

from pathlib import Path

from PIL import Image, ImageDraw

N = 1024
OUT = Path(__file__).resolve().parent.parent / "assets"


def lerp(a, b, t):
    return tuple(int(x + (y - x) * t) for x, y in zip(a, b))


def make():
    img = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    pad = 64
    grad = Image.new("RGBA", (N, N))
    gd = ImageDraw.Draw(grad)
    for y in range(N):
        gd.line([(0, y), (N, y)], fill=lerp((255, 220, 40), (255, 140, 0), y / (N - 1)) + (255,))
    mask = Image.new("L", (N, N), 0)
    ImageDraw.Draw(mask).rounded_rectangle((pad, pad, N - pad, N - pad), radius=int((N - 2 * pad) * 0.225),
                                           fill=255)
    img.paste(grad, (0, 0), mask)

    d = ImageDraw.Draw(img)
    u = (N - 2 * pad) / 40
    ink = (92, 46, 0, 255)

    def P(x, y):
        return pad + x * u, pad + y * u

    w = int(2.9 * u)
    for ex in (13.5, 26.5):
        pts = [P(ex - 3.6, 17.5), P(ex, 13), P(ex + 3.6, 17.5)]
        d.line(pts, fill=ink, width=w, joint="curve")
        for x, y in (pts[0], pts[2]):
            d.ellipse((x - w / 2, y - w / 2, x + w / 2, y + w / 2), fill=ink)
    mouth_box = (*P(10.5, 16), *P(29.5, 33))
    d.chord(mouth_box, 0, 180, fill=ink)
    tongue = Image.new("L", (N, N), 0)
    ImageDraw.Draw(tongue).chord(mouth_box, 0, 180, fill=255)
    t_layer = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    ImageDraw.Draw(t_layer).ellipse((*P(14, 27), *P(26, 36)), fill=(255, 107, 107, 255))
    img.paste(t_layer, (0, 0), Image.composite(t_layer.getchannel("A"), Image.new("L", (N, N), 0), tongue))
    # record dot
    cx, cy, r = P(32.5, 7.5)[0], P(32.5, 7.5)[1], 3.6 * u
    d.ellipse((cx - r - 0.9 * u, cy - r - 0.9 * u, cx + r + 0.9 * u, cy + r + 0.9 * u), fill=(255, 255, 255, 255))
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(255, 59, 48, 255))
    return img


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    big = make()
    big.resize((512, 512), Image.LANCZOS).save(OUT / "icon.png")
    big.save(OUT / "icon.ico", sizes=[(s, s) for s in (16, 20, 24, 32, 40, 48, 64, 128, 256)])
    print("wrote", OUT / "icon.png", OUT / "icon.ico")
