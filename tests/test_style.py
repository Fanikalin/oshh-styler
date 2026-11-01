"""Запуск: python3 -I components/apps/style/tests/test_style.py"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image  # noqa: E402

from oshh_style import hypr, lockview, looks, monitors, scenes, screens, settings, setup  # noqa: E402

HYPRLOCK = """# мой комментарий
input-field {
    monitor =
    font_family = JetBrainsMono  # шрифт
}
"""


class Lua(unittest.TestCase):
    def test_values(self):
        self.assertEqual(hypr.lua_value(True), "true")
        self.assertEqual(hypr.lua_value(0.5), "0.5")
        self.assertEqual(hypr.lua_value("rgba(ff0000ee)"), '"rgba(ff0000ee)"')

    def test_gaps_are_never_a_string(self):
        self.assertEqual(hypr.lua_value("5 5 5 5"), "5")
        self.assertEqual(hypr.lua_value("1 2 3 4"), "{ top = 1, right = 2, bottom = 3, left = 4 }")

    def test_string_cannot_escape(self):
        self.assertEqual(hypr.lua_string('a"b\\\nc'), '"a\\"b\\\\\\nc"')

    def test_nested_option(self):
        self.assertEqual(hypr.lua_config("general:col.active_border", "x"),
                         'hl.config({ general = { col = { active_border = "x" } } })')
        self.assertEqual(hypr.lua_key("3d"), '["3d"]')

    def test_overlay_holds_only_changes(self):
        state = {"env": {}, "hypr": {"general:gaps_in": 2}, "animations": {"windows": {"speed": 2, "bezier": "x"}}}
        lines = hypr.render_overlay(state).splitlines()
        self.assertIn("hl.config({ general = { gaps_in = 2 } })", lines)
        self.assertIn('hl.animation({ leaf = "windows", enabled = true, speed = 2, bezier = "x" })', lines)
        self.assertEqual(len([line for line in lines if line.startswith("hl.config")]), 1)


class Include(unittest.TestCase):
    def test_goes_before_toggles_once(self):
        with tempfile.TemporaryDirectory() as folder:
            hypr.MAIN_CONFIG = Path(folder) / "hyprland.lua"
            hypr.MAIN_CONFIG.write_text("a\n-- Persistent toggles\nb\n")
            hypr.ensure_include()
            hypr.ensure_include()
            text = hypr.MAIN_CONFIG.read_text()
            self.assertEqual(text.count(hypr.INCLUDE_MARK), 1)
            self.assertLess(text.index(hypr.INCLUDE_MARK), text.index(hypr.TOGGLES_MARK))
            self.assertEqual(Path(folder, "hyprland.lua.before-oshh-style").read_text(), "a\n-- Persistent toggles\nb\n")


class Hyprlock(unittest.TestCase):
    def test_get(self):
        self.assertEqual(screens.conf_get(HYPRLOCK, "input-field", "monitor"), "")
        self.assertEqual(screens.conf_get(HYPRLOCK, "image", "path", "нет"), "нет")

    def test_set_keeps_the_rest(self):
        text = screens.conf_set(HYPRLOCK, "input-field", "monitor", "DP-1")
        self.assertIn("    monitor = DP-1\n", text)
        self.assertIn("# мой комментарий", text)
        self.assertIn("# шрифт", text)

    def test_set_adds_key_and_section(self):
        text = screens.conf_set(HYPRLOCK, "input-field", "rounding", "4")
        self.assertEqual(screens.conf_get(text, "input-field", "rounding"), "4")
        text = screens.conf_set(text, "background", "blur_passes", "2")
        self.assertEqual(screens.conf_get(text, "background", "blur_passes"), "2")

    def test_clock_toggles_cleanly(self):
        with tempfile.TemporaryDirectory() as folder:
            screens.HYPRLOCK = Path(folder) / "hyprlock.conf"
            screens.HYPRLOCK.write_text(HYPRLOCK)
            screens.write_clock({"clock": True})
            screens.write_clock({"clock": True, "clock_format": "12"})
            text = screens.HYPRLOCK.read_text()
            self.assertEqual(text.count(screens.CLOCK_BEGIN), 1)
            self.assertIn("%I:%M", text)
            screens.write_clock({"clock": False})
            self.assertEqual(screens.HYPRLOCK.read_text().strip(), HYPRLOCK.strip())


class LockView(unittest.TestCase):
    def test_colors(self):
        names = {"$x": "rgba(10, 20, 30, 0.5)"}
        self.assertEqual(lockview.color("$x", names), (10, 20, 30, 128))
        self.assertEqual(lockview.color("rgb(ff0080)", {}), (255, 0, 128, 255))
        self.assertEqual(lockview.color("0x80ff0080", {}), (255, 0, 128, 128))
        self.assertEqual(lockview.color("чепуха", {}, (1, 2, 3, 4)), (1, 2, 3, 4))

    def test_sections_and_labels(self):
        text = 'label {\n    text = a\n}\nlabel {\n    text = cmd[update:1000] date +"%Y"\n}\n'
        blocks = lockview.sections(text, "label")
        self.assertEqual([block["text"][:3] for block in blocks], ["a", "cmd"])
        self.assertRegex(lockview.label_text(blocks[1]["text"]), r"^\d{4}$")
        self.assertEqual(lockview.label_text("<i>привет</i>"), "привет")

    def test_position_axis_points_up(self):
        lockview.WIDTH, lockview.HEIGHT = 100, 100
        self.assertEqual(lockview.anchor({"position": "0, 10"}, 20, 20, 1), (40, 30))
        self.assertEqual(lockview.anchor({"position": "0, -5", "valign": "top"}, 20, 20, 1), (40, 5))

    def test_render_from_config(self):
        lockview.WIDTH, lockview.HEIGHT = 320, 180
        with tempfile.TemporaryDirectory() as folder:
            screens.HYPRLOCK = Path(folder) / "hyprlock.conf"
            Path(folder, "theme.conf").write_text("$color = rgba(255, 0, 0, 1.0)\n")
            screens.HYPRLOCK.write_text(f"source = {folder}/theme.conf\nbackground {{\n    color = $color\n}}\n"
                                        "input-field {\n    size = 400, 100\n    fade_on_empty = false\n    rounding = 0\n"
                                        "    outer_color = rgba(0, 255, 0, 1.0)\n    inner_color = rgba(0, 0, 255, 1.0)\n}\n")
            target = lockview.render(Path(folder) / "out.png", 1920)
            with Image.open(target) as image:
                self.assertEqual(image.getpixel((5, 5)), (255, 0, 0))
                self.assertEqual(image.getpixel((160, 90)), (0, 0, 255))


class Fetch(unittest.TestCase):
    def test_short_download_leaves_nothing(self):
        with tempfile.TemporaryDirectory() as folder:
            source, target = Path(folder, "source"), Path(folder, "out/clip.mp4")
            source.write_bytes(b"1234")
            with self.assertRaises(OSError):
                looks.fetch(source.as_uri(), target, expected=5)
            self.assertEqual(list(target.parent.iterdir()), [])
            looks.fetch(source.as_uri(), target, expected=4)
            self.assertEqual(target.read_bytes(), b"1234")


class Catalog(unittest.TestCase):
    def test_every_theme_has_a_clip(self):
        covered = {theme for item in looks.catalog() for theme in item["themes"]}
        root = looks.OMARCHY / "themes"
        if not root.is_dir():
            root = looks.USER_THEMES
        themes = {path.name for path in root.iterdir()}
        self.assertEqual(themes - covered, set())

    def test_landscape(self):
        for item in looks.catalog():
            self.assertGreater(item["width"], item["height"], item["id"])


class Sections(unittest.TestCase):
    class Store:
        options = {name: {"type": "bool", "value": False, "description": "", "name": name, "default": False, "current": False}
                   for name in ("input:sensitivity", "general:gaps_in")}
        state = {"hypr": {}, "animations": {}, "env": {}}

    def test_input_lives_in_setup(self):
        titles = [group for group, _, _ in setup.build_tree(self.Store)]
        self.assertIn("Мышь и клавиатура", titles)

    def test_bindings_are_read_from_lua(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "hyprland.lua"
            path.write_text('hl.bind("SUPER + K", x)\nhl.monitor({ output = "DP-1" })\n')
            self.assertEqual(setup.config_lines(path, "hl.monitor"), 'hl.monitor({ output = "DP-1" })')
            original, hypr.MAIN_CONFIG = hypr.MAIN_CONFIG, path
            path.write_text('hl.bind("SUPER + K", x, { description = "Show" })\n')
            self.assertEqual(setup.bindings().split(), ["SUPER", "+", "K", "Show"])
            hypr.MAIN_CONFIG = original


class LockLogo(unittest.TestCase):
    def test_hide_and_show_keep_the_rest(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "hyprlock.conf"
            path.write_text("general {\n}\n\nimage {\n    path = /old.png\n    size = 120\n}\n\nlabel {\n}\n")
            original, screens.HYPRLOCK = screens.HYPRLOCK, path
            try:
                state = {"lock": {}}
                settings = screens.logo_settings(state)
                self.assertEqual(settings["path"], "/old.png")
                screens.write_logo({**settings, "show": False})
                self.assertNotIn("image {", path.read_text())
                self.assertIn("label {", path.read_text())
                screens.write_logo({**settings, "path": "/new.png"})
                screens.write_logo({**settings, "path": "/new2.png"})
                self.assertEqual(path.read_text().count("image {"), 1)
                self.assertIn("/new2.png", path.read_text())
            finally:
                screens.HYPRLOCK = original


class FilePicker(unittest.TestCase):
    def test_several_files_and_cancel(self):
        with tempfile.TemporaryDirectory() as folder:
            fake = Path(folder) / "zenity"
            fake.write_text('#!/bin/bash\n[[ $* == *--multiple* ]] || exit 1\nprintf "/a b.png\\n/c.mp4\\n"\n')
            fake.chmod(0o755)
            original = os.environ["PATH"]
            os.environ["PATH"] = f"{folder}:{original}"
            try:
                self.assertEqual(looks.pick_files("t", {".png"}, multiple=True), ["/a b.png", "/c.mp4"])
                self.assertEqual(looks.pick_files("t", {".png"}), [])
            finally:
                os.environ["PATH"] = original


class MotionSlider(unittest.TestCase):
    def test_stops_follow_the_applied_set(self):
        class Store:
            spec = {"speed": 3.8, "bezier": "easeOutQuint", "enabled": True, "style": ""}
            state = {"animations": {}}

            def animation(self, leaf):
                return dict(self.spec)

            def set_many(self, options, leaves):
                Store.spec = leaves["windows"]

        row = settings.motion_row(Store())
        self.assertEqual(row.marks[row.get()], "Обычные")
        for index in range(len(row.marks)):
            row.set(index)
            self.assertEqual(row.get(), index)


class MotionPreview(unittest.TestCase):
    def test_curve_ends_and_overshoot(self):
        self.assertAlmostEqual(scenes.bezier((0.0, 0.75, 0.15, 1.0), 0), 0, places=3)
        self.assertAlmostEqual(scenes.bezier((0.0, 0.75, 0.15, 1.0), 1), 1, places=3)
        self.assertGreater(scenes.bezier((0.05, 0.9, 0.1, 1.5), 0.5), 1)

    def test_every_style_draws_a_frame(self):
        back = Image.new("RGBA", (232, 130), (10, 10, 10, 255))
        for leaf, style in (("windowsIn", "slide"), ("windowsOut", "popin 87%"), ("windowsIn", "gnomed"), ("windows", ""),
                            ("workspaces", "slidevert"), ("workspaces", "slidefade 20%"), ("workspaces", "fade"),
                            ("border", ""), ("fade", ""), ("layers", "fade")):
            for value in (0, 0.5, 1.15):
                self.assertEqual(scenes.mini_frame(back, value, leaf, style, {}).size, back.size)


class Panel(unittest.TestCase):
    TEXT = '{\n  "position": "top",\n  "height": 26,\n  "clock": {\n    "format": "x"\n  }\n}\n'

    def test_side_and_back(self):
        side = settings.waybar_layout(self.TEXT, "left")
        self.assertIn('"position": "left"', side)
        self.assertIn('"width": 26', side)
        self.assertNotIn('"height"', side)
        self.assertIn('"rotate": 270', side)
        self.assertIn('"rotate": 90', settings.waybar_layout(side, "right"))
        self.assertEqual(settings.waybar_layout(side, "top"), self.TEXT)

    def test_bar_keeps_the_opposite_side_free(self):
        for position, reserve in (("top", (0, 20, 0, 0)), ("bottom", (0, 0, 0, 20)), ("left", (20, 0, 0, 0)), ("right", (0, 0, 20, 0))):
            canvas = Image.new("RGBA", (400, 300))
            with mock.patch.object(scenes, "bar_settings", return_value=(position, 20)):
                self.assertEqual(scenes.draw_bar(canvas, {}, 1), reserve)


class OtherWallpapers(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        patches = {"USER_BACKGROUNDS": self.root / "backgrounds", "OMARCHY": self.root / "omarchy", "USER_THEMES": self.root / "themes",
                   "CURRENT": self.root / "current", "CACHE": self.root / "cache"}
        for name, value in patches.items():
            patcher = mock.patch.object(looks, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        mock.patch.object(looks, "catalog", return_value=[]).start()
        self.addCleanup(mock.patch.stopall)

    def image(self, path, color):
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (32, 18), color).save(path)
        return path

    def test_lists_only_what_the_theme_does_not_show(self):
        self.image(self.root / "current/theme/backgrounds/same.png", "black")
        self.image(self.root / "omarchy/themes/nord/backgrounds/same.png", "black")
        self.image(self.root / "omarchy/themes/nord/backgrounds/blue.png", "blue")
        self.image(self.root / "backgrounds/white/mine.png", "white")
        self.image(self.root / "backgrounds/dark/own.png", "gray")
        names = sorted((item.path.name, item.origin) for item in looks.other_wallpapers("dark"))
        self.assertEqual(names, [("blue.png", "nord"), ("mine.png", "white")])

    def test_closest_to_the_theme_goes_first(self):
        walls = [looks.Wallpaper(self.image(self.root / f"{name}.png", name), False, None, "x") for name in ("white", "red", "navy")]
        with mock.patch.object(looks, "current_colors", return_value={"background": "#000020", "foreground": "#000080"}):
            self.assertEqual(looks.by_theme_colors(walls)[0].path.stem, "navy")
            self.assertTrue((self.root / "cache/palettes.json").exists())
            self.assertEqual(looks.by_theme_colors(walls + [looks.Wallpaper(self.root / "none.png", False, None, "x")])[-1].path.stem, "none")

    def test_adopted_copy_does_not_overwrite(self):
        source = self.image(self.root / "omarchy/themes/nord/backgrounds/a.png", "blue")
        first = looks.adopt_wallpaper("dark", looks.Wallpaper(source, False, None, "nord"))
        second = looks.adopt_wallpaper("dark", looks.Wallpaper(source, False, None, "nord"))
        self.assertEqual((first.name, second.name), ("a.png", "nord-a.png"))


class Monitors(unittest.TestCase):
    @staticmethod
    def monitor(name, x, y, width=1920, height=1080, scale=1, transform=0):
        return {"name": name, "x": x, "y": y, "width": width, "height": height, "scale": scale, "transform": transform,
                "disabled": False, "mirrorOf": "none", "id": 0}

    def test_size_follows_scale_and_rotation(self):
        self.assertEqual(monitors.size(self.monitor("a", 0, 0, 2560, 1440, 2)), (1280, 720))
        self.assertEqual(monitors.size(self.monitor("a", 0, 0, transform=1)), (1080, 1920))

    def test_relation_reads_the_live_layout(self):
        base = self.monitor("base", 0, 0, 2560, 1440)
        self.assertEqual(monitors.relation(self.monitor("a", 2560, -540), base), ("right", -540))
        self.assertEqual(monitors.relation(self.monitor("a", -1920, 100), base), ("left", 100))
        self.assertEqual(monitors.relation(self.monitor("a", 40, -1080), base), ("top", 40))
        self.assertEqual(monitors.relation(self.monitor("a", 0, 1440), base), ("bottom", 0))

    def test_layout_stays_next_to_a_resized_screen(self):
        base = self.monitor("base", 0, 0, 1280, 720)
        side = self.monitor("side", 2560, 0)
        self.assertEqual(monitors.layout([base, side], {"side": ("right", 10)}), {"side": (1280, 10)})

    def test_two_screens_on_one_side_do_not_overlap(self):
        base, first, second = self.monitor("base", 0, 0), self.monitor("a", 1920, 0), self.monitor("b", 4000, 0)
        spots = monitors.layout([base, first, second], {"a": ("right", 0), "b": ("right", 0)})
        self.assertEqual(spots, {"a": (1920, 0), "b": (3840, 0)})

    def test_modes_are_grouped_and_sorted(self):
        found = monitors.modes({"availableModes": ["1280x720@60.00Hz", "1920x1080@60.00Hz", "1920x1080@100.00Hz"]})
        self.assertEqual(list(found), ["1920x1080", "1280x720"])
        self.assertEqual(found["1920x1080"], ["100.00", "60.00"])

    def test_lua_lines(self):
        line = hypr.lua_monitor("DP-1", {"mode": "2560x1440@165.00", "position": "0x0", "scale": 1.5, "transform": 1, "vrr": 2})
        for part in ('output = "DP-1"', 'mode = "2560x1440@165.00"', 'position = "0x0"', "scale = 1.5", "transform = 1", "vrr = 2"):
            self.assertIn(part, line)
        self.assertEqual(hypr.lua_monitor("A", {"disabled": True, "mode": "x"}), 'hl.monitor({ output = "A", disabled = true })')

    def test_single_enabled_screen_stays_on(self):
        store = mock.Mock(state={"monitors": {}})
        with mock.patch.object(monitors, "listing", return_value=[self.monitor("only", 0, 0)]), \
                mock.patch.object(monitors, "rate", return_value="60.00"), mock.patch.object(hypr, "apply_live") as send:
            self.assertIn("единственный", monitors.Screens(store).change("only", disabled=True))
            send.assert_not_called()


class Sliders(unittest.TestCase):
    def test_float_range_gets_a_fine_step(self):
        self.assertEqual(settings.Setting("x", kind="float", lo=-1, hi=1).step, 0.05)
        self.assertEqual(settings.Setting("x", kind="float", lo=-1, hi=1, step=0.01).step, 0.01)


class Opacity(unittest.TestCase):
    def test_overlay_rule_comes_after_state(self):
        state = {"hypr": {}, "animations": {}, "env": {}, "monitors": {}, "opacity": {"terminal": [0.6, 0.5]}}
        text = hypr.render_overlay(state)
        self.assertIn('match = { tag = "terminal" }, opacity = "0.6 0.5"', text)

    def test_slider_steps_small_and_never_below_minimum(self):
        from oshh_style import settings as st
        seen = []
        row = st.Setting("t", "", "float", 0.1, 1, 0.01, getter=lambda: seen[-1] if seen else 1, setter=seen.append)
        for big, expect in ((False, 0.99), (True, 0.9)):
            seen.clear()
            value = max(row.lo, round(1 - row.step * (10 if big else 1), 3))
            self.assertEqual(value, expect)
        self.assertGreaterEqual(row.lo, 0.1)


if __name__ == "__main__":
    unittest.main()
