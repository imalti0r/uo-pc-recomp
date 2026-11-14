"""Make the launcher's artwork: the backdrop from the supplied key art, and a wordmark.

The backdrop is the Unicorn Overlord key art the user supplied (build/art_src.jpeg):
cropped to 16:9, graded slightly cooler at the edges, and faded into a dark panel on
the left so the wordmark and controls stay legible. That image is the game's own
artwork, not this project's; see art/README.md.

The wordmark is lettering set in Roboto (Apache-2.0, already in runtime/ui/fonts)
and filled with a gold gradient, drawn from scratch.

    python tools/make_launcher_art.py [--source build/art_src.jpeg]
"""
import argparse
import pathlib

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = pathlib.Path(__file__).resolve().parents[1]
ART = ROOT / 'uo_setup' / 'art'
FONT = ROOT / 'runtime' / 'ui' / 'fonts' / 'Roboto-Medium.ttf'

W, H = 2560, 1440


def vertical_gradient(size, stops):
    """A vertical gradient through (position 0..1, colour) stops."""
    w, h = size
    column = Image.new('RGB', (1, h))
    pixels = column.load()
    for y in range(h):
        t = y / (h - 1)
        for (p0, c0), (p1, c1) in zip(stops, stops[1:]):
            if p0 <= t <= p1:
                k = (t - p0) / (p1 - p0) if p1 > p0 else 0.0
                pixels[0, y] = tuple(int(a + (b - a) * k) for a, b in zip(c0, c1))
                break
        else:
            pixels[0, y] = stops[-1][1]
    return column.resize((w, h), Image.Resampling.BILINEAR)


def backdrop(source):
    """The key art, fitted to 16:9, graded, and faded into a dark panel on the left."""
    with Image.open(source) as image:
        scene = image.convert('RGB')
    # Fit to the window's shape, keeping the middle of the group and the horizon.
    scale = max(W / scene.width, H / scene.height)
    scene = scene.resize((round(scene.width * scale), round(scene.height * scale)), Image.Resampling.LANCZOS)
    left = (scene.width - W) // 2
    top = int((scene.height - H) * 0.42)
    scene = scene.crop((left, top, left + W, top + H))

    # A cool grade toward the edges, so the warm figures stay the subject.
    cool = Image.new('RGB', (W, H), (26, 40, 62))
    cool_mask = Image.new('L', (W, H), 0)
    ImageDraw.Draw(cool_mask).ellipse((W * 0.16, -H * 0.10, W * 0.92, H * 1.10), fill=255)
    cool_mask = cool_mask.filter(ImageFilter.GaussianBlur(W * 0.07)).point(lambda v: 255 - v)
    scene = Image.composite(cool, scene, cool_mask.point(lambda v: int(v * 0.55)))

    # Vignette: the corners fall away.
    vignette = Image.new('L', (W, H), 0)
    ImageDraw.Draw(vignette).ellipse((-W * 0.14, -H * 0.20, W * 1.14, H * 1.20), fill=255)
    vignette = vignette.filter(ImageFilter.GaussianBlur(W * 0.09))
    scene = Image.composite(scene, Image.new('RGB', (W, H), (7, 12, 22)), vignette)

    # The dark panel on the left, faded rather than cut, so the wordmark and the
    # status line sit on a calm ground while the art still shows through.
    panel = Image.new('L', (W, H), 0)
    pixels = panel.load()
    edge = int(W * 0.46)
    for x in range(edge):
        t = x / edge
        value = int(252 * (1 - t) ** 1.30)
        for y in range(H):
            pixels[x, y] = value
    panel = panel.filter(ImageFilter.GaussianBlur(2))
    scene = Image.composite(Image.new('RGB', (W, H), (8, 14, 26)), scene, panel)

    # A soft bottom band under the summary strip.
    band = Image.new('L', (W, H), 0)
    band_pixels = band.load()
    for y in range(int(H * 0.80), H):
        t = (y - H * 0.80) / (H * 0.20)
        for x in range(W):
            band_pixels[x, y] = int(210 * t ** 1.5)
    scene = Image.composite(Image.new('RGB', (W, H), (7, 12, 22)), scene, band.filter(ImageFilter.GaussianBlur(3)))
    return scene


def set_text(draw, xy, text, font, tracking, fill, anchor_left=True):
    """Lettering with tracking: draw each glyph, returning the total width."""
    widths = [draw.textlength(ch, font=font) for ch in text]
    total = sum(widths) + tracking * (len(text) - 1)
    x, y = xy
    if not anchor_left:
        x -= total / 2
    for ch, w in zip(text, widths):
        draw.text((x, y), ch, font=font, fill=fill)
        x += w + tracking
    return total


def wordmark():
    """Transparent lettering: UNICORN / OVERLORD over a rule, then PC RECOMP."""
    scale = 2
    big = ImageFont.truetype(str(FONT), 132 * scale)
    small = ImageFont.truetype(str(FONT), 30 * scale)
    tracking_big, tracking_small = 20 * scale, 14 * scale

    probe = ImageDraw.Draw(Image.new('RGBA', (8, 8)))
    w1 = probe.textlength('UNICORN', font=big) + tracking_big * 6
    w2 = probe.textlength('OVERLORD', font=big) + tracking_big * 7
    ws = probe.textlength('PC RECOMP', font=small) + tracking_small * 8
    width = int(max(w1, w2, ws) + 40 * scale)
    line_h = int(150 * scale)
    height = int(line_h * 2 + 96 * scale)

    mask = Image.new('L', (width, height), 0)
    draw = ImageDraw.Draw(mask)
    set_text(draw, ((width - w1) / 2, 0), 'UNICORN', big, tracking_big, 255)
    set_text(draw, ((width - w2) / 2, line_h * 0.92), 'OVERLORD', big, tracking_big, 255)
    set_text(draw, ((width - ws) / 2, line_h * 1.92 + 26 * scale), 'PC RECOMP', small, tracking_small, 255)

    # The rule between the name and the subtitle: a line, a gap, a diamond.
    rule_y = int(line_h * 1.92 + 6 * scale)
    draw.rectangle((width * 0.5 - 330 * scale, rule_y, width * 0.5 - 40 * scale, rule_y + 2 * scale), fill=190)
    draw.rectangle((width * 0.5 + 40 * scale, rule_y, width * 0.5 + 330 * scale, rule_y + 2 * scale), fill=190)
    diamond = 13 * scale
    draw.polygon([(width * 0.5, rule_y + 1 * scale - diamond), (width * 0.5 + diamond, rule_y + 1 * scale),
                  (width * 0.5, rule_y + 1 * scale + diamond), (width * 0.5 - diamond, rule_y + 1 * scale)], fill=255)

    # Gold gradient: pale at the top of each line, deep amber at the bottom.
    gold = vertical_gradient((width, height), [
        (0.00, (255, 246, 214)),
        (0.30, (247, 222, 150)),
        (0.55, (226, 178, 82)),
        (0.78, (176, 126, 44)),
        (1.00, (214, 168, 92)),
    ]).convert('RGBA')

    # A dark edge under the fill keeps the lettering legible on the bright art.
    outline = mask.filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.GaussianBlur(1.2))
    art = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    art.paste(Image.new('RGBA', (width, height), (10, 17, 30, 255)), (0, 0), outline)
    art.paste(gold, (0, 0), mask)
    art = art.resize((width // scale, height // scale), Image.Resampling.LANCZOS)
    return art.crop(art.getbbox())


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--source', type=pathlib.Path, default=ROOT / 'build' / 'art_src.jpeg')
    args = ap.parse_args()
    ART.mkdir(parents=True, exist_ok=True)
    scene = backdrop(args.source)
    scene.save(ART / 'backdrop.jpg', quality=92, optimize=True)
    mark = wordmark()
    mark.save(ART / 'wordmark.png', optimize=True)
    print(f'backdrop.jpg {scene.size[0]}x{scene.size[1]}')
    print(f'wordmark.png {mark.size[0]}x{mark.size[1]}')


if __name__ == '__main__':
    main()
