"""Экраны: снимок из Hyprland, расположение и применение через hl.monitor."""
import json
import time

from . import hypr

SAVED_ONLY = ("vrr", "bitdepth")
SIDES = [("right", "справа"), ("left", "слева"), ("top", "сверху"), ("bottom", "снизу")]
ALIGNS = [("start", "по началу"), ("center", "по центру"), ("end", "по концу"), ("free", "свой сдвиг")]


def listing():
    """Все экраны, выключенные тоже. Основной и подключённые раньше идут в порядке Hyprland."""
    try:
        return sorted(json.loads(hypr.hyprctl("monitors", "all", "-j")), key=lambda item: (item["disabled"], item["x"], item["y"]))
    except ValueError:
        return []


def title(monitor):
    model = monitor.get("model")
    return f"{monitor['name']} · {model}" if model else monitor["name"]


def size(monitor):
    """Размер на карте: с учётом масштаба и поворота."""
    width, height = monitor["width"] / (monitor["scale"] or 1), monitor["height"] / (monitor["scale"] or 1)
    return (round(height), round(width)) if monitor["transform"] % 2 else (round(width), round(height))


def modes(monitor):
    """{'1920x1080': ['100.00', '60.00']}: разрешения по убыванию, частоты по убыванию."""
    found = {}
    for mode in monitor.get("availableModes", []):
        resolution, _, rate = mode.removesuffix("Hz").partition("@")
        found.setdefault(resolution, []).append(rate)
    area = lambda name: int(name.split("x")[0]) * int(name.split("x")[1])
    return {name: sorted(found[name], key=float, reverse=True) for name in sorted(found, key=area, reverse=True)}


def resolution(monitor):
    return f"{monitor['width']}x{monitor['height']}"


def rate(monitor):
    """Ближайшая частота из списка экрана: Hyprland сообщает её с другой точностью."""
    rates = modes(monitor).get(resolution(monitor)) or [f"{monitor['refreshRate']:.2f}"]
    return min(rates, key=lambda item: abs(float(item) - monitor["refreshRate"]))


def anchor(active):
    return next((item for item in active if item["x"] == 0 and item["y"] == 0), active[0])


def relation(monitor, base):
    """(сторона относительно опорного экрана, сдвиг вдоль неё)."""
    width, height = size(monitor)
    base_width, base_height = size(base)
    if monitor["x"] >= base["x"] + base_width:
        return "right", monitor["y"] - base["y"]
    if monitor["x"] + width <= base["x"]:
        return "left", monitor["y"] - base["y"]
    if monitor["y"] + height <= base["y"]:
        return "top", monitor["x"] - base["x"]
    return "bottom", monitor["x"] - base["x"]


def span(monitor, side):
    return size(monitor)[1 if side in ("left", "right") else 0]


def layout(active, relations):
    """{имя: (x, y)}. Экраны встают вплотную к уже расставленным, поэтому не наезжают друг на друга."""
    base = anchor(active)
    placed = [(base["x"], base["y"], *size(base))]
    result = {}
    others = sorted((item for item in active if item is not base), key=lambda item: abs(item["x"] - base["x"]) + abs(item["y"] - base["y"]))
    for monitor in others:
        side, offset = relations[monitor["name"]]
        width, height = size(monitor)
        if side == "right":
            spot = (max(x + w for x, _, w, _ in placed), base["y"] + offset)
        elif side == "left":
            spot = (min(x for x, _, _, _ in placed) - width, base["y"] + offset)
        elif side == "top":
            spot = (base["x"] + offset, min(y for _, y, _, _ in placed) - height)
        else:
            spot = (base["x"] + offset, max(y + h for _, y, _, h in placed))
        placed.append((*spot, width, height))
        result[monitor["name"]] = spot
    return result


class Screens:
    """Живой снимок экранов и его изменение. В конфиг попадает только то, что пользователь оставил."""

    def __init__(self, store):
        self.store = store
        store.state.setdefault("monitors", {})
        self.refresh()
        self.loaded = self.snapshot()

    def refresh(self):
        self.items = listing()

    def find(self, name):
        return next((item for item in self.items if item["name"] == name), None)

    def active(self):
        return [item for item in self.items if not item["disabled"] and item["mirrorOf"] == "none"]

    def base(self):
        return anchor(self.active())

    def mirror_of(self, monitor):
        if monitor["mirrorOf"] == "none":
            return ""
        return next((item["name"] for item in self.items if str(item["id"]) == monitor["mirrorOf"]), "")

    def spec(self, name):
        monitor = self.find(name)
        saved = self.store.state["monitors"].get(name, {})
        if monitor["disabled"]:
            return {**saved, "disabled": True}
        spec = {"mode": f"{resolution(monitor)}@{rate(monitor)}", "position": f"{monitor['x']}x{monitor['y']}",
                "scale": monitor["scale"], "transform": monitor["transform"]}
        spec.update({key: saved[key] for key in SAVED_ONLY if key in saved})
        mirror = self.mirror_of(monitor)
        return {**spec, "mirror": mirror} if mirror else spec

    def snapshot(self):
        return {item["name"]: self.spec(item["name"]) for item in self.items}

    def relations(self):
        active = self.active()
        if not active:
            return {}
        base = anchor(active)
        return {item["name"]: relation(item, base) for item in active if item is not base}

    def send(self, name, spec):
        return hypr.apply_live(hypr.lua_monitor(name, spec))

    def change(self, name, **fields):
        """Меняет экран и сдвигает соседей, чтобы они остались рядом с ним."""
        if fields.get("disabled") and [item["name"] for item in self.active()] == [name]:
            return "Это единственный включённый экран"
        relations = self.relations()
        spec = {key: value for key, value in {**self.spec(name), **fields}.items() if value not in (None, "")}
        if spec.get("disabled") is False:
            del spec["disabled"]
            spec = {"mode": "preferred", "position": "auto", "scale": 1, **spec}
        error = self.send(name, spec)
        if error:
            return error
        time.sleep(0.4)
        self.refresh()
        return self.arrange(relations)

    def arrange(self, relations):
        active = self.active()
        if not active:
            return None
        known = {item["name"]: relations.get(item["name"]) for item in active}
        base = anchor(active)
        missing = {item["name"]: relation(item, base) for item in active if item is not base and not known[item["name"]]}
        for name, (x, y) in layout(active, {**known, **missing}).items():
            monitor = self.find(name)
            if (monitor["x"], monitor["y"]) != (x, y):
                error = self.send(name, {**self.spec(name), "position": f"{x}x{y}"})
                if error:
                    return error
        self.refresh()
        return None

    def move(self, name, side=None, offset=None):
        relations = self.relations()
        old_side, old_offset = relations[name]
        relations[name] = (side or old_side, old_offset if offset is None else offset)
        error = self.arrange(relations)
        if not error:
            self.save()
        return error

    def restore(self, snapshot):
        for name, spec in snapshot.items():
            if self.find(name) and self.spec(name) != spec:
                self.send(name, spec)
        time.sleep(0.4)
        self.refresh()

    def save(self):
        monitors = self.store.state["monitors"]
        current = self.snapshot()
        for name, spec in current.items():
            if name in monitors or spec != self.loaded.get(name):
                monitors[name] = spec
        self.loaded = current
        self.store.persist()

    def reset(self, name):
        self.store.state["monitors"].pop(name, None)
        self.store.reload()
        self.refresh()
        self.loaded = self.snapshot()
