"""Близость картинки к цветовой гамме темы: тот же счёт, что у подбора клипов в tools/match_wallpapers.py."""
from PIL import Image


def srgb_to_lab(rgb):
    def lin(v):
        v /= 255
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = lin(rgb[0]), lin(rgb[1]), lin(rgb[2])
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883

    def f(t):
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116

    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def distance(a, b):
    return sum((p - q) ** 2 for p, q in zip(a, b)) ** 0.5


def hex_rgb(value):
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def dominant(path, count=8):
    with Image.open(path) as source:
        image = source.convert("RGB").resize((96, 54))
    quantized = image.quantize(count, method=Image.Quantize.MEDIANCUT)
    palette = quantized.getpalette()
    total = 96 * 54
    return [(n / total, srgb_to_lab(tuple(palette[i * 3:i * 3 + 3]))) for n, i in quantized.getcolors()]


def theme_palette(colors):
    """Цвета темы в Lab и светлота её фона."""
    lab = {key: srgb_to_lab(hex_rgb(value)) for key, value in colors.items()}
    return list(lab.values()), lab["background"][0] if "background" in lab else 0


def score(poster, palette, background_l):
    spread = sum(w * min(distance(c, p) for p in palette) for w, c in poster)
    lightness = sum(w * c[0] for w, c in poster)
    return spread + (25 if (lightness > 60) != (background_l > 60) else 0)
