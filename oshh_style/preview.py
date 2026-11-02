"""Картинка в терминале: протокол kitty (kitty, ghostty, wezterm), sixel (foot),
в остальных терминалах — полублоки."""
import base64
import fcntl
import hashlib
import os
import struct
import sys
import termios
import time
from pathlib import Path

from PIL import Image
from rich.style import Style
from rich.text import Text
from textual.widget import Widget

from . import paths

THUMBS = Path.home() / ".cache/oshh/style/thumbs"
IMAGE_ID = 0x4f5348
PLACEHOLDER = "\U0010eeee"
ROW_MARKS = [chr(code) for code in (
    0x305, 0x30d, 0x30e, 0x310, 0x312, 0x33d, 0x33e, 0x33f, 0x346, 0x34a, 0x34b, 0x34c, 0x350, 0x351, 0x352, 0x357,
    0x35b, 0x363, 0x364, 0x365, 0x366, 0x367, 0x368, 0x369, 0x36a, 0x36b, 0x36c, 0x36d, 0x36e, 0x36f, 0x483, 0x484,
    0x485, 0x486, 0x487, 0x592, 0x593, 0x594, 0x595, 0x597, 0x598, 0x599, 0x59c, 0x59d, 0x59e, 0x59f, 0x5a0, 0x5a1)]


class Stale(Exception):
    """Кадр устарел (пользователь уже выбрал другие обои) — кодирование прервано."""


def image_protocol():
    forced = os.environ.get("OSHH_PREVIEW_PROTOCOL")
    if forced in ("kitty", "sixel", "blocks"):
        return forced
    terminal = paths.terminal_name()
    if (terminal in ("kitty", "ghostty", "wezterm")
            or os.environ.get("KITTY_WINDOW_ID")
            or os.environ.get("GHOSTTY_RESOURCES_DIR")
            or os.environ.get("WEZTERM_EXECUTABLE")):
        return "kitty"
    if terminal == "foot" or os.environ.get("TERM", "").startswith("foot"):
        return "sixel"  # foot: sixel включён по умолчанию
    return "blocks"


def cell_pixels():
    """Размер ячейки терминала в пикселях (TIOCGWINSZ), по умолчанию 10×20."""
    try:
        packed = fcntl.ioctl(sys.stdout.fileno(), termios.TIOCGWINSZ, struct.pack("HHHH", 0, 0, 0, 0))
        rows, cols, xpix, ypix = struct.unpack("HHHH", packed)
        if cols and rows and xpix and ypix:
            return max(1, xpix // cols), max(1, ypix // rows)
    except (OSError, ValueError):
        pass
    return 10, 20


def sixel_encode(image, colors=48, stale=None):
    """Картинка → sixel-поток. Больше ~64 цветов foot рисовать отказывается
    («maximum image dimensions exceeded» на каждый кадр); 48 — компромисс между
    качеством и объёмом данных (сам поток долго разбирается терминалом посимвольно,
    чем он крупнее — тем дольше видимая задержка перед отрисовкой).
    stale: вызывается между строчными полосами; если возвращает True — кодирование
    бросает Stale (кадр уже не нужен, например пользователь успел выбрать другие обои)."""
    quantized = image.quantize(colors, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    width, height = quantized.size
    raw = quantized.tobytes()  # 1 байт-индекс на пиксель, построчно — быстрее, чем
    # поэлементный доступ через PIL load()/PyAccess на каждый пиксель
    used = sorted(set(raw))
    out = [f"\x1bP1;1q\"1;1;{width};{height}"]
    palette = quantized.getpalette()
    for index in used:
        red, green, blue = palette[index * 3: index * 3 + 3]
        out.append(f"#{index};2;{red * 100 // 255};{green * 100 // 255};{blue * 100 // 255}")
    for top in range(0, height, 6):
        if stale is not None and stale():
            raise Stale()
        rows_here = min(6, height - top)
        row_slices = [raw[(top + r) * width:(top + r) * width + width] for r in range(rows_here)]
        # bits_of заводим лениво — только для цветов, реально встретившихся в ЭТОЙ полосе,
        # а не для всех used (на фото в одной полосе обычно горстка цветов из 48 общих)
        bits_of = {}
        for column, values in enumerate(zip(*row_slices)):
            for row, index in enumerate(values):
                arr = bits_of.get(index)
                if arr is None:
                    arr = bits_of[index] = bytearray(width)
                arr[column] |= 1 << row
        out.append("-")
        first_color = True
        for index, bits in bits_of.items():
            if not first_color:
                out.append("$")  # возврат курсора к началу строки: без него следующий
                # цвет рисуется ПОСЛЕ предыдущего, курсор уезжает за пределы кадра —
                # терминал видит бесконечно растущую ширину и сыплет truncating-варнингами
            first_color = False
            out.append(f"#{index}")
            previous, count = None, 0
            for bits_value in bits:
                char = chr(0x3F + bits_value)
                if char == previous:
                    count += 1
                    continue
                if previous is not None:
                    out.append(f"!{count}{previous}")
                previous, count = char, 1
            out.append(f"!{count}{previous}")
    return "".join(out) + "\x1b\\"


SIXEL_CACHE = {}
SIXEL_LIMIT = 720  # потолок по большей стороне: баланс между чёткостью и объёмом
# sixel-данных — foot разбирает поток посимвольно, крупный кадр заметно тормозит отрисовку
# даже если наш Python-кодер отработал быстро


def _sixel_of(thumb, cols, rows, stale=None):
    """Sixel-поток с кэшем: один и тот же кадр не кодируем дважды."""
    key = (str(thumb), cols, rows)
    if key in SIXEL_CACHE:
        return SIXEL_CACHE[key]
    cell_width, cell_height = cell_pixels()
    width, height = cols * cell_width, rows * cell_height
    if max(width, height) > SIXEL_LIMIT:
        scale = SIXEL_LIMIT / max(width, height)
        width, height = round(width * scale), round(height * scale)
    started = time.monotonic()
    with Image.open(thumb) as image:
        frame = image.convert("RGB").resize((width, height))
    data = sixel_encode(frame, stale=stale)
    paths.log_line(f"sixel: {width}x{height} за {time.monotonic() - started:.2f} с, {len(data)} байт")
    if len(SIXEL_CACHE) > 8:
        SIXEL_CACHE.clear()
    SIXEL_CACHE[key] = data
    return data


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
        self.protocol = image_protocol()
        self._loaded_key = None  # (path, cols, rows) последнего отрисованного кадра
        self._gen = 0  # счётчик запросов: устаревший кодер прерывает себя сам
        self._debounce = None  # таймер, откладывающий запуск кодера при частых show()/resize
        self._drawn_region = None  # (x, y, cols, rows) последнего нарисованного sixel-кадра —
        # нужен, чтобы стереть его вручную: терминал сам не чистит пиксели при уходе виджета

    def show(self, path, ready=False):
        """ready: файл уже готовый PNG нужного размера, копия в кэше не нужна."""
        self.path = Path(path) if path else None
        self.ready = ready
        if path is None:
            self.pixels = None
            self._loaded_key = None
            if self.protocol == "sixel":
                self._clear_sixel()
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
        key = (str(self.path), self.cols, self.rows)
        if key == self._loaded_key:
            # тот же кадр того же размера уже нарисован — повторная перезагрузка (эхо
            # resize-события после прямой записи sixel в поток терминала) только грузит CPU
            return
        self._loaded_key = key
        self._gen += 1
        gen = self._gen
        if self._debounce is not None:
            self._debounce.stop()
        # при быстрой навигации по списку (стрелками) show() вызывается на каждый шаг —
        # ждём короткую паузу, иначе на каждую промежуточную обоину заводится воркер
        self._debounce = self.set_timer(0.12, lambda: self._start_load(gen))

    def _start_load(self, gen):
        if gen != self._gen:
            return
        self.run_worker(lambda: self.load(gen), thread=True, exclusive=True, group="preview")

    def load(self, gen):
        paths.log_line(f"preview: protocol={self.protocol}, TERM={os.environ.get('TERM', '')}")
        path = self.path
        if path is None or not path.exists():
            self.app.call_from_thread(self.refresh)
            return
        try:
            thumb = path if self.ready else thumbnail(path)
        except OSError:
            self.app.call_from_thread(self.refresh)
            return
        if gen != self._gen:
            return  # пока готовили миниатюру, пользователь выбрал другой файл
        if self.protocol == "kitty":
            self.app.call_from_thread(self.place, thumb)
            return
        if self.protocol == "sixel":
            try:
                data = _sixel_of(thumb, self.cols, self.rows, stale=lambda: gen != self._gen)
            except Stale:
                return  # кадр устарел — молча бросаем, новый воркер уже в пути
            except Exception as error:  # битый кадр или постер — показываем «нет предпросмотра», не падаем
                paths.log_error(error)
                self.app.call_from_thread(self.refresh)
                return
            if gen != self._gen:
                return
            self.app.call_from_thread(self.place_sixel, data)
            return
        try:
            with Image.open(thumb) as image:
                pixels = image.convert("RGB").resize((self.cols, self.rows * 2))
        except Exception as error:  # битый кадр или постер — показываем «нет предпросмотра», не падаем
            paths.log_error(error)
            self.app.call_from_thread(self.refresh)
            return
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

    def place_sixel(self, data):
        driver = self.app._driver
        if driver is None:
            return
        region = self.region
        key = (region.x, region.y, self.cols, self.rows)
        if self._drawn_region is not None and self._drawn_region != key:
            # виджет сместился/изменил размер с прошлой отрисовки — старые пиксели
            # на прежнем месте никто кроме нас не сотрёт, иначе они "бликуют" в чужом месте
            self._clear_sixel()
        driver.write(f"\x1b[{region.y + 1};{region.x + 1}H")
        driver.write(data)
        self._drawn_region = key
        if self.pixels is not True:  # перерисовывать пустые ячейки под sixel только один раз
            self.pixels = True
            self.refresh()

    def _clear_sixel(self):
        """Стирает ранее нарисованный sixel-кадр прямой записью пробелов поверх старого
        места. Нужно и при уходе со страницы (on_unmount), и перед отрисовкой на новом
        месте — терминал не знает, что картинка больше не актуальна, и хранит её пиксели
        до тех пор, пока что-то явно не перепишет эти же ячейки."""
        driver = self.app._driver
        if driver is None or self._drawn_region is None:
            return
        x, y, cols, rows = self._drawn_region
        blank = " " * cols
        for row in range(rows):
            driver.write(f"\x1b[{y + row + 1};{x + 1}H{blank}")
        self._drawn_region = None

    def on_unmount(self):
        driver = self.app._driver
        if self.protocol == "kitty" and driver:
            driver.write(f"\x1b_Ga=d,d=I,i={IMAGE_ID},q=2\x1b\\")
        elif self.protocol == "sixel":
            self._clear_sixel()

    def render(self):
        if self.pixels is None:
            return Text("\n  нет предпросмотра", style="dim")
        text = Text(no_wrap=True, end="")
        if self.pixels is True:
            if self.protocol == "sixel":  # sixel рисует сам, ячейки оставляем пустыми
                for row in range(self.rows):
                    text.append(" " * self.cols)
                    if row < self.rows - 1:
                        text.append("\n")
                return text
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
