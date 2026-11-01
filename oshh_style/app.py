"""Приложение «Оформление»: дерево групп слева, страница настроек справа."""
from pathlib import Path

from rich.cells import cell_len
from rich.style import Style
from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.message import Message
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.theme import Theme as TextualTheme
from textual.widget import Widget
from textual.widgets import Footer, Input, OptionList, Static, Tree
from textual.widgets.option_list import Option

from . import hypr, looks, monitors, paths, scenes, settings, setup
from .preview import Preview

BAR_WIDTH = 18


def mix(first, second, amount):
    a = [int(first[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(second[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(round(x * (1 - amount) + y * amount) for x, y in zip(a, b))


def textual_theme(colors):
    """Цвета приложения берутся из активной темы системы, как у всего рабочего стола."""
    background = colors.get("background", "#101014")
    foreground = colors.get("foreground", "#e0e0e0")
    accent = colors.get("accent", "#7aa2f7")
    return TextualTheme(
        name="oshh", primary=accent, secondary=colors.get("color4", accent), accent=accent, foreground=foreground,
        background=background, surface=mix(background, foreground, 0.06), panel=mix(background, foreground, 0.12),
        success=colors.get("color2", accent), warning=colors.get("color3", accent), error=colors.get("color1", accent),
        dark=looks.luminance(background) < 0.5)


class TextModal(ModalScreen):
    BINDINGS = [Binding("escape", "dismiss(None)", "Отмена")]

    def __init__(self, title, value, hint=""):
        super().__init__()
        self.title_text, self.value, self.hint = title, value, hint

    def compose(self) -> ComposeResult:
        with Vertical(id="modal"):
            yield Static(self.title_text, classes="modal-title")
            yield Input(value=str(self.value if self.value is not None else ""), id="modal-input")
            yield Static(self.hint or "Enter — сохранить, Esc — отмена", classes="dim")

    @on(Input.Submitted)
    def submitted(self, event):
        self.dismiss(event.value)


class ChoiceModal(ModalScreen):
    BINDINGS = [Binding("escape", "dismiss(None)", "Отмена")]

    def __init__(self, title, choices, current, on_preview=None):
        super().__init__()
        self.title_text, self.choices, self.current = title, choices, current
        self.on_preview = on_preview

    def compose(self) -> ComposeResult:
        with Vertical(id="modal"):
            yield Static(self.title_text, classes="modal-title")
            yield OptionList(*[Option(label) for _, label in self.choices], id="modal-list")

    def on_mount(self):
        values = [value for value, _ in self.choices]
        if self.current in values:
            self.query_one(OptionList).highlighted = values.index(self.current)

    @on(OptionList.OptionHighlighted)
    def highlighted(self, event):
        if self.on_preview:
            self.on_preview(self.choices[event.option_index][0])

    @on(OptionList.OptionSelected)
    def selected(self, event):
        self.dismiss(self.choices[event.option_index][0])


class KeepModal(ModalScreen):
    """После смены режима экрана: без подтверждения настройка откатывается сама."""

    BINDINGS = [Binding("enter", "dismiss(True)", "Оставить"), Binding("escape", "dismiss(False)", "Вернуть")]
    SECONDS = 15

    def compose(self) -> ComposeResult:
        with Vertical(id="modal"):
            yield Static("Оставить эту настройку?", classes="modal-title")
            yield Static(id="keep-text")

    def on_mount(self):
        self.left = self.SECONDS
        self.show_left()
        self.set_interval(1, self.tick)

    def show_left(self):
        self.query_one("#keep-text", Static).update(f"Enter — оставить, Esc — вернуть.\nСамо вернётся через {self.left} с.")

    def tick(self):
        self.left -= 1
        if self.left <= 0:
            self.dismiss(False)
        else:
            self.show_left()


class Row(Widget, can_focus=True):
    """Строка настройки. ←→ меняют значение, Enter открывает ввод или запускает действие, r — сброс."""

    DEFAULT_CSS = "Row { height: 1; width: 1fr; }"

    class Changed(Message):
        pass

    class Focused(Message):
        def __init__(self, setting, overrides=None):
            super().__init__()
            self.setting, self.overrides = setting, overrides

    def __init__(self, setting):
        super().__init__()
        self.setting = setting

    def color(self, name):
        return self.app.theme_variables.get(name, "white")

    def bar(self, value):
        s = self.setting
        share = (float(value) - s.lo) / ((s.hi - s.lo) or 1)
        filled = round(max(0.0, min(1.0, share)) * (BAR_WIDTH - 1))
        return "━" * filled, "●", "─" * (BAR_WIDTH - 1 - filled)

    def number(self, value):
        return f"{value:.2f}".rstrip("0").rstrip(".") if isinstance(value, float) else str(value)

    def value_parts(self):
        s = self.setting
        value = s.get()
        accent, dim = self.color("primary"), self.color("text-muted")
        if s.kind == "action":
            return [(f"{s.verb} ⏎", accent)]
        if value is None:
            return [("нет данных", dim)]
        if s.kind == "bool":
            return [("● вкл" if value else "○ выкл", accent if value else dim)]
        if s.kind == "choice":
            many = len(s.choices) > 6
            return [("◂ ", dim), (s.choice_label(value), accent), (" ⏎" if many else " ▸", dim)]
        if s.kind == "color":
            return [("██ ", value if value.startswith("#") else dim), (value, accent)]
        if s.kind in ("int", "float") and s.lo is not None and s.hi is not None:
            left, knob, right = self.bar(value)
            shown = s.marks[value] if s.marks else self.number(value)
            return [(left, accent), (knob, accent), (right, dim), (f" {shown:>6}", accent)]
        return [(str(value)[:48] or "—", accent)]

    def render(self):
        s = self.setting
        parts = self.value_parts()
        used = sum(cell_len(part) for part, _ in parts)
        mark = " •" if s.modified() else ""
        label = s.label + mark
        room = max(1, self.size.width - used - 2)
        if cell_len(label) > room:
            label = label[:max(1, room - 1)] + "…"
        text = Text(" " + label, no_wrap=True, end="")
        text.append(" " * max(1, room - cell_len(label)))
        for part, color in parts:
            try:
                text.append(part, Style(color=color))
            except Exception:
                text.append(part)
        return text

    def on_focus(self):
        self.app.set_help(self.setting.hint)
        self.post_message(self.Focused(self.setting))

    def try_value(self, value):
        """Предпросмотр значения в списке выбора: сама настройка не меняется."""
        self.post_message(self.Focused(self.setting, {self.setting.try_key: value}))

    def apply(self, value):
        guard = self.setting.guard
        before = self.setting.get() if guard else None
        try:
            error = self.setting.set(value)
        except Exception as problem:
            error = str(problem)
        if error:
            self.app.notify(str(error), severity="warning", timeout=6)
        self.refresh()
        self.post_message(self.Changed())
        if guard and not error and value != before:
            self.app.push_screen(KeepModal(), self.settled)
        return error

    def settled(self, keep):
        self.setting.guard(keep)
        self.app.after_action()

    def shift(self, direction, big=False):
        s = self.setting
        value = s.get()
        if s.kind == "bool":
            self.apply(not value)
        elif s.kind == "choice" and s.choices:
            values = [item for item, _ in s.choices]
            index = values.index(value) if value in values else 0
            self.apply(values[(index + direction) % len(values)])
        elif s.kind in ("int", "float") and value is not None:
            step = (s.step or 1) * (10 if big else 1)
            result = float(value) + direction * step
            if s.lo is not None:
                result = max(s.lo, result)
            if s.hi is not None:
                result = min(s.hi, result)
            self.apply(int(round(result)) if s.kind == "int" else round(result, 3))

    def pick(self):
        title, types = self.setting.picker
        try:
            paths = looks.pick_files(title, types)
        except OSError:
            self.app.call_from_thread(self.app.notify, "Не найден zenity: выбор файла недоступен", severity="warning")
            return
        if paths:
            self.app.call_from_thread(self.apply, paths[0])

    def activate(self):
        s = self.setting
        if s.picker:
            self.run_worker(self.pick, thread=True, group="pick")
            return
        if s.kind == "action" and s.confirm:
            self.app.push_screen(ChoiceModal(s.confirm, [(False, "Отмена"), (True, "Да, сбросить")], False), self.confirmed)
        elif s.kind == "action":
            self.run_action()
        elif s.kind == "bool":
            self.shift(1)
        elif s.kind == "choice":
            if len(s.choices) > 6:
                preview = self.try_value if s.try_key else None
                self.app.push_screen(ChoiceModal(s.label, s.choices, s.get(), preview), self.chosen)
            else:
                self.shift(1)
        else:
            self.app.push_screen(TextModal(s.label, s.get(), s.hint), self.typed)

    def run_action(self):
        self.apply(None)
        self.app.after_action()

    def confirmed(self, answer):
        if answer:
            self.run_action()

    def chosen(self, value):
        if value is not None:
            self.apply(value)
        else:
            self.post_message(self.Focused(self.setting))

    def typed(self, value):
        if value is None:
            return
        s = self.setting
        try:
            if s.kind == "int":
                value = int(float(value))
            elif s.kind == "float":
                value = float(value.replace(",", "."))
        except ValueError:
            self.app.notify("Нужно число", severity="warning")
            return
        self.apply(value)

    def on_key(self, event):
        key = event.key
        if key in ("left", "right", "shift+left", "shift+right"):
            self.shift(1 if key.endswith("right") else -1, key.startswith("shift"))
        elif key == "enter":
            self.activate()
        elif key == "up":
            self.screen.focus_previous()
        elif key == "down":
            self.screen.focus_next()
        elif key == "r":
            if self.setting.can_reset():
                self.setting.reset()
                self.app.after_action()
        else:
            return
        event.stop()

    def on_click(self, event):
        self.focus()
        s = self.setting
        if s.kind in ("int", "float") and s.lo is not None and s.hi is not None:
            start = self.size.width - BAR_WIDTH - 8 - max(0, max(map(len, s.marks or [""])) - 6)
            position = event.x - start
            if 0 <= position < BAR_WIDTH:
                result = s.lo + (s.hi - s.lo) * position / (BAR_WIDTH - 1)
                self.apply(int(round(result)) if s.kind == "int" else round(result, 2))
            return
        if s.kind == "action" and s.previewable:
            return
        self.activate()


class PickList(OptionList):
    """Клик только выделяет строку и показывает предпросмотр, применяет Enter."""

    async def _on_click(self, event):
        event.prevent_default()
        clicked = event.style.meta.get("option")
        if clicked is not None:
            self.highlighted = clicked


class NavTree(Tree):
    """Enter уводит фокус на страницу. Клик оставляет его здесь: иначе рамки панелей мигают."""

    def on_key(self, event):
        node = self.cursor_node
        if event.key == "enter" and node is not None and node.data:
            event.stop()
            event.prevent_default()
            if not self.app.run_single(node.data):
                self.app.enter_page()


class SettingsPage(VerticalScroll):
    can_focus = False

    def __init__(self, rows):
        super().__init__(classes="page")
        self.rows = rows

    def compose(self) -> ComposeResult:
        for row in self.rows:
            yield Row(row)


class RawList(Vertical):
    """Все параметры раздела Hyprland: то, что появится в новой версии, попадёт сюда само."""

    def __init__(self, store, category):
        super().__init__()
        self.store, self.category = store, category

    def compose(self) -> ComposeResult:
        yield Input(placeholder="Фильтр по имени и описанию", id="raw-filter")
        yield VerticalScroll(id="raw-rows")

    def on_mount(self):
        self.fill("")

    @on(Input.Changed)
    def filtered(self, event):
        self.fill(event.value)

    def fill(self, query):
        box = self.query_one("#raw-rows")
        box.remove_children()
        box.mount(*[Row(row) for row in settings.raw_rows(self.store, self.category, query)])


class ScenePage(Horizontal):
    """Настройки слева, справа предпросмотр: перерисовывается при выборе строки и при каждом изменении."""

    IMAGES = {"lock", "greeter", "wall", "desktop", "font", "cursor", "motion", "monitors"}
    NOTES = {
        "lock": "Примерный вид. Настоящий экран — в подразделе «Поведение», «Посмотреть настоящий экран».",
        "greeter": "Примерный вид. Настоящий экран — в подразделе «Вход», «Показать экран входа».",
        "desktop": "Макет рабочего стола по текущим значениям. Изменения применяются сразу и к настоящим окнам.",
        "wall": "Как обои лягут на экран.",
        "monitors": "Экраны в настоящем масштабе. Выбранный обведён.",
        "cursor": "Курсор меняется сразу, можно проверить и в этом окне.",
        "motion": "Сверху кадры по времени, снизу кривая: точки на ней — те же кадры. Выше линии «конец» — отскок.",
    }

    def __init__(self, rows, kind, left=None):
        super().__init__(classes="page")
        self.rows, self.kind, self.left = rows, kind, left
        self.overrides = None
        self.active = None
        self.rendering = self.pending = False

    def compose(self) -> ComposeResult:
        yield self.left or SettingsPage(self.rows)
        with Vertical(id="side"):
            if self.kind in self.IMAGES:
                yield Preview(id="preview")
                yield Static(self.NOTES.get(self.kind, ""), classes="dim")
            elif self.kind == "keys":
                yield Static("Проверка: зажмите клавишу и посмотрите на скорость повтора, переключите раскладку.", classes="dim")
                yield Input(placeholder="Попробуйте печатать здесь", id="try-keys")
            elif self.kind == "mouse":
                yield Static("Проверка: прокрутите список и подвигайте курсор. Настройки применяются сразу.", classes="dim")
                with VerticalScroll(id="try-scroll"):
                    yield Static("\n".join(f"Строка {number}" for number in range(1, 201)))
            elif self.kind == "files":
                with VerticalScroll(id="file-view"):
                    yield Static(id="file-text")
            yield Static(id="option-info", classes="dim")

    def on_mount(self):
        if self.rows:
            self.select(self.rows[0])
        self.redraw()

    def select(self, s):
        self.active = s
        self.show_info(s)
        if self.kind == "files":
            self.show_text()

    @on(Row.Focused)
    def focused(self, event):
        self.overrides = event.overrides if event.overrides is not None else event.setting.preview
        self.select(event.setting)
        self.redraw()

    @on(Row.Changed)
    def changed(self):
        self.overrides = None
        self.redraw()
        if self.kind == "files":
            self.show_text()

    def show_info(self, s):
        info = self.query_one("#option-info", Static)
        if s.option is None:
            info.update("")
            return
        lines = [s.option["name"], "", s.option["description"], "", f"По умолчанию: {s.option['default']}"]
        info.update(Text("\n".join(str(line) for line in lines)))

    def show_text(self):
        s = self.active
        self.run_worker(lambda: self.app.call_from_thread(self.place_text, s, s.text()), thread=True, exclusive=True, group="text")

    def place_text(self, s, text):
        if self.is_mounted and s is self.active:
            self.query_one("#file-text", Static).update(Text(text))

    def redraw(self):
        """Рисуем по одному кадру: пока идёт отрисовка, новые изменения ждут и попадают в следующий кадр."""
        if self.kind not in self.IMAGES:
            return
        self.pending = True
        if not self.rendering:
            self.start_render()

    def start_render(self):
        self.rendering, self.pending = True, False
        overrides = self.overrides
        if self.kind == "motion" and self.active:
            overrides = {"leaf": self.active.leaf, "title": self.active.title}
        self.run_worker(lambda: self.render_scene(overrides), thread=True, group="scene")

    def render_scene(self, overrides):
        try:
            target = scenes.render(self.kind, self.app.store, overrides)
        except (OSError, ValueError, KeyError, TypeError):
            target = None
        self.app.call_from_thread(self.place, target)

    def place(self, target):
        self.rendering = False
        if not self.is_mounted:
            return
        if target:
            self.query_one(Preview).show(target, True)
        if self.pending:
            self.start_render()


class ThemesPage(Horizontal):
    def __init__(self):
        super().__init__(classes="page")
        self.themes = looks.list_themes()

    def compose(self) -> ComposeResult:
        current = looks.current_theme()
        options = []
        for theme in self.themes:
            mark = "● " if theme.name == current else "  "
            shade = "◐" if theme.dark else "○"
            options.append(Option(f"{mark}{shade} {theme.title}"))
        yield PickList(*options, id="list")
        with Vertical(id="side"):
            yield Preview(id="preview")
            yield Static(id="palette")
            yield Static("Клик или стрелки — посмотреть, Enter — применить. ◐ тёмная, ○ светлая.", classes="dim")

    def on_mount(self):
        names = [theme.name for theme in self.themes]
        current = looks.current_theme()
        self.query_one(OptionList).highlighted = names.index(current) if current in names else 0

    @on(OptionList.OptionHighlighted)
    def highlighted(self, event):
        theme = self.themes[event.option_index]
        self.query_one(Preview).show(theme.preview)
        text = Text()
        for color in theme.palette:
            text.append("███", Style(color=color))
        self.query_one("#palette", Static).update(text)

    @on(OptionList.OptionSelected)
    def selected(self, event):
        theme = self.themes[event.option_index]
        self.app.set_help(f"Применяю тему {theme.title}…")
        self.app.run_worker(lambda: self.app.apply_theme(theme), thread=True)


class WallpapersPage(Horizontal):
    """Один список: обои темы, свои файлы и клипы каталога. Клипы скачиваются по одному: их лицензия запрещает раздачу."""

    ADD_LABEL = "+ Добавить свои файлы…"
    ACTION_HINTS = {
        "same": "Выключите, чтобы поставить на каждый экран свои обои.",
        "screen": "Enter — следующий экран. Выбранные обои лягут на него.",
        "other": "Обои других тем и каталога. Сверху самые близкие к цветам темы.",
    }

    def __init__(self):
        super().__init__(classes="page")
        self.theme = looks.current_theme()
        self.items = looks.list_wallpapers(self.theme)
        self.others = []
        self.loading = False
        self.screens = looks.wallpaper_screens()

    def compose(self) -> ComposeResult:
        yield PickList(id="list")
        with Vertical(id="side"):
            yield Preview(id="preview")
            yield Static(id="info")
            yield Static("Клик или стрелки — посмотреть, Enter — поставить. ○ справа — анимированные.", classes="dim")

    def on_mount(self):
        self.fill()

    def label(self, item, chosen, width):
        """Текст с меткой у правого края: ширину строки считаем сами, OptionList не умеет выравнивать."""
        left = f"{'●' if item.path.name == chosen else ' '} {item.title}"
        right = ""
        if item.remote:
            right = looks.size_text(item.item["size"])
        if item.video:
            right = f"{right} ○".strip()
        room = max(1, width - cell_len(left) - cell_len(right))
        return Text(left + " " * room + right, no_wrap=True)

    def same(self):
        return looks.wallpaper_settings()["SAME"] != "false"

    def target_screen(self):
        """Экран, для которого выбираем обои. Отключённый заменяем первым."""
        wanted = looks.wallpaper_settings()["SCREEN"]
        return next((item for item in self.screens if item["name"] == wanted), self.screens[0])

    def rows(self):
        """Строки списка: обои или имя действия."""
        heads = []
        if len(self.screens) > 1:
            heads = ["same"] if self.same() else ["same", "screen"]
        return heads + self.items + ["add", "other"] + self.others

    def action_label(self, name):
        if name == "same":
            return f"{'●' if self.same() else '○'} Обои на всех экранах одинаковые"
        if name == "screen":
            return f"  Экран: ◂ {monitors.title(self.target_screen())} ▸"
        if name == "add":
            return self.ADD_LABEL
        return f"{'…' if self.loading else '▾' if self.others else '▸'} Другие обои"

    def labels(self):
        chosen = looks.selected_wallpaper(self.theme)
        chosen = chosen.name if chosen else ""
        width = self.query_one(OptionList).content_size.width - 3
        return [self.action_label(row) if isinstance(row, str) else self.label(row, chosen, width) for row in self.rows()]

    def fill(self, keep=None):
        listing = self.query_one(OptionList)
        labels = self.labels()
        listing.clear_options()
        listing.add_options([Option(label) for label in labels])
        self.call_after_refresh(self.relabel)
        chosen = looks.selected_wallpaper(self.theme)
        target = keep or (chosen.name if chosen else None)
        rows = self.rows()
        names = [row if isinstance(row, str) else row.path.name for row in rows]
        listing.highlighted = names.index(target) if target in names else next(index for index, row in enumerate(rows) if row == "add" or not isinstance(row, str))

    def relabel(self):
        listing = self.query_one(OptionList)
        for index, label in enumerate(self.labels()):
            listing.replace_option_prompt_at_index(index, label)

    def on_resize(self):
        self.call_after_refresh(self.relabel)

    def toggle_others(self):
        if self.loading:
            return
        if self.others:
            self.others = []
            self.fill("other")
            return
        self.loading = True
        self.relabel()
        self.run_worker(self.load_others, thread=True, group="others")

    def load_others(self):
        def progress(done, total):
            self.app.call_from_thread(self.app.set_help, f"Подбираю по цветам темы: {done} из {total}")

        try:
            others = looks.by_theme_colors(looks.other_wallpapers(self.theme), progress)
        except Exception as error:  # падение подбора не должно ронять приложение
            paths.log_error(error)
            others = None
        self.app.call_from_thread(self.show_others, others)

    def show_others(self, others):
        self.app.set_help("")
        self.loading = False
        if others is None:
            self.app.notify("Не удалось собрать «Другие обои»; подробности в ~/.local/state/omarchy/styler.log",
                            severity="warning", timeout=8)
            self.fill("other")
            return
        self.others = others
        self.fill("other")

    def toggle_same(self):
        settings = looks.wallpaper_settings()
        settings["SAME"] = "true" if self.same() else "false"
        settings["SCREEN"] = self.target_screen()["name"]
        looks.save_wallpaper_settings(settings)
        self.fill("same")
        self.run_worker(looks.apply_wallpaper, thread=True, group="use")

    def next_screen(self):
        settings = looks.wallpaper_settings()
        names = [item["name"] for item in self.screens]
        settings["SCREEN"] = names[(names.index(self.target_screen()["name"]) + 1) % len(names)]
        looks.save_wallpaper_settings(settings)
        self.fill("screen")

    @on(OptionList.OptionHighlighted)
    def highlighted(self, event):
        item = self.rows()[event.option_index]
        if isinstance(item, str):
            self.query_one(Preview).show(None)
            self.query_one("#info", Static).update(self.ACTION_HINTS.get(item, ""))
            return
        info = f"Из темы {looks.pretty(item.origin)}. " if item.origin else ""
        if item.item:
            themes = ", ".join(looks.pretty(name) for name in item.item["themes"])
            info = f"{item.item['width']}×{item.item['height']}, {item.item['fps']} к/с. Подходит к: {themes}. Источник: Pexels."
        self.query_one("#info", Static).update(info)
        self.run_worker(lambda: self.show_still(item), thread=True, exclusive=True, group="still")

    def show_still(self, item):
        try:
            still = looks.still_of(item)
        except Exception as error:  # нет сети, нет ffmpeg и т.п. — просто без картинки
            paths.log_error(error)
            still = None
        self.app.call_from_thread(self.query_one(Preview).show, still)

    @on(OptionList.OptionSelected)
    def selected(self, event):
        item = self.rows()[event.option_index]
        if isinstance(item, str):
            if item == "add":
                self.run_worker(self.pick, thread=True, group="pick")
            else:
                {"same": self.toggle_same, "screen": self.next_screen, "other": self.toggle_others}[item]()
            return
        self.run_worker(lambda: self.use(item), thread=True, group="use")

    def use(self, item):
        def progress(received, total):
            percent = received * 100 // (total or 1)
            self.app.call_from_thread(self.app.set_help, f"Скачиваю «{item.title}»: {percent}%")

        path = item.path
        try:
            if item.remote:
                looks.fetch(item.item["url"], path, progress, item.item["size"])
            elif item.origin:
                path = looks.adopt_wallpaper(self.theme, item)
            error = looks.apply_wallpaper(path)
        except OSError as problem:
            error = f"Не удалось скачать: {problem}"
        self.app.call_from_thread(self.applied, error, item, path)

    def applied(self, error, item, path):
        self.app.set_help("")
        if error:
            self.app.notify(error, severity="warning", timeout=8)
        if item in self.others and path.exists():
            self.others.remove(item)
            self.items = looks.list_wallpapers(self.theme)
            self.fill(path.name)
        else:
            self.relabel()

    def pick(self):
        try:
            paths = looks.pick_files("Обои для темы", looks.IMAGE_TYPES | looks.VIDEO_TYPES, multiple=True)
        except OSError:
            self.app.call_from_thread(self.app.notify, "Не найден zenity: выбор файла недоступен", severity="warning")
            return
        added = []
        for path in paths:
            target, error = looks.add_wallpaper(self.theme, path)
            if error:
                self.app.call_from_thread(self.app.notify, f"{Path(path).name}: {error}", severity="warning")
            else:
                added.append(target)
        if added:
            self.app.call_from_thread(self.added, added[-1])

    def added(self, target):
        """Новый файл только выделяем: посмотреть и применить по Enter, как и остальные обои."""
        self.items = looks.list_wallpapers(self.theme)
        self.fill(target.name)


class StyleApp(App):
    CSS_PATH = Path(__file__).with_name("style.tcss")
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [
        Binding("q", "quit", "Выход"),
        Binding("e", "edit_config", "Открыть конфиг"),
        Binding("escape", "focus_nav", "К разделам", show=True),
    ]

    def __init__(self, section="style", start=None):
        super().__init__()
        self.heading = start or {"style": "Style", "setup": "Setup"}[section]
        self.build_tree = setup.build_tree if section == "setup" else settings.build_tree
        self.title = self.heading
        self.start = start
        self.store = settings.Store()
        self.current_page = None

    def compose(self) -> ComposeResult:
        yield Static(id="header")
        with Horizontal(id="body"):
            yield NavTree(self.heading, id="nav")
            with Vertical(id="main"):
                yield Static(id="crumbs")
                yield Vertical(id="page")
                yield Static(id="help")
        yield Footer()

    def on_mount(self):
        self.register_theme(textual_theme(looks.current_colors()))
        self.theme = "oshh"
        if not hypr.ensure_include():
            self.notify("Не найден ~/.config/hypr/hyprland.lua: настройки применятся только до перезагрузки",
                        severity="warning", timeout=10)
        tree = self.query_one(Tree)
        tree.show_root = False
        tree.guide_depth = 2
        first = None
        for title, icon, pages in self.build_tree(self.store):
            if len(pages) == 1:
                leaf = tree.root.add_leaf(f"{icon}  {title}", data=(title, pages[0]))
                first = first or leaf
                if title == self.start:
                    first = leaf
                continue
            branch = tree.root.add(f"{icon}  {title}", expand=False)
            for page in pages:
                leaf = branch.add_leaf(page[0], data=(title, page))
                first = first or leaf
                if title == self.start:
                    branch.expand()
                    first = leaf
        self.update_header()
        tree.focus()
        tree.move_cursor(first)
        self.show_node(first.data)

    def update_header(self):
        text = Text(f" {self.heading}", style="bold")
        theme = looks.pretty(looks.current_theme())
        pad = max(1, self.size.width - cell_len(text.plain) - cell_len(theme) - 8)
        text.append(" " * pad)
        text.append(f"тема: {theme} ", style="dim")
        self.query_one("#header", Static).update(text)

    def on_resize(self):
        self.update_header()

    def set_help(self, text):
        self.query_one("#help", Static).update(text or "")

    @on(Tree.NodeHighlighted)
    def node_highlighted(self, event):
        if event.node.data:
            self.show_node(event.node.data)

    @on(Tree.NodeSelected)
    def node_selected(self, event):
        if event.node.data:
            self.run_single(event.node.data)

    def single_row(self, data):
        """Страница из одной кнопки «открыть» страницей не считается: пункт сам её нажимает."""
        rows = data[1][2]
        if isinstance(rows, list) and len(rows) == 1 and rows[0].kind == "action" and rows[0].verb == "открыть":
            return rows[0]
        return None

    def show_node(self, data):
        row = self.single_row(data)
        if not row:
            self.open_page(data)
            return
        self.current_page = self.current_data = None
        self.query_one("#page").remove_children()
        self.query_one("#crumbs", Static).update(Text(f" {data[0]} › {data[1][0]}", style="bold"))
        self.set_help(f"{row.hint} Enter или клик — открыть.".strip())

    def run_single(self, data):
        row = self.single_row(data)
        if not row:
            return False
        error = row.set(None)
        if error:
            self.notify(str(error), severity="warning", timeout=6)
        return True

    def enter_page(self):
        page = self.query_one("#page")
        for widget in page.walk_children():
            if widget.focusable:
                widget.focus()
                return

    def open_page(self, data, force=False):
        group, (title, kind, payload) = data
        key = (group, title)
        if self.current_page == key and not force:
            return
        self.current_page = key
        self.current_data = data
        host = self.query_one("#page")
        host.remove_children()
        self.query_one("#crumbs", Static).update(Text(f" {group} › {title}", style="bold"))
        self.set_help("")
        widget = {
            "themes": lambda: ThemesPage(),
            "wallpapers": lambda: WallpapersPage(),
            "raw": lambda: ScenePage(None, settings.RAW_SCENES.get(payload, "info"), RawList(self.store, payload)),
        }.get(kind, lambda: ScenePage(self.current_rows(group, title), kind))()
        host.mount(widget)

    def current_rows(self, group, title):
        """Строки собираются заново: пресет или сброс мог поменять значения соседних параметров."""
        for name, _, pages in self.build_tree(self.store):
            if name == group:
                for page in pages:
                    if page[0] == title:
                        return page[2]
        return []

    def after_action(self):
        if self.current_data:
            self.open_page(self.current_data, force=True)
            self.call_after_refresh(self.enter_page)

    def action_focus_nav(self):
        self.query_one(Tree).focus()

    def action_edit_config(self):
        hypr.open_in_editor(hypr.MAIN_CONFIG)
        self.notify("Конфиг открыт во внешнем редакторе")

    def apply_theme(self, theme):
        error = looks.set_theme(theme.name)
        self.call_from_thread(self.theme_applied, theme, error)

    def theme_applied(self, theme, error):
        if error:
            self.notify(error, severity="warning", timeout=8)
        self.register_theme(textual_theme(looks.current_colors()))
        self.theme = "textual-dark"
        self.theme = "oshh"
        self.set_help(f"Тема {theme.title} применена")
        self.update_header()
        self.after_action()
