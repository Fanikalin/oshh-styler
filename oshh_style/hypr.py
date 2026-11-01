"""Hyprland: чтение параметров через hyprctl и слой настроек поверх конфига.

Конфиг пользователя (~/.config/hypr/hyprland.lua) остаётся главным. Приложение пишет только то,
что пользователь изменил, в отдельный ~/.config/hypr/oshh-style.lua, который hyprland.lua подключает
перед переключателями Omarchy. Удалил файл — вернулся к чистому конфигу.
"""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

HOME = Path.home()
HYPR_DIR = HOME / ".config/hypr"
MAIN_CONFIG = HYPR_DIR / "hyprland.lua"
OVERLAY = HYPR_DIR / "oshh-style.lua"
STATE = HOME / ".config/oshh/style.json"

INCLUDE_MARK = "-- oshh-style: настройки приложения «Оформление»"
INCLUDE_BLOCK = f"""{INCLUDE_MARK}
do
  local path = home .. "/.config/hypr/oshh-style.lua"
  local f = io.open(path, "r")
  if f then
    f:close()
    local ok, err = pcall(dofile, path)
    if not ok then
      hl.exec_cmd("notify-send -u critical 'Оформление: ошибка настроек' " .. string.format("%q", tostring(err)))
    end
  end
end

"""
TOGGLES_MARK = "-- Persistent toggles"
BOUNCE_CURVE = "oshhBounce"
CURVES = f'hl.curve("{BOUNCE_CURVE}", {{ type = "bezier", points = {{ {{ 0.05, 0.9 }}, {{ 0.1, 1.1 }} }} }})'


def hyprctl(*args):
    try:
        done = subprocess.run(["hyprctl", *args], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired) as error:
        return str(error)
    return done.stdout.strip() or done.stderr.strip()


def descriptions():
    """Все параметры текущей версии Hyprland: имя, описание, значения, границы."""
    try:
        return {item["name"]: item for item in json.loads(hyprctl("descriptions", "-j"))}
    except ValueError:
        return {}


def animations():
    """(ветви анимаций, кривые) из работающего Hyprland."""
    try:
        leaves, curves = json.loads(hyprctl("animations", "-j"))
    except ValueError:
        return {}, []
    return {leaf["name"]: leaf for leaf in leaves}, [curve["name"] for curve in curves]


def curve_points():
    """Опорные точки кривых: имя -> (x0, y0, x1, y1)."""
    try:
        _, curves = json.loads(hyprctl("animations", "-j"))
    except ValueError:
        return {}
    return {curve["name"]: (curve["X0"], curve["Y0"], curve["X1"], curve["Y1"]) for curve in curves}


def load_state():
    try:
        state = json.loads(STATE.read_text())
    except (OSError, ValueError):
        state = {}
    for key in ("hypr", "animations", "env", "lock", "greeter", "monitors", "opacity"):
        if not isinstance(state.get(key), dict):
            state[key] = {}
    return state


def save_state(state):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    temporary.replace(STATE)


def lua_string(value):
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def lua_key(key):
    return key if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) else f"[{lua_string(key)}]"


def lua_value(value):
    """Значение параметра в синтаксисе Lua. Зазоры "5 5 5 5" Hyprland принимает числом или таблицей."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(round(value, 4))
    if isinstance(value, (list, tuple)):
        return "{ " + ", ".join(lua_value(item) for item in value) + " }"
    text = str(value)
    if re.fullmatch(r"\d+( \d+){3}", text):
        top, right, bottom, left = text.split()
        if top == right == bottom == left:
            return top
        return f"{{ top = {top}, right = {right}, bottom = {bottom}, left = {left} }}"
    if text.startswith("{"):
        return text
    return lua_string(text)


def option_path(name):
    """general:col.active_border -> ["general", "col", "active_border"]"""
    return re.split(r"[:.]", name)


def lua_config(name, value):
    code = lua_value(value)
    for key in reversed(option_path(name)):
        code = f"{{ {lua_key(key)} = {code} }}"
    return f"hl.config({code})"


def lua_animation(leaf, spec):
    fields = [f"leaf = {lua_string(leaf)}", f"enabled = {lua_value(bool(spec.get('enabled', True)))}"]
    fields.append(f"speed = {lua_value(spec.get('speed', 1))}")
    fields.append(f"bezier = {lua_string(spec.get('bezier') or 'default')}")
    if spec.get("style"):
        fields.append(f"style = {lua_string(spec['style'])}")
    return "hl.animation({ " + ", ".join(fields) + " })"


OPACITY_TAGS = {"default": "default-opacity", "terminal": "terminal"}
OPACITY_DEFAULT = {"default": (0.97, 0.9), "terminal": (0.97, 0.9)}


def lua_opacity(tag, pair):
    """Правило поверх omarchy: оно стоит позже и заменяет прозрачность окон с этой меткой."""
    return f'hl.window_rule({{ match = {{ tag = "{OPACITY_TAGS[tag]}" }}, opacity = "{pair[0]} {pair[1]}" }})'


def lua_monitor(name, spec):
    if spec.get("disabled"):
        return f"hl.monitor({{ output = {lua_string(name)}, disabled = true }})"
    fields = [f"output = {lua_string(name)}", "disabled = false"]
    fields += [f"{key} = {lua_string(spec[key])}" for key in ("mode", "position", "mirror") if spec.get(key)]
    fields += [f"{key} = {spec[key]}" for key in ("scale", "transform", "vrr", "bitdepth") if key in spec]
    return f"hl.monitor({{ {', '.join(fields)} }})"


def render_overlay(state):
    lines = [
        "-- Файл пишет приложение «Оформление» (oshh-style-open). Здесь только то, что изменено в нём.",
        "-- Руками правьте hyprland.lua: при сбросе настроек этот файл перезаписывается.",
        "",
        CURVES,
    ]
    lines += [f"hl.env({lua_string(key)}, {lua_string(value)})" for key, value in sorted(state["env"].items())]
    lines += [lua_config(name, value) for name, value in sorted(state["hypr"].items())]
    lines += [lua_animation(leaf, spec) for leaf, spec in sorted(state["animations"].items())]
    lines += [lua_opacity(tag, pair) for tag, pair in sorted(state.get("opacity", {}).items())]
    lines += [lua_monitor(name, spec) for name, spec in sorted(state.get("monitors", {}).items())]
    return "\n".join(lines) + "\n"


def write_overlay(state):
    temporary = OVERLAY.with_suffix(".tmp")
    temporary.write_text(render_overlay(state))
    temporary.replace(OVERLAY)


def ensure_include():
    """Подключает слой к hyprland.lua один раз. Уже настроенную систему тоже подхватывает."""
    try:
        text = MAIN_CONFIG.read_text()
    except OSError:
        return False
    if INCLUDE_MARK in text:
        return True
    backup = MAIN_CONFIG.with_suffix(".lua.before-oshh-style")
    if not backup.exists():
        shutil.copy2(MAIN_CONFIG, backup)
    position = text.find(TOGGLES_MARK)
    text = text + "\n" + INCLUDE_BLOCK if position < 0 else text[:position] + INCLUDE_BLOCK + text[position:]
    temporary = MAIN_CONFIG.with_suffix(".tmp")
    temporary.write_text(text)
    temporary.replace(MAIN_CONFIG)
    return True


def apply_live(code):
    """Применяет Lua сразу, без перезагрузки конфига. Возвращает текст ошибки или None."""
    reply = hyprctl("eval", code)
    return None if reply == "ok" else reply


def editor_command(path):
    """Редактор пользователя в своём окне. Нет редактора — приложение по умолчанию для файла."""
    editor = os.environ.get("EDITOR", "")
    if editor and shutil.which(editor.split()[0]) and shutil.which("omarchy-launch-editor"):
        return ["omarchy-launch-editor", str(path)]
    return ["xdg-open", str(path)]


def open_in_editor(path):
    subprocess.Popen(editor_command(path), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
