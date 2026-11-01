"""Картинка в терминале: протокол kitty, в остальных терминалах — полублоки."""
import base64
import hashlib
import os
from pathlib import Path

from PIL import Image
from rich.style import Style
from rich.text import Text
from textual.widget import Widget

THUMBS = Path.home() / ".cache/oshh/style/thumbs"
IMAGE_ID = 0x4f5348
PLACEHOLDER = "\U0010eeee"
ROW_MARKS = [chr(code) for code in (
    0x305, 0x30d, 0x30e, 0x310, 0x312, 0x33d, 0x33e, 0x33f, 0x346, 0x34a, 0x34b, 0x34c, 0x350, 0x351, 0x352, 0x357,
    0x35b, 0x363, 0x364, 0x365, 0x366, 0x367, 0x368, 0x369, 0x36a, 0x36b, 0x36c, 0x36d, 0x36e, 0x36f, 0x483, 0x484,
    0x485, 0x486, 0x487, 0x592, 0x593, 0x594, 0x595, 0x597, 0x598, 0x599, 0x59c, 0x59d, 0x59e, 0x59f, 0x5a0, 0x5a1)]


def thumbnail(path, width=960):
    """Уменьшенная копия в PNG: kitty принимает файл только в этом формате."""
    stat = path.stat()
    key = hashlib.sha1(f"{path}:{stat.st_mtime_ns}:{width}".encode()).hexdigest()
    target = THUMBS / f"{key}.png"
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(path) as image:
            image = image.convert("RGB")
            image.thumbnail((width, width))
            image.save(target)
    return target


class Preview(Widget):
    """Показывает файл в размере виджета с сохранением пропорций 16:9."""

    DEFAULT_CSS = "Preview { height: auto; width: 1fr; }"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.path = None
        self.cols = 0
        self.rows = 0
        self.pixels = None
        self.ready = False
        self.kitty = bool(os.environ.get("KITTY_WINDOW_ID"))

    def show(self, path, ready=False):
        """ready: файл уже готовый PNG нужного размера, копия в кэше не нужна."""
        self.path = Path(path) if path else None
        self.ready = ready
        if path is None:
            self.pixels = None
        if self.is_mounted:
            self.refresh_image()

    def on_resize(self):
        self.refresh_image()

    def get_content_height(self, container, viewport, width):
        return max(4, min(46, round(width * 9 / 16 / 2)))

    def refresh_image(self):
        self.cols = self.size.width
        self.rows = min(len(ROW_MARKS), self.size.height)
        if not self.cols or not self.rows:
            return
        self.run_worker(self.load, thread=True, exclusive=True, group="preview")

    def load(self):
        path = self.path
        if path is None or not path.exists():
            self.app.call_from_thread(self.refresh)
            return
        try:
            thumb = path if self.ready else thumbnail(path)
        except OSError:
            self.app.call_from_thread(self.refresh)
            return
        if self.kitty:
            self.app.call_from_thread(self.place, thumb)
        else:
            with Image.open(thumb) as image:
                pixels = image.convert("RGB").resize((self.cols, self.rows * 2))
            self.pixels = pixels
            self.app.call_from_thread(self.refresh)

    def place(self, thumb):
        driver = self.app._driver
        if driver is None:
            return
        encoded = base64.b64encode(str(thumb).encode()).decode()
        driver.write(f"\x1b_Ga=d,d=I,i={IMAGE_ID},q=2\x1b\\")
        driver.write(f"\x1b_Ga=T,U=1,t=f,f=100,i={IMAGE_ID},c={self.cols},r={self.rows},q=2;{encoded}\x1b\\")
        self.pixels = True
        self.refresh()

    def on_unmount(self):
        driver = self.app._driver
        if self.kitty and driver:
            driver.write(f"\x1b_Ga=d,d=I,i={IMAGE_ID},q=2\x1b\\")

    def render(self):
        if self.pixels is None:
            return Text("\n  нет предпросмотра", style="dim")
        text = Text(no_wrap=True, end="")
        if self.pixels is True:
            style = Style(color=f"#{IMAGE_ID:06x}")
            for row in range(self.rows):
                text.append(PLACEHOLDER + ROW_MARKS[row] + ROW_MARKS[0] + PLACEHOLDER * (self.cols - 1), style)
                if row < self.rows - 1:
                    text.append("\n")
            return text
        pixels = self.pixels.load()
        for row in range(self.rows):
            for col in range(self.cols):
                top = "#%02x%02x%02x" % pixels[col, row * 2]
                bottom = "#%02x%02x%02x" % pixels[col, row * 2 + 1]
                text.append("▀", Style(color=top, bgcolor=bottom))
            if row < self.rows - 1:
                text.append("\n")
        return text
