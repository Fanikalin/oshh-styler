"""Темы и обои. Обои принадлежат теме: выбор запоминается для каждой темы отдельно."""
import hashlib
import json
import os
import re
import shutil
import subprocess
import tomllib
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from . import monitors, palette, paths

HOME = paths.HOME
OMARCHY = paths.omarchy_dir()
USER_THEMES = HOME / ".config/omarchy/themes"
CURRENT = paths.current_dir()
USER_BACKGROUNDS = HOME / ".config/omarchy/backgrounds"
CACHE = HOME / ".cache/oshh/style"
WALLPAPER_CONF = HOME / ".config/oshh/wallpaper.conf"
CATALOG = Path(__file__).resolve().parent.parent / "catalog.json"

IMAGE_TYPES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
VIDEO_TYPES = {".mp4", ".webm", ".mkv", ".mov", ".gif"}
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) OSHH-Style"


def pretty(name):
    return " ".join(part.capitalize() for part in name.split("-"))


def luminance(color):
    value = color.lstrip("#")
    r, g, b = (int(value[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


@dataclass
class Theme:
    name: str
    path: Path
    colors: dict

    @property
    def title(self):
        return pretty(self.name)

    @property
    def dark(self):
        return luminance(self.colors.get("background", "#000000")) < 0.5

    @property
    def preview(self):
        return self.path / "preview.png"

    @property
    def palette(self):
        keys = ["background", "foreground", "accent", *[f"color{i}" for i in range(1, 7)]]
        return [self.colors[key] for key in keys if key in self.colors]


def read_colors(path):
    try:
        data = tomllib.loads((path / "colors.toml").read_text())
    except (OSError, ValueError):
        return {}
    return {key: value for key, value in data.items() if isinstance(value, str) and value.startswith("#")}


def list_themes():
    """Тема пользователя накладывается на встроенную файл за файлом, как в omarchy-theme-set."""
    found = {}
    for root in (USER_THEMES, OMARCHY / "themes"):
        if root.is_dir():
            for path in root.iterdir():
                if path.is_dir():
                    found.setdefault(path.name, []).append(path)
    themes = []
    for name, paths in found.items():
        path = next((item for item in paths if (item / "colors.toml").exists()), paths[0])
        themes.append(Theme(name, next((item for item in paths if (item / "preview.png").exists()), path), read_colors(path)))
    return sorted(themes, key=lambda theme: (not theme.dark, theme.name))


def current_theme():
    try:
        return (CURRENT / "theme.name").read_text().strip()
    except OSError:
        return ""


def current_colors():
    return read_colors(CURRENT / "theme")


def set_theme(name):
    done = subprocess.run(["omarchy-theme-set", name], capture_output=True, text=True, timeout=120)
    return None if done.returncode == 0 else (done.stderr or done.stdout).strip()


@dataclass
class Wallpaper:
    path: Path
    own: bool
    item: dict = None
    origin: str = None

    @property
    def video(self):
        return self.path.suffix.lower() in VIDEO_TYPES

    @property
    def remote(self):
        """Клип из каталога, который ещё не скачан."""
        return self.item is not None and not self.path.exists()

    @property
    def title(self):
        if self.item:
            return self.item["title"]
        stem = re.sub(r"^\d+-", "", self.path.stem)
        return stem.replace("-", " ").replace("_", " ").capitalize()


def catalog_entry(path, items):
    match = re.fullmatch(r"pexels-(\w+)", path.stem)
    return next((item for item in items if match and item["id"] == match.group(1)), None)


def list_wallpapers(theme):
    """Обои темы: из комплекта, добавленные пользователем и клипы каталога, в том числе ещё не скачанные."""
    items = catalog()
    result = []
    for root, own in ((CURRENT / "theme/backgrounds", False), (USER_BACKGROUNDS / theme, True)):
        if root.is_dir():
            for path in sorted(root.iterdir()):
                if path.is_file() and path.suffix.lower() in IMAGE_TYPES | VIDEO_TYPES:
                    result.append(Wallpaper(path, own, catalog_entry(path, items)))
    present = {wallpaper.path for wallpaper in result}
    for item in items:
        path = catalog_file(theme, item)
        if path not in present and theme in item["themes"]:
            result.append(Wallpaper(path, True, item))
    return result


def other_wallpapers(theme):
    """Обои других тем и остальные клипы каталога, без того, что уже есть в списке темы."""
    shown = list_wallpapers(theme)
    names = {wallpaper.path.name for wallpaper in shown}
    items = catalog()
    result = []
    roots = [(path.parent.name, path) for base in (OMARCHY / "themes", USER_THEMES) for path in base.glob("*/backgrounds")]
    roots += [(path.name, path) for path in USER_BACKGROUNDS.glob("*")]
    for origin, root in sorted(roots):
        if origin == theme or not root.is_dir():
            continue
        for path in sorted(root.iterdir()):
            if path.is_file() and path.suffix.lower() in IMAGE_TYPES | VIDEO_TYPES and path.name not in names:
                names.add(path.name)
                result.append(Wallpaper(path, False, catalog_entry(path, items), origin))
    for item in items:
        path = catalog_file(theme, item)
        if path.name not in names:
            result.append(Wallpaper(path, True, item, item["themes"][0]))
    return result


def read_palettes():
    try:
        return json.loads((CACHE / "palettes.json").read_text())
    except (OSError, ValueError):
        return {}


def wallpaper_palette(wallpaper, known):
    """Главные цвета считаем один раз: для клипа нужен его постер, для видео кадр."""
    key = wallpaper.item["id"] if wallpaper.item else str(wallpaper.path)
    if key not in known:
        try:
            known[key] = palette.dominant(still_of(wallpaper))
        except (OSError, ValueError, TypeError, AttributeError):
            return None
    return known[key]


def by_theme_colors(wallpapers, progress=None):
    """Сверху самые близкие к гамме текущей темы. Без постера или кадра обои уходят в конец."""
    known = read_palettes()
    colors, background = palette.theme_palette(current_colors())
    done = 0

    def rank(wallpaper):
        nonlocal done
        colored = wallpaper_palette(wallpaper, known)
        done += 1
        if progress:
            progress(done, len(wallpapers))
        return palette.score(colored, colors, background) if colored else float("inf")

    with ThreadPoolExecutor(8) as pool:
        scores = list(pool.map(rank, wallpapers))
    CACHE.mkdir(parents=True, exist_ok=True)
    (CACHE / "palettes.json").write_text(json.dumps(known))
    return [wallpaper for _, wallpaper in sorted(zip(scores, wallpapers), key=lambda pair: pair[0])]


def adopt_wallpaper(theme, wallpaper):
    """Чужие обои становятся обоями темы: копия не зависит от другой темы. Клип скачивается отдельно."""
    target = USER_BACKGROUNDS / theme / wallpaper.path.name
    if target.exists():
        target = target.with_name(f"{wallpaper.origin}-{target.name}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(wallpaper.path, target)
    return target


def selected_wallpaper(theme):
    state = HOME / ".local/state/oshh/wallpaper"
    settings = wallpaper_settings()
    names = [state / f"{theme}@{settings['SCREEN']}"] if settings["SAME"] == "false" else []
    try:
        return Path(next((path for path in names if path.exists()), state / theme).read_text().strip())
    except OSError:
        link = CURRENT / "background"
        return Path(os.readlink(link)) if link.is_symlink() else None


def apply_wallpaper(path=None):
    command = ["oshh-wallpaper-apply"] + ([str(path)] if path else [])
    done = subprocess.run(command, capture_output=True, text=True, timeout=30)
    return None if done.returncode == 0 else (done.stderr or done.stdout).strip()


def add_wallpaper(theme, source):
    source = Path(source).expanduser()
    if not source.is_file():
        return None, "Файл не найден"
    if source.suffix.lower() not in IMAGE_TYPES | VIDEO_TYPES:
        return None, "Подходят картинки и видео: " + ", ".join(sorted(IMAGE_TYPES | VIDEO_TYPES))
    target = USER_BACKGROUNDS / theme / source.name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return target, None


def pick_files(title, types, multiple=False):
    """Окно выбора файлов. Отмена даёт пустой список, без zenity — OSError."""
    patterns = " ".join(f"*{suffix} *{suffix.upper()}" for suffix in sorted(types))
    start = subprocess.run(["xdg-user-dir", "PICTURES"], capture_output=True, text=True).stdout.strip() or str(HOME)
    command = ["zenity", "--file-selection", f"--title={title}", f"--filename={start}/",
               f"--file-filter=Подходящие файлы | {patterns}", "--file-filter=Все файлы | *"]
    if multiple:
        command += ["--multiple", "--separator=\n"]
    done = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, text=True)
    return done.stdout.splitlines() if done.returncode == 0 else []


def wallpaper_settings():
    settings = {"MODE": "fill", "PAUSE": "hidden", "SPEED": "1.0", "SAME": "true", "SCREEN": ""}
    try:
        for line in WALLPAPER_CONF.read_text().splitlines():
            key, _, value = line.partition("=")
            if key in settings and value:
                settings[key] = value
    except OSError:
        pass
    return settings


def wallpaper_screens():
    return [item for item in monitors.listing() if not item["disabled"]]


def save_wallpaper_settings(settings):
    WALLPAPER_CONF.parent.mkdir(parents=True, exist_ok=True)
    WALLPAPER_CONF.write_text("".join(f"{key}={value}\n" for key, value in settings.items()))


def catalog(theme=None):
    try:
        items = json.loads(CATALOG.read_text())["items"]
    except (OSError, ValueError, KeyError):
        return []
    return [item for item in items if theme is None or theme in item["themes"]]


def catalog_file(theme, item):
    return USER_BACKGROUNDS / theme / f"pexels-{item['id']}.mp4"


def fetch(url, target, progress=None, expected=None):
    """Скачивает через временный файл: оборванная загрузка не попадёт в обои."""
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    received = 0
    try:
        with urllib.request.urlopen(request, timeout=30) as response, partial.open("wb") as output:
            total = expected or int(response.headers.get("Content-Length") or 0)
            while chunk := response.read(1 << 18):
                output.write(chunk)
                received += len(chunk)
                if progress:
                    progress(received, total)
        if expected and received != expected:
            raise OSError(f"получено {received} байт из {expected}")
        partial.replace(target)
    finally:
        partial.unlink(missing_ok=True)


def poster_for(item):
    target = CACHE / "posters" / f"{item['id']}.jpg"
    if not target.exists():
        fetch(item["poster"], target)
    return target


def video_frame(path):
    """Кадр из видео для предпросмотра и экрана блокировки."""
    key = hashlib.sha1(str(path).encode()).hexdigest()
    target = HOME / ".cache/oshh/wallpaper" / f"{key}.jpg"
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "1", "-i", str(path), "-frames:v", "1", str(target)],
                       capture_output=True, timeout=30)
    return target if target.exists() else None


def still_of(wallpaper):
    if wallpaper.remote:
        return poster_for(wallpaper.item)
    return video_frame(wallpaper.path) if wallpaper.video else wallpaper.path


def size_text(size):
    return f"{size / 1_000_000:.0f} МБ" if size >= 1_000_000 else f"{size / 1000:.0f} КБ"
