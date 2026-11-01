"""Предпросмотр экрана блокировки: картинка по настоящему hyprlock.conf.

Hyprlock не умеет рисовать в файл, поэтому виджеты input-field, image и label собираем сами.
Размытие и шрифт примерные, положение, размеры и цвета берутся из конфига.
"""
import json
import re
import subprocess
import threading
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from . import hypr, screens

HOME = Path.home()
OUTPUT = HOME / ".cache/oshh/style/lock-preview.png"
WIDTH = 1280
HEIGHT = 720
FALLBACK_COLOR = (20, 20, 28, 255)
WHITE = (240, 240, 240, 255)


def read_conf(path, seen=None):
    """Текст конфига вместе с файлами из source = ..."""
    seen = seen if seen is not None else set()
    path = Path(path).expanduser()
    if path in seen:
        return ""
    seen.add(path)
    try:
        text = path.read_text()
    except OSError:
        return ""
    return re.sub(r"^\s*source\s*=\s*(.+?)\s*$", lambda m: read_conf(m.group(1), seen), text, flags=re.M)


def variables(text):
    return dict(re.findall(r"^\s*(\$\w+)\s*=\s*(.+?)\s*$", text, flags=re.M))


def sections(text, name):
    """Все блоки name { ... } в виде словарей."""
    result = []
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        if re.match(rf"\s*{re.escape(name)}\s*\{{", lines[index]):
            block = {}
            index += 1
            while index < len(lines) and "}" not in re.sub(r"\$\{.*?\}", "", lines[index]):
                match = re.match(r"\s*([\w:.-]+)\s*=\s*(.*?)\s*$", lines[index])
                if match:
                    block[match.group(1)] = match.group(2)
                index += 1
            result.append(block)
        index += 1
    return result


def color(value, names, default=WHITE):
    """rgba(1,2,3,0.5), rgb(1,2,3), rgba(rrggbbaa), rgb(rrggbb), 0xaarrggbb и $имя."""
    value = str(names.get(value, value)).strip()
    match = re.fullmatch(r"rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*([\d.]+)\s*)?\)", value)
    if match:
        r, g, b, a = match.groups()
        return int(float(r)), int(float(g)), int(float(b)), round(float(a or 1) * 255)
    match = re.fullmatch(r"rgba?\(\s*([0-9a-fA-F]{6})([0-9a-fA-F]{2})?\s*\)", value)
    if match:
        code, alpha = match.group(1), match.group(2) or "ff"
        return int(code[:2], 16), int(code[2:4], 16), int(code[4:], 16), int(alpha, 16)
    match = re.fullmatch(r"0x([0-9a-fA-F]{2})([0-9a-fA-F]{6})", value)
    if match:
        code = match.group(2)
        return int(code[:2], 16), int(code[2:4], 16), int(code[4:], 16), int(match.group(1), 16)
    return default


def number(value, default=0.0):
    try:
        return float(str(value).split(",")[0])
    except ValueError:
        return default


def pair(value, default):
    numbers = re.findall(r"-?\d+(?:\.\d+)?", str(value))
    return (float(numbers[0]), float(numbers[1])) if len(numbers) >= 2 else default


def monitor_width():
    """Настройки в пикселях монитора: от его ширины зависит, насколько велико поле на экране."""
    try:
        monitors = json.loads(hypr.hyprctl("monitors", "-j"))
        monitor = next((item for item in monitors if item.get("focused")), monitors[0])
        return monitor["width"] / (monitor.get("scale") or 1)
    except (ValueError, LookupError, TypeError):
        return 1920


def find_font(family):
    try:
        done = subprocess.run(["fc-match", "-f", "%{file}", family], capture_output=True, text=True, timeout=3)
        return done.stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        return None


def load_font(path, size):
    try:
        return ImageFont.truetype(path, max(4, round(size)))
    except (OSError, TypeError, ValueError):
        return ImageFont.load_default()


def anchor(block, width, height, scale):
    """Левый верхний угол виджета. В hyprlock ось y направлена вверх."""
    x, y = pair(block.get("position", "0, 0"), (0.0, 0.0))
    x, y = x * scale, y * scale
    halign, valign = block.get("halign", "center"), block.get("valign", "center")
    left = {"left": x, "right": WIDTH - width + x}.get(halign, (WIDTH - width) / 2 + x)
    top = {"top": -y, "bottom": HEIGHT - height - y}.get(valign, (HEIGHT - height) / 2 - y)
    return round(left), round(top)


def label_text(value):
    """Подпись hyprlock: обычный текст, $TIME или команда date."""
    command = re.fullmatch(r'cmd\[[^\]]*\]\s*date \+"([^"]+)"', value)
    if command:
        done = subprocess.run(["date", f"+{command.group(1)}"], capture_output=True, text=True, timeout=3)
        return done.stdout.strip()
    if value.startswith("cmd["):
        return ""
    if "$TIME" in value:
        done = subprocess.run(["date", "+%H:%M"], capture_output=True, text=True, timeout=3)
        value = value.replace("$TIME12", done.stdout.strip()).replace("$TIME", done.stdout.strip())
    return re.sub(r"<[^>]+>", "", value)


def draw_background(block, names):
    fill = color(block.get("color", ""), names, FALLBACK_COLOR)
    canvas = Image.new("RGBA", (WIDTH, HEIGHT), fill[:3] + (255,))
    path = Path(block.get("path", "").replace("$HOME", str(HOME))).expanduser()
    try:
        with Image.open(path) as image:
            image = image.convert("RGB")
    except (OSError, ValueError):
        return canvas
    ratio = max(WIDTH / image.width, HEIGHT / image.height)
    image = image.resize((round(image.width * ratio), round(image.height * ratio)))
    left, top = (image.width - WIDTH) // 2, (image.height - HEIGHT) // 2
    image = image.crop((left, top, left + WIDTH, top + HEIGHT))
    passes, size = number(block.get("blur_passes", 0)), number(block.get("blur_size", 8), 8)
    if passes > 0:
        image = image.filter(ImageFilter.GaussianBlur(passes * size * 0.75 * WIDTH / 1920))
    image = ImageEnhance.Brightness(image).enhance(number(block.get("brightness", 1), 1))
    return image.convert("RGBA")


def draw_input(canvas, block, names, scale):
    if block.get("fade_on_empty", "true") == "true":
        return
    width, height = pair(block.get("size", "400, 90"), (400.0, 90.0))
    width, height = round(width * scale), round(height * scale)
    left, top = anchor(block, width, height, scale)
    rounding = number(block.get("rounding", -1), -1)
    radius = height // 2 if rounding < 0 else round(rounding * scale)
    outline = max(0, round(number(block.get("outline_thickness", 4), 4) * scale))
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    box = (left, top, left + width - 1, top + height - 1)
    draw.rounded_rectangle(box, radius, fill=color(block.get("inner_color", ""), names, (20, 20, 20, 255)),
                           outline=color(block.get("outer_color", ""), names) if outline else None,
                           width=outline)
    text = re.sub(r"<[^>]+>", "", block.get("placeholder_text", ""))
    font_path = find_font(block.get("font_family", "monospace"))
    size = height / 2
    font = load_font(font_path, size)
    while size > 6 and draw.textlength(text, font=font) > width - 2 * outline - 8:
        size -= 1
        font = load_font(font_path, size)
    draw.text((left + width / 2, top + height / 2), text, font=font, anchor="mm",
              fill=color(block.get("font_color", ""), names)[:3] + (150,))
    canvas.alpha_composite(layer)


def draw_image(canvas, block, scale):
    try:
        with Image.open(Path(block.get("path", "")).expanduser()) as image:
            image = image.convert("RGBA")
    except (OSError, ValueError):
        return
    height = max(1, round(number(block.get("size", 150), 150) * scale))
    image = image.resize((max(1, round(image.width * height / image.height)), height))
    if number(block.get("rounding", -1), -1) < 0:
        mask = Image.new("L", image.size, 0)
        ImageDraw.Draw(mask).ellipse((0, 0, image.width - 1, image.height - 1), fill=255)
        image.putalpha(Image.composite(image.getchannel("A"), mask, mask))
    canvas.alpha_composite(image, anchor(block, image.width, image.height, scale))


def draw_label(canvas, block, names, scale):
    text = label_text(block.get("text", ""))
    if not text:
        return
    font = load_font(find_font(block.get("font_family", "monospace")), number(block.get("font_size", 16), 16) * scale)
    draw = ImageDraw.Draw(canvas)
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    x, y = anchor(block, right - left, bottom - top, scale)
    draw.text((x - left, y - top), text, font=font, fill=color(block.get("color", ""), names))


def render(target=OUTPUT, width=None):
    """Рисует экран блокировки в target, файл заменяется целиком: его может читать терминал."""
    text = read_conf(screens.HYPRLOCK)
    names = variables(text)
    text = re.sub(r"\$[A-Za-z_]\w*", lambda m: names.get(m.group(0), m.group(0)), text)
    scale = WIDTH / (width or monitor_width())
    background = next(iter(sections(text, "background")), {})
    canvas = draw_background(background, names)
    for block in sections(text, "input-field"):
        draw_input(canvas, block, names, scale)
    for block in sections(text, "image"):
        draw_image(canvas, block, scale)
    for block in sections(text, "label"):
        draw_label(canvas, block, names, scale)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f"{target.stem}.{threading.get_ident()}.png")
    canvas.convert("RGB").save(temporary)
    temporary.replace(target)
    return target
