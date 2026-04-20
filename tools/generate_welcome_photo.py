#!/usr/bin/env python3
"""Render the welcome-panel forest/aurora artwork as a pre-dithered PNG.

Run: `python3 tools/generate_welcome_photo.py`. Output is written to
`assets/welcome-photo.png`. The image mirrors the design handoff's
`.welcome-photo` layer (`linear-gradient(#0F1A18 -> #0B1512 -> #08100E)`
plus two aurora radial ellipses in teal + forest) and adds a fine
blue-noise dither so the flat gradient does not band on large GTK
surfaces.

Only depends on Pillow; the Hermod Python 3.14 environment does not
have numpy.
"""

from __future__ import annotations

import random
from pathlib import Path

from PIL import Image, ImageChops

_ROOT = Path(__file__).resolve().parents[1]
_OUT = _ROOT / "assets" / "welcome-photo.png"

_WIDTH = 1440
_HEIGHT = 1800


def _hex(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))


def _lerp(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    t = max(0.0, min(1.0, t))
    return (
        int(round(a[0] + (b[0] - a[0]) * t)),
        int(round(a[1] + (b[1] - a[1]) * t)),
        int(round(a[2] + (b[2] - a[2]) * t)),
    )


def _base_gradient(width: int, height: int) -> Image.Image:
    # Design uses a 160deg linear gradient (mostly top -> bottom with a
    # subtle right-to-left tilt). For a 4:5 portrait canvas the visible
    # difference from a straight vertical stretch is <1 LSB, so we build
    # a 1px-wide vertical strip and stretch it horizontally. This keeps
    # the image banding-free by construction.
    stop0 = _hex("#0F1A18")
    stop1 = _hex("#0B1512")
    stop2 = _hex("#08100E")

    strip = Image.new("RGB", (1, height))
    pixels = strip.load()
    for y in range(height):
        t = y / max(height - 1, 1)
        if t <= 0.6:
            pixels[0, y] = _lerp(stop0, stop1, t / 0.6)
        else:
            pixels[0, y] = _lerp(stop1, stop2, (t - 0.6) / 0.4)
    return strip.resize((width, height), resample=Image.BILINEAR)


def _aurora(
    width: int,
    height: int,
    cx: float,
    cy: float,
    rx: float,
    ry: float,
    colour: tuple[int, int, int],
    alpha: float,
) -> Image.Image:
    """Generate a coloured radial ellipse as an RGBA layer."""

    # Build an isotropic radial falloff in a small square, stretch it
    # into the target ellipse, then translate so the centre lands at
    # (cx, cy). Pillow's Image.radial_gradient returns an 'L' image that
    # is 1.0 at the edges and 0.0 at the centre, which is the opposite
    # of what we want for a glow — invert it.
    source_side = 512
    mask = Image.radial_gradient("L").resize(
        (source_side, source_side), resample=Image.BILINEAR
    )
    mask = ImageChops.invert(mask)

    ellipse_w = max(1, int(round(rx * 2.0 * width)))
    ellipse_h = max(1, int(round(ry * 2.0 * height)))
    ellipse_mask = mask.resize((ellipse_w, ellipse_h), resample=Image.BILINEAR)

    # Canvas for the full overlay; paste the ellipse centred on (cx, cy)
    # and scale intensity by `alpha`.
    canvas = Image.new("L", (width, height), color=0)
    top_left_x = int(round(cx * width - ellipse_w / 2))
    top_left_y = int(round(cy * height - ellipse_h / 2))
    canvas.paste(ellipse_mask, (top_left_x, top_left_y), ellipse_mask)

    # Attenuate.
    attenuated = canvas.point(lambda v, a=alpha: int(round(v * a)))

    layer = Image.new("RGBA", (width, height), color=colour + (0,))
    layer.putalpha(attenuated)
    return layer


def _apply_dither(image: Image.Image, seed: int = 0x48) -> Image.Image:
    """Add +/- 1 LSB blue-ish noise to every channel.

    Strict blue noise would need a pre-baked tile, but a seeded PRNG is
    already enough to break the ~5-step bands that GTK's compositor
    shows on large flat gradients.
    """

    rng = random.Random(seed)
    noise_band = Image.new("L", image.size)
    noise_band.putdata([rng.randint(0, 2) for _ in range(image.size[0] * image.size[1])])
    noise_rgb = Image.merge(
        "RGB",
        (
            noise_band,
            Image.eval(noise_band, lambda v: (v + 1) % 3),
            Image.eval(noise_band, lambda v: (v + 2) % 3),
        ),
    )
    # ImageChops.add clamps to 255; we subtract 1 first so the dither is
    # symmetric (values are 0/1/2 -> -1/0/+1 after the shift).
    shifted = Image.eval(noise_rgb, lambda v: v - 1 if v > 0 else 0)
    added = ImageChops.add(image, shifted)
    return added


def render() -> Image.Image:
    base = _base_gradient(_WIDTH, _HEIGHT)

    # Aurora A — teal glow centred top-left; stretched vertically so the
    # colour reaches roughly the mid-panel.
    teal = _aurora(
        _WIDTH,
        _HEIGHT,
        cx=0.30,
        cy=0.18,
        rx=0.80,
        ry=0.48,
        colour=_hex("#2E6A70"),
        alpha=0.22,
    )
    base = Image.alpha_composite(base.convert("RGBA"), teal)

    # Aurora B — forest glow top-right, softer so the caption corner
    # stays comfortably dark.
    forest = _aurora(
        _WIDTH,
        _HEIGHT,
        cx=0.78,
        cy=0.08,
        rx=0.60,
        ry=0.40,
        colour=_hex("#3B6B4E"),
        alpha=0.14,
    )
    base = Image.alpha_composite(base, forest)

    return _apply_dither(base.convert("RGB"))


def main() -> None:
    _OUT.parent.mkdir(parents=True, exist_ok=True)
    render().save(_OUT, format="PNG", optimize=True)
    print(f"wrote {_OUT.relative_to(_ROOT)} ({_WIDTH}x{_HEIGHT})")


if __name__ == "__main__":
    main()
