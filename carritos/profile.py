"""Persist channels, per-car names/colors and settings between runs."""
import json
import os
import tempfile
from pathlib import Path

from .core import MAX_NAME_LEN, Settings, normalize_channel, normalize_color

CONFIG_DIR = Path.home() / ".carritos-mc"
DEFAULT_PATH = CONFIG_DIR / "profile.json"
MAX_CARS = 500


def _empty() -> dict:
    return {"channels": [], "cars": {}, "settings": {}}


def load(path=DEFAULT_PATH) -> dict:
    """Read a profile; any problem (missing, corrupt, wrong shape) yields an empty one."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _empty()
    if not isinstance(raw, dict):
        return _empty()

    channels = []
    for item in raw.get("channels", []) if isinstance(raw.get("channels"), list) else []:
        channel = normalize_channel(item)
        if channel and channel not in channels:
            channels.append(channel)

    cars = {}
    raw_cars = raw.get("cars") if isinstance(raw.get("cars"), dict) else {}
    for key, entry in list(raw_cars.items())[:MAX_CARS]:
        if not isinstance(key, str) or not isinstance(entry, dict):
            continue
        clean = {}
        if isinstance(entry.get("name"), str):
            clean["name"] = " ".join(entry["name"].split())[:MAX_NAME_LEN]
        color = normalize_color(entry.get("color"))
        if color:
            clean["color"] = color
        cars[key] = clean

    settings = raw.get("settings") if isinstance(raw.get("settings"), dict) else {}
    return {"channels": channels, "cars": cars, "settings": settings}


def save(path, channels, cars, settings: Settings) -> None:
    """Write atomically so a crash mid-write cannot corrupt the profile."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "channels": list(channels),
        "cars": {k: {f: (list(v) if f == "color" else v) for f, v in e.items()} for k, e in cars.items()},
        "settings": settings.to_dict(),
    }
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
