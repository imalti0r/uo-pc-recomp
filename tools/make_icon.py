"""Turn the key art into the port's rounded-square icon.

The icon is the artwork itself with its corners rounded and a transparent margin, so it reads as
an app tile on a dark taskbar or a light launcher. Sizes follow what Windows asks for: 16 to 256
in the .ico (256 is what the shell uses at large icon sizes), and a 1024 PNG for the launcher.

    python tools/make_icon.py <source.png> <out.ico> <out.png> [--radius 0.22]
"""
import argparse
import pathlib

from PIL import Image, ImageDraw


def rounded(img, radius_frac, inset_frac, size):
    """The image cropped to a square, rounded by radius_frac of its side, on a transparent tile."""
    w, h = img.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    square = img.crop((left, top, left + side, top + side)).convert('RGBA')

    # A transparent border keeps the rounded corner visible instead of being clipped by the tile edge.
    inset = int(side * inset_frac)
    inner = side - 2 * inset
    square = square.resize((inner, inner), Image.LANCZOS)

    tile = Image.new('RGBA', (side, side), (0, 0, 0, 0))
    tile.paste(square, (inset, inset))

    # The rounded mask covers the artwork's area, so the corners are cut and the border stays clear.
    mask = Image.new('L', (side, side), 0)
    draw = ImageDraw.Draw(mask)
    r = int(inner * radius_frac)
    draw.rounded_rectangle((inset, inset, inset + inner - 1, inset + inner - 1), radius=r, fill=255)
    tile.putalpha(Image.composite(tile.getchannel('A'), Image.new('L', (side, side), 0), mask))

    return tile.resize((size, size), Image.LANCZOS) if size != side else tile


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('source', type=pathlib.Path)
    ap.add_argument('ico', type=pathlib.Path)
    ap.add_argument('png', type=pathlib.Path)
    ap.add_argument('--radius', type=float, default=0.22)
    ap.add_argument('--inset', type=float, default=0.02)
    args = ap.parse_args()

    art = Image.open(args.source).convert('RGBA')
    big = rounded(art, args.radius, args.inset, 1024)
    args.png.parent.mkdir(parents=True, exist_ok=True)
    big.save(args.png)

    sizes = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256]
    frames = [rounded(art, args.radius, args.inset, s) for s in sizes]
    args.ico.parent.mkdir(parents=True, exist_ok=True)
    frames[-1].save(args.ico, format='ICO', sizes=[(s, s) for s in sizes], append_images=frames[:-1])
    print(f'{args.png}: 1024x1024')
    print(f'{args.ico}: {len(sizes)} sizes ({", ".join(str(s) for s in sizes)})')


if __name__ == '__main__':
    main()
