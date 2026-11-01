"""Предпросмотры разделов: картинка собирается из настоящих значений Hyprland, темы и файлов настроек.

Это макет, а не снимок экрана: геометрия и цвета точные, размытие и шрифты примерные.
"""
import getpass
import json
import math
import re
import struct
import threading
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from . import hypr, lockview, looks, monitors, paths, screens

HOME = Path.home()
OUTPUT = HOME / ".cache/oshh/style/scene.png"
WAYBAR = HOME / ".config/waybar/config.jsonc"
KITTY = HOME / ".config/kitty/kitty.conf"
OPTIONS = [
    "general:gaps_in", "general:gaps_out", "general:border_size", "general:col.active_border", "general:col.inactive_border",
    "general:layout", "decoration:rounding", "decoration:rounding_power", "decoration:shadow:enabled", "decoration:shadow:range",
    "decoration:shadow:render_power", "decoration:shadow:sharp", "decoration:shadow:color", "decoration:blur:enabled",
    "decoration:blur:size", "decoration:blur:passes", "decoration:active_opacity", "decoration:inactive_opacity",
    "decoration:dim_inactive", "decoration:dim_strength", "master:mfact", "scrolling:column_width",
]
CURSORS = [("left_ptr", "обычный"), ("hand2", "ссылка"), ("xterm", "текст"), ("watch", "ожидание")]
MOTION_CURVE = (0.0, 0.75, 0.15, 1.0)
ICON_ROOTS = [HOME / ".local/share/icons", HOME / ".icons", Path("/usr/share/icons")]


def read_value(name):
    """Значение из hyprctl getoption: число, флаг, текст или первый цвет градиента."""
    try:
        data = json.loads(hypr.hyprctl("getoption", name, "-j"))
    except ValueError:
        return None
    for key in ("int", "float", "bool", "str"):
        if key in data:
            return data[key]
    if "css" in data:
        return float(data["css"].split()[0])
    return data.get("gradient", data.get("custom"))


def gradient_color(value, default):
    match = re.match(r"([0-9a-fA-F]{2})([0-9a-fA-F]{6})\b", str(value or ""))
    if not match:
        return default
    code = match.group(2)
    return int(code[:2], 16), int(code[2:4], 16), int(code[4:], 16), int(match.group(1), 16)


def rgb(value, default=(200, 200, 200)):
    match = re.fullmatch(r"#([0-9a-fA-F]{6})", str(value or ""))
    if not match:
        return default
    code = match.group(1)
    return int(code[:2], 16), int(code[2:4], 16), int(code[4:], 16)


def size():
    return lockview.WIDTH, lockview.HEIGHT


def scale():
    return lockview.WIDTH / lockview.monitor_width()


def font(family, px):
    return lockview.load_font(lockview.find_font(family), px)


def mono(px):
    return font(current_font(), px)


def current_font():
    return hypr_font() or "monospace"


def hypr_font():
    try:
        match = re.search(r"^\s*font_family\s+(.+?)\s*$", KITTY.read_text(), flags=re.M)
    except OSError:
        return None
    return match.group(1) if match else None


def wallpaper_path():
    return paths.current_dir() / "background"


@lru_cache(maxsize=4)
def wallpaper_image(path, stamp, mode, width, height):
    canvas = Image.new("RGB", (width, height), (0, 0, 0))
    try:
        with Image.open(path) as image:
            image = image.convert("RGB")
    except (OSError, ValueError):
        return canvas
    if mode == "center":
        ratio = width / lockview.monitor_width()
    elif mode == "fit":
        ratio = min(width / image.width, height / image.height)
    else:
        ratio = max(width / image.width, height / image.height)
    image = image.resize((max(1, round(image.width * ratio)), max(1, round(image.height * ratio))))
    canvas.paste(image, ((width - image.width) // 2, (height - image.height) // 2))
    return canvas


def background(mode=None):
    path = wallpaper_path()
    try:
        stamp = path.stat().st_mtime_ns
        path = path.resolve()
    except OSError:
        stamp = 0
    width, height = size()
    return wallpaper_image(str(path), stamp, mode or looks.wallpaper_settings()["MODE"], width, height).convert("RGBA")


def shape(width, height, radius, power=2.0):
    """Маска прямоугольника со скруглением степени power, как rounding_power в Hyprland."""
    ratio = 3
    width, height = max(1, width), max(1, height)
    mask = Image.new("L", (width * ratio, height * ratio), 0)
    draw = ImageDraw.Draw(mask)
    radius = min(radius, width / 2, height / 2) * ratio
    if radius < 1:
        draw.rectangle((0, 0, mask.width, mask.height), fill=255)
    else:
        points = []
        for cx, cy, start in ((mask.width - radius, radius, -90), (mask.width - radius, mask.height - radius, 0),
                              (radius, mask.height - radius, 90), (radius, radius, 180)):
            for step in range(25):
                angle = math.radians(start + step * 90 / 24)
                cos, sin = math.cos(angle), math.sin(angle)
                points.append((cx + radius * abs(cos) ** (2 / power) * math.copysign(1, cos),
                               cy + radius * abs(sin) ** (2 / power) * math.copysign(1, sin)))
        draw.polygon(points, fill=255)
    return mask.resize((width, height), Image.LANCZOS)


def bar_settings():
    try:
        text = WAYBAR.read_text()
    except OSError:
        return "top", 26
    position = re.search(r'"position":\s*"(\w+)"', text)
    height = re.search(r'"(?:height|width)":\s*(\d+)', text)
    return position.group(1) if position else "top", int(height.group(1)) if height else 26


def draw_bar(canvas, colors, factor):
    """Рисует панель и возвращает занятое ею место слева, сверху, справа и снизу."""
    position, height = bar_settings()
    height = max(10, round(height * factor))
    length = canvas.height if position in ("left", "right") else canvas.width
    layer = Image.new("RGBA", (length, height), rgb(colors.get("background"), (20, 20, 28)) + (255,))
    draw = ImageDraw.Draw(layer)
    face = mono(max(8, round(height * 0.55)))
    ink = rgb(colors.get("foreground"))
    draw.text((12, height / 2), "1  2  3  4  5", font=face, fill=ink, anchor="lm")
    draw.text((length / 2, height / 2), "12:00", font=face, fill=ink, anchor="mm")
    if position in ("left", "right"):
        layer = layer.transpose(Image.ROTATE_270)
    place = {"bottom": (0, canvas.height - height), "right": (canvas.width - height, 0)}.get(position, (0, 0))
    canvas.alpha_composite(layer, place)
    return {"left": (height, 0, 0, 0), "right": (0, 0, height, 0), "bottom": (0, 0, 0, height)}.get(position, (0, height, 0, 0))


def layout(values, area, factor):
    """Прямоугольники трёх окон. Первое активное."""
    left, top, right, bottom = area
    gap = values["general:gaps_in"] * factor
    width, height = right - left, bottom - top
    kind = values["general:layout"]
    if kind == "scrolling":
        column = width * (values["scrolling:column_width"] or 0.5)
        return [(left + index * (column + 2 * gap), top, left + index * (column + 2 * gap) + column, bottom) for index in range(3)]
    share = values["master:mfact"] if kind == "master" else 0.5
    middle = left + width * share
    split = top + height / 2
    return [(left, top, middle - gap, bottom), (middle + gap, top, right, split - gap), (middle + gap, split + gap, right, bottom)]


def window_text(index, colors):
    ink = rgb(colors.get("foreground"))
    accent = rgb(colors.get("accent"), ink)
    if index == 0:
        return [("~ $ fastfetch", accent), ("OS: OSHH", ink), ("WM: Hyprland", ink), (f"Тема: {looks.pretty(looks.current_theme())}", ink)]
    if index == 1:
        return [("def main():", rgb(colors.get("color4"), accent)), ("    print('привет')", ink), ("    return 0", ink)]
    return [("~/Документы", accent), ("проект/  заметки.md", ink), ("фото/    отчёт.pdf", ink)]


def draw_window(canvas, box, index, values, colors, factor):
    active = index == 0
    left, top, right, bottom = (round(value) for value in box)
    width, height = right - left, bottom - top
    if width < 4 or height < 4 or left >= canvas.width:
        return
    radius = values["decoration:rounding"] * factor
    power = values["decoration:rounding_power"] or 2
    outer = shape(width, height, radius, power)
    if values["decoration:shadow:enabled"]:
        spread = values["decoration:shadow:range"] * factor
        shade = gradient_color(values["decoration:shadow:color"], (20, 20, 20, 238))
        layer = Image.new("L", canvas.size, 0)
        layer.paste(outer, (left, top))
        if not values["decoration:shadow:sharp"]:
            layer = layer.filter(ImageFilter.GaussianBlur(spread / max(1, (values["decoration:shadow:render_power"] or 3) / 2)))
        else:
            layer = layer.filter(ImageFilter.MaxFilter(max(3, round(spread) | 1)))
        tint = Image.new("RGBA", canvas.size, shade[:3] + (0,))
        tint.putalpha(layer.point(lambda value: value * shade[3] // 255))
        canvas.alpha_composite(tint)
    border = round(values["general:border_size"] * factor) if values["general:border_size"] else 0
    inner_box = (left + border, top + border, right - border, bottom - border)
    inner_width, inner_height = inner_box[2] - inner_box[0], inner_box[3] - inner_box[1]
    inner = shape(inner_width, inner_height, max(0, radius - border), power)
    opacity = values["decoration:active_opacity" if active else "decoration:inactive_opacity"]
    under = canvas.crop(inner_box)
    if values["decoration:blur:enabled"] and opacity < 1:
        under = under.filter(ImageFilter.GaussianBlur(values["decoration:blur:size"] * values["decoration:blur:passes"] * factor))
    fill = Image.new("RGBA", under.size, rgb(colors.get("background"), (20, 20, 28)) + (round(opacity * 255),))
    body = Image.alpha_composite(under, fill)
    draw = ImageDraw.Draw(body)
    face = mono(max(9, round(14 * factor * 1.4)))
    for line, (text, color) in enumerate(window_text(index, colors)):
        draw.text((10, 8 + line * face.size * 1.4), text, font=face, fill=color + (255,))
    if not active and values["decoration:dim_inactive"]:
        body = Image.alpha_composite(body, Image.new("RGBA", body.size, (0, 0, 0, round(values["decoration:dim_strength"] * 255))))
    if border:
        name = "general:col.active_border" if active else "general:col.inactive_border"
        tone = gradient_color(values[name], (130, 130, 130, 255))
        ring = Image.new("RGBA", (width, height), tone[:3] + (0,))
        ring.putalpha(outer.point(lambda value: value * tone[3] // 255))
        canvas.alpha_composite(ring, (left, top))
        clear = canvas.crop(inner_box)
        original = background().crop(inner_box)
        clear.paste(original, (0, 0), inner)
        canvas.paste(clear, inner_box[:2])
    canvas.paste(body, inner_box[:2], inner)


def desktop(overrides=None, windows=True, mode=None):
    values = {name: read_value(name) for name in OPTIONS}
    values.update(overrides or {})
    for name, value in values.items():
        if value is None:
            values[name] = 0
    colors = looks.current_colors()
    factor = scale()
    canvas = background(mode)
    width, height = size()
    reserve = draw_bar(canvas, colors, factor)
    outer = values["general:gaps_out"] * factor
    area = (reserve[0] + outer, reserve[1] + outer, width - reserve[2] - outer, height - reserve[3] - outer)
    if windows:
        boxes = layout(values, area, factor)
        for index in (2, 1, 0):
            draw_window(canvas, boxes[index], index, values, colors, factor)
    return canvas


def greeter(store):
    settings = screens.greeter_settings(store.state)
    colors = looks.current_colors()
    width, height = size()
    factor = scale()
    canvas = Image.new("RGBA", (width, height), rgb(colors.get("background"), (20, 20, 28)) + (255,))
    if settings["background"] == "theme":
        canvas = background("fill")
        if settings["blur"] > 0:
            canvas = canvas.filter(ImageFilter.GaussianBlur(settings["blur"] * 48 * factor))
    canvas = Image.alpha_composite(canvas, Image.new("RGBA", (width, height), (0, 0, 0, round(settings["dim"] * 255))))
    draw = ImageDraw.Draw(canvas)
    ink = rgb(colors.get("foreground"), (230, 230, 230))
    if settings["clock"]:
        draw.text((width / 2, 80 * factor), "12:00", font=font("sans:bold", 88 * factor), fill=ink, anchor="mt")
    panel = 320 * factor
    field = 40 * factor
    gap = 12 * factor
    left = {"left": 120 * factor, "right": width - 120 * factor - panel}.get(settings["panel"], (width - panel) / 2)
    top = height / 2 - (48 * factor + 2 * field + 2 * gap) / 2
    draw.text((left + panel / 2, top), str(settings["title"]), font=font("sans:bold", 40 * factor), fill=ink, anchor="mt")
    top += 48 * factor + gap
    labels = [(getpass.getuser() if settings["remember_user"] else "") or "Пользователь", "Пароль"]
    face = font("sans", 18 * factor)
    for label in labels:
        draw.rounded_rectangle((left, top, left + panel, top + field), 6 * factor, fill=rgb(colors.get("background"), (20, 20, 28)),
                               outline=ink, width=max(1, round(1.5 * factor)))
        draw.text((left + 14 * factor, top + field / 2), label, font=face, fill=ink, anchor="lm")
        top += field + gap
    return canvas



def fonts(overrides=None):
    overrides = overrides or {}
    colors = looks.current_colors()
    family = overrides.get("font") or current_font()
    canvas = Image.new("RGBA", size(), rgb(colors.get("background"), (20, 20, 28)) + (255,))
    draw = ImageDraw.Draw(canvas)
    ink = rgb(colors.get("foreground"))
    accent = rgb(colors.get("accent"), ink)
    draw.text((48, 40), family, font=font(family, 44), fill=accent)
    lines = ["Съешь же ещё этих мягких французских булок", "The quick brown fox jumps over the lazy dog",
             "0123456789 ()[]{} != -> => <= // 0O 1lI"]
    top = 130
    for text in lines:
        draw.text((48, top), text, font=font(family, 30), fill=ink)
        top += 56
    try:
        match = re.search(r"^font_size\s+(\d+)", KITTY.read_text(), flags=re.M)
        px = int(match.group(1)) if match else 12
    except OSError:
        px = 12
    top += 30
    draw.text((48, top), f"Терминал, {px} пт:", font=font(family, 20), fill=accent)
    top += 40
    for text in ("~ $ git status", "Изменения не в индексе:", "    изменено: README.md"):
        draw.text((48, top), text, font=font(family, round(px * 1.8)), fill=ink)
        top += round(px * 1.8 * 1.5)
    return canvas


def cursor_file(theme, name):
    for root in ICON_ROOTS:
        path = root / theme / "cursors" / name
        if path.exists():
            return path
    return None


def read_cursor(path, nominal):
    """Формат Xcursor: оглавление и картинки BGRA с предумноженной альфой."""
    data = path.read_bytes()
    if data[:4] != b"Xcur":
        return None
    header, _, count = struct.unpack_from("<III", data, 4)
    table = []
    for index in range(count):
        kind, subtype, position = struct.unpack_from("<III", data, header + index * 12)
        if kind == 0xfffd0002:
            table.append((abs(subtype - nominal), subtype, position))
    if not table:
        return None
    position = min(table)[2]
    width, height = struct.unpack_from("<II", data, position + 16)
    pixels = data[position + 36:position + 36 + width * height * 4]
    return Image.frombuffer("RGBa", (width, height), pixels, "raw", "BGRa", 0, 1).convert("RGBA")


def cursor(store, overrides=None):
    overrides = overrides or {}
    env = store.state["env"]
    theme = overrides.get("cursor_theme") or env.get("XCURSOR_THEME", "Adwaita")
    nominal = int(overrides.get("cursor_size") or env.get("XCURSOR_SIZE", 24))
    canvas = background()
    canvas = Image.alpha_composite(canvas, Image.new("RGBA", canvas.size, (0, 0, 0, 120)))
    draw = ImageDraw.Draw(canvas)
    colors = looks.current_colors()
    ink = rgb(colors.get("foreground"), (235, 235, 235))
    draw.text((64, 48), f"{theme}, {nominal} пикс.", font=mono(32), fill=ink)
    step = canvas.width // len(CURSORS)
    factor = scale()
    for index, (name, title) in enumerate(CURSORS):
        path = cursor_file(theme, name)
        image = read_cursor(path, nominal) if path else None
        x = index * step + 64
        draw.text((x, 150), title, font=mono(20), fill=ink)
        if image is None:
            draw.text((x, 220), "нет", font=mono(20), fill=ink)
            continue
        real = image.resize((max(1, round(image.width * nominal / image.height * factor)), max(1, round(nominal * factor))), Image.LANCZOS)
        canvas.alpha_composite(real, (x, 200))
        big = image.resize((image.width * 4, image.height * 4), Image.NEAREST)
        canvas.alpha_composite(big, (x, 300))
    draw.text((64, canvas.height - 64), "Сверху размер на экране, снизу увеличено", font=mono(20), fill=ink)
    return canvas


def save(canvas, target=OUTPUT):
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f"{target.stem}.{threading.get_ident()}.png")
    canvas.convert("RGB").save(temporary)
    temporary.replace(target)
    return target


def bezier(points, x):
    """Значение кривой в момент x: параметр ищем делением пополам."""
    x0, y0, x1, y1 = points

    def axis(a, b, t):
        return 3 * a * t * (1 - t) ** 2 + 3 * b * t * t * (1 - t) + t ** 3

    low, high = 0.0, 1.0
    for _ in range(18):
        middle = (low + high) / 2
        if axis(x0, x1, middle) < x:
            low = middle
        else:
            high = middle
    return axis(y0, y1, (low + high) / 2)


def mini_window(screen, box, colors, border, alpha=1.0):
    """Окно на миниатюре экрана. Выход за край обрезается, прозрачность — как при затухании."""
    left, top, right, bottom = [round(value) for value in box]
    if right <= left or bottom <= top:
        return
    layer = Image.new("RGBA", screen.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    opacity = round(255 * min(1.0, max(0.0, alpha)))
    draw.rounded_rectangle((left, top, right, bottom), 5, fill=rgb(colors.get("background"), (20, 20, 28)) + (opacity,),
                           outline=border + (opacity,), width=2)
    ink = rgb(colors.get("foreground")) + (opacity // 2,)
    for line in range(3):
        y = top + (line + 1) * 11
        if y < bottom - 4 and right - left > 40:
            draw.line((left + 9, y, left + 9 + (right - left - 18) * (3 - line) // 4, y), fill=ink, width=2)
    screen.alpha_composite(layer)


def mini_frame(back, value, leaf, style, colors):
    """Один кадр анимации на миниатюре экрана. value — положение на кривой, может выйти за 1."""
    width, height = back.size
    accent = rgb(colors.get("accent"), (120, 170, 240))
    box = (width * 0.2, height * 0.16, width * 0.8, height * 0.84)
    screen = back.copy()
    style = str(style or "")
    if leaf.startswith(("workspaces", "specialWorkspace")):
        old, new = back.copy(), back.copy()
        mini_window(old, (width * 0.08, height * 0.14, width * 0.6, height * 0.86), colors, accent)
        mini_window(new, (width * 0.4, height * 0.14, width * 0.92, height * 0.86), colors, accent)
        share = min(1.0, max(0.0, value))
        if style.startswith("fade"):
            return Image.blend(old, new, share)
        if style.startswith("slidefade"):
            old, new = Image.blend(old, back, share), Image.blend(back, new, share)
        if style.startswith("slidevert"):
            screen.paste(old, (0, round(-height * value)))
            screen.paste(new, (0, round(height * (1 - value))))
        else:
            screen.paste(old, (round(-width * value), 0))
            screen.paste(new, (round(width * (1 - value)), 0))
        return screen
    if leaf == "border":
        muted = rgb(colors.get("color8"), (90, 90, 90))
        share = min(1.0, max(0.0, value))
        mini_window(screen, box, colors, tuple(round(a + (b - a) * share) for a, b in zip(muted, accent)))
        return screen
    if leaf.endswith("Out"):
        value = 1 - value
    left, top, right, bottom = box
    if leaf == "windows":
        shift = (1 - value) * width * 0.3
        mini_window(screen, (left - shift, top, right - shift, bottom), colors, accent)
    elif leaf.startswith("fade") or style == "fade":
        mini_window(screen, box, colors, accent, value)
    elif style.startswith("gnomed"):
        middle, half = height / 2, (bottom - top) / 2 * value
        mini_window(screen, (left, middle - half, right, middle + half), colors, accent)
    elif style.startswith("popin"):
        match = re.search(r"(\d+)%", style)
        start = int(match.group(1)) / 100 if match else 0.5
        share = start + (1 - start) * value
        center_x, center_y = width / 2, height / 2
        half_x, half_y = (right - left) / 2 * share, (bottom - top) / 2 * share
        mini_window(screen, (center_x - half_x, center_y - half_y, center_x + half_x, center_y + half_y), colors, accent, value)
    else:
        shift = (1 - value) * (width - left)
        mini_window(screen, (left + shift, top, right + shift, bottom), colors, accent)
    return screen


def motion(store, overrides=None):
    """Раскадровка и график: сразу видно и как движется окно, и как разгоняется анимация."""
    request = overrides or {}
    leaf = request.get("leaf") or "windows"
    spec = store.animation(leaf)
    points = hypr.curve_points().get(spec.get("bezier"), MOTION_CURVE)
    duration = float(spec.get("speed") or 0) / 10
    colors = looks.current_colors()
    canvas = Image.new("RGBA", size(), rgb(colors.get("background"), (20, 20, 28)) + (255,))
    draw = ImageDraw.Draw(canvas)
    ink = rgb(colors.get("foreground"))
    accent = rgb(colors.get("accent"), ink)
    muted = rgb(colors.get("color8"), (120, 120, 120))
    style = spec.get("style") or ""
    head = f"{request.get('title') or 'Анимация'}: {duration:.2f} с, кривая {spec.get('bezier') or 'default'}"
    draw.text((40, 26), head + (f", стиль {style}" if style else ""), font=mono(24), fill=ink)
    if not spec.get("enabled", True):
        draw.text((40, 58), "Анимация выключена: так она будет выглядеть после включения", font=mono(18), fill=muted)
    width, height = 232, 130
    back = background().resize((width, height)).convert("RGBA")
    moments = (0.0, 0.06, 0.15, 0.35, 1.0)
    for index, moment in enumerate(moments):
        left = 40 + index * (width + 16)
        canvas.paste(mini_frame(back, bezier(points, moment) if moment else 0.0, leaf, style, colors), (left, 92))
        draw.text((left + width / 2, 232), f"{round(moment * duration * 1000)} мс", font=mono(18), fill=ink, anchor="mt")
    left, right, top, bottom = 90, 1240, 280, 690
    place_x = lambda share: left + (right - left) * share
    place_y = lambda value: bottom - (bottom - top) * (min(1.4, max(-0.2, value)) + 0.2) / 1.6
    for value, label in ((0, "начало"), (1, "конец")):
        draw.line((left, place_y(value), right, place_y(value)), fill=muted, width=1)
        draw.text((left - 8, place_y(value)), label, font=mono(14), fill=muted, anchor="rm")
    curve = [(place_x(step / 200), place_y(bezier(points, step / 200))) for step in range(201)]
    draw.line(curve, fill=accent, width=4, joint="curve")
    for moment in moments:
        x, y = place_x(moment), place_y(bezier(points, moment) if moment else 0.0)
        draw.ellipse((x - 7, y - 7, x + 7, y + 7), fill=accent, outline=ink, width=2)
    return canvas


def monitor_map(overrides=None):
    """Экраны в масштабе: выбранный обведён цветом темы, размеры логические, как их видит мышь."""
    selected = (overrides or {}).get("monitor")
    colors = looks.current_colors()
    paper, ink = rgb(colors.get("background"), (20, 20, 28)), rgb(colors.get("foreground"))
    accent = rgb(colors.get("accent"), ink)
    canvas = Image.new("RGBA", size(), paper + (255,))
    draw = ImageDraw.Draw(canvas)
    items = monitors.listing()
    shown = [item for item in items if not item["disabled"] and item["mirrorOf"] == "none"]
    boxes = [(item["x"], item["y"], *monitors.size(item)) for item in shown]
    if boxes:
        left, top = min(x for x, _, _, _ in boxes), min(y for _, y, _, _ in boxes)
        wide, tall = max(x + w for x, _, w, _ in boxes) - left, max(y + h for _, y, _, h in boxes) - top
        factor = min((canvas.width - 80) / wide, (canvas.height - 130) / tall)
        shift_x, shift_y = (canvas.width - wide * factor) / 2, (canvas.height - 60 - tall * factor) / 2
    for item, (x, y, width, height) in zip(shown, boxes):
        box = (shift_x + (x - left) * factor, shift_y + (y - top) * factor)
        box += (box[0] + width * factor - 6, box[1] + height * factor - 6)
        active = item["name"] == selected
        tone = tuple(round(p + (i - p) * (0.2 if active else 0.1)) for p, i in zip(paper, ink))
        draw.rounded_rectangle(box, 10, fill=tone, outline=accent if active else tuple(round(p + (i - p) * 0.4) for p, i in zip(paper, ink)), width=4 if active else 2)
        middle = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
        draw.text((middle[0], middle[1] - 30), item["name"], font=mono(30), fill=accent if active else ink, anchor="mm")
        lines = [f"{item['width']} × {item['height']}, {item['refreshRate']:.0f} Гц"]
        lines.append(f"масштаб {item['scale'] * 100:.0f} %" + (", повёрнут" if item["transform"] else ""))
        draw.multiline_text((middle[0], middle[1] + 24), "\n".join(lines), font=mono(20), fill=ink, anchor="ma", align="center")
    off = [item["name"] for item in items if item["disabled"]]
    copies = [item["name"] for item in items if not item["disabled"] and item["mirrorOf"] != "none"]
    notes = ([f"Выключены: {', '.join(off)}"] if off else []) + ([f"Повторяют другой экран: {', '.join(copies)}"] if copies else [])
    if notes:
        draw.text((40, canvas.height - 40), ".  ".join(notes), font=mono(20), fill=ink, anchor="lm")
    return canvas


def render(kind, store, overrides=None):
    if kind == "lock":
        return lockview.render()
    if kind == "motion":
        return save(motion(store, overrides))
    if kind == "greeter":
        return save(greeter(store))
    if kind == "monitors":
        return save(monitor_map(overrides))
    if kind == "wall":
        return save(desktop(windows=False))
    if kind == "font":
        return save(fonts(overrides))
    if kind == "cursor":
        return save(cursor(store, overrides))
    return save(desktop(overrides))
