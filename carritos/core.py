"""Pure game logic: tilt mapping, car physics, MQTT routing and the thread-safe fleet.

No pygame or MQTT imports here, so everything is unit-testable.
Screen coordinates: x grows right, y grows down, heading 0 = facing right,
and a positive heading change turns clockwise on screen (i.e. steer right).

A car is identified by a key "<channel>/<id>" (channel = MQTT topic prefix), so the
same device id on two channels gives two cars.
"""
import colorsys
import json
import math
import random
import re
import threading
import time
from dataclasses import asdict, dataclass, replace

# Tilt mapping (degrees)
DEADZONE_DEG = 5.0
FULL_TILT_DEG = 30.0

# Car physics (pixels, seconds)
MAX_SPEED = 320.0
MAX_REVERSE = 120.0
ACCEL = 400.0
FRICTION = 220.0
TURN_RATE = 2.6          # rad/s at full steer and full grip
TURN_SPEED_REF = 90.0    # speed at which steering reaches full authority
CAR_RADIUS = 26.0        # roughly half the sprite length, so cars do not overlap visually
BUMP_DAMP = 0.6

# A car that sends nothing for this long is removed from the arena
TIMEOUT_S = 3.0
SPAWN_MARGIN = 80.0

MAX_BOTS = 12
MAX_NAME_LEN = 20
MAX_CHANNEL_LEN = 48

PALETTE = [
    (255, 99, 110), (255, 149, 66), (255, 205, 86), (150, 224, 97),
    (52, 211, 153), (45, 212, 224), (88, 166, 255), (129, 140, 248),
    (176, 124, 255), (240, 110, 230), (255, 120, 170), (226, 232, 240),
]

CHANNEL_RE = re.compile(r"^[A-Za-z0-9_-]+(/[A-Za-z0-9_-]+)*$")
MSG_KINDS = ("tilt", "status")

SETTING_BOUNDS = {
    "deadzone_deg": (0.0, 15.0),
    "full_tilt_deg": (15.0, 60.0),
    "max_speed": (120.0, 600.0),
}
SETTING_FLAGS = ("trails", "show_names", "show_grid")


# --- helpers ----------------------------------------------------------------

def tilt_axis(angle_deg: float, deadzone: float = DEADZONE_DEG, full: float = FULL_TILT_DEG) -> float:
    """Map a tilt angle to [-1, 1] with a deadzone and linear ramp."""
    magnitude = abs(angle_deg)
    if magnitude <= deadzone:
        return 0.0
    scaled = min((magnitude - deadzone) / (full - deadzone), 1.0)
    return math.copysign(scaled, angle_deg)


def parse_tilt(payload):
    """Parse '{"roll": deg, "pitch": deg}' -> (roll, pitch), or None if invalid."""
    try:
        data = json.loads(payload)
        roll = float(data["roll"])
        pitch = float(data["pitch"])
    except (ValueError, KeyError, TypeError):
        return None
    if not (math.isfinite(roll) and math.isfinite(pitch)):
        return None
    return roll, pitch


def normalize_channel(value):
    """Clean an MQTT topic prefix ('lab/cars'); None if it is not a safe prefix."""
    if not isinstance(value, str):
        return None
    value = value.strip().strip("/")
    if not value or len(value) > MAX_CHANNEL_LEN or not CHANNEL_RE.match(value):
        return None
    return value


def normalize_color(value):
    """Return (r, g, b) if value is a valid 3-int 0..255 sequence, else None."""
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    if not all(isinstance(c, int) and not isinstance(c, bool) and 0 <= c <= 255 for c in value):
        return None
    return tuple(value)


def hue_color(hue: float) -> tuple:
    """Vivid color for a hue in [0, 1]."""
    r, g, b = colorsys.hsv_to_rgb(hue % 1.0, 0.62, 1.0)
    return int(r * 255), int(g * 255), int(b * 255)


def parse_topic(topic: str, channels):
    """'<channel>/<id>/<kind>' -> (channel, id, kind) if channel is subscribed, else None."""
    head, sep, kind = topic.rpartition("/")
    channel, sep2, car_id = head.rpartition("/")
    if not sep or not sep2 or kind not in MSG_KINDS or not car_id or channel not in channels:
        return None
    return channel, car_id, kind


def handle_message(fleet, channels, topic: str, payload) -> bool:
    """Route one MQTT message into the fleet. Returns True if it was understood."""
    parsed = parse_topic(topic, channels)
    if parsed is None:
        return False
    channel, car_id, kind = parsed
    key = f"{channel}/{car_id}"
    if kind == "tilt":
        tilt = parse_tilt(payload)
        if tilt is None:
            return False
        fleet.update_tilt(key, *tilt)
        return True
    if payload in (b"offline", "offline"):
        fleet.remove(key)
        return True
    if payload in (b"online", "online"):
        fleet.touch(key)
        return True
    return False


def demo_tilt(t: float, index: int):
    """Scripted (roll, pitch) in degrees used by the demo bots."""
    return 28 * math.sin(t * 0.7 + index * 2.2), 18 + 12 * math.sin(t * 0.4 + index * 1.3)


# --- settings ---------------------------------------------------------------

@dataclass
class Settings:
    deadzone_deg: float = DEADZONE_DEG
    full_tilt_deg: float = FULL_TILT_DEG
    max_speed: float = MAX_SPEED
    trails: bool = True
    show_names: bool = True
    show_grid: bool = True

    @classmethod
    def from_dict(cls, data) -> "Settings":
        settings = cls()
        if not isinstance(data, dict):
            return settings
        for name, (lo, hi) in SETTING_BOUNDS.items():
            value = data.get(name)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
                setattr(settings, name, float(min(hi, max(lo, value))))
        for name in SETTING_FLAGS:
            if isinstance(data.get(name), bool):
                setattr(settings, name, data[name])
        settings.full_tilt_deg = max(settings.full_tilt_deg, settings.deadzone_deg + 5.0)
        return settings

    def to_dict(self) -> dict:
        return asdict(self)


# --- cars -------------------------------------------------------------------

@dataclass
class Car:
    id: str                    # key: "<channel>/<id>"
    x: float = 0.0
    y: float = 0.0
    heading: float = 0.0
    speed: float = 0.0
    throttle: float = 0.0      # [-1, 1], from pitch (tilt forward = accelerate)
    steer: float = 0.0         # [-1, 1], from roll (tilt right = steer right)
    last_seen: float = 0.0
    index: int = 0
    name: str = ""
    color: tuple = (255, 255, 255)
    source: str = "mqtt"       # "mqtt" | "bot" | "keyboard"
    frozen: bool = False
    roll: float = 0.0          # last raw tilt in degrees, for display
    pitch: float = 0.0

    @property
    def short_id(self) -> str:
        return self.id.rpartition("/")[2]

    @property
    def channel(self) -> str:
        return self.id.rpartition("/")[0]

    @property
    def label(self) -> str:
        return self.name or self.short_id


def _approach(value: float, target: float, step: float) -> float:
    if value < target:
        return min(value + step, target)
    return max(value - step, target)


def step_car(car: Car, dt: float, width: float, height: float, max_speed: float = MAX_SPEED) -> None:
    max_reverse = max_speed * MAX_REVERSE / MAX_SPEED
    target = car.throttle * (max_speed if car.throttle >= 0 else max_reverse)
    rate = ACCEL if car.throttle != 0 else FRICTION
    car.speed = _approach(car.speed, target, rate * dt)

    grip = max(-1.0, min(1.0, car.speed / TURN_SPEED_REF))
    car.heading += car.steer * TURN_RATE * grip * dt

    car.x = (car.x + math.cos(car.heading) * car.speed * dt) % width
    car.y = (car.y + math.sin(car.heading) * car.speed * dt) % height


def resolve_collisions(cars) -> None:
    min_dist = 2 * CAR_RADIUS
    for i, a in enumerate(cars):
        for b in cars[i + 1:]:
            dx, dy = b.x - a.x, b.y - a.y
            dist = math.hypot(dx, dy)
            if dist >= min_dist:
                continue
            if dist == 0:
                dx, dy, dist = 1.0, 0.0, 1.0
            nx, ny = dx / dist, dy / dist
            push = (min_dist - dist) / 2
            a.x -= nx * push
            a.y -= ny * push
            b.x += nx * push
            b.y += ny * push
            a.speed *= BUMP_DAMP
            b.speed *= BUMP_DAMP


# --- fleet ------------------------------------------------------------------

class Fleet:
    """All cars in the arena. MQTT threads write controls, the game loop steps."""

    def __init__(self, width, height, clock=time.monotonic, rng=None, settings=None, remembered=None):
        self.width = width
        self.height = height
        self.settings = settings or Settings()
        self.remembered: dict[str, dict] = remembered if remembered is not None else {}
        self._clock = clock
        self._rng = rng or random.Random()
        self._lock = threading.Lock()
        self._cars: dict[str, Car] = {}
        self._blocked: set[str] = set()
        self._next_index = 0

    # -- internals (call with the lock held) --

    def _place(self, car: Car) -> None:
        car.x = self._rng.uniform(SPAWN_MARGIN, max(SPAWN_MARGIN + 1, self.width - SPAWN_MARGIN))
        car.y = self._rng.uniform(SPAWN_MARGIN, max(SPAWN_MARGIN + 1, self.height - SPAWN_MARGIN))
        car.heading = self._rng.uniform(0, 2 * math.pi)

    def _ensure(self, key: str, source: str = "mqtt"):
        if key in self._blocked:
            return None
        car = self._cars.get(key)
        if car is None:
            index = self._next_index
            self._next_index += 1
            car = Car(id=key, index=index, source=source, color=PALETTE[index % len(PALETTE)])
            if source == "mqtt":
                saved = self.remembered.get(key, {})
                car.name = saved.get("name", "")
                car.color = saved.get("color", car.color)
            elif source == "bot":
                car.name = f"Bot {key.rpartition('-')[2]}"
            elif source == "keyboard":
                car.name = "Keyboard"
            self._place(car)
            self._cars[key] = car
        car.last_seen = self._clock()
        return car

    def _controls(self, car: Car, roll: float, pitch: float) -> None:
        s = self.settings
        car.roll, car.pitch = roll, pitch
        car.steer = tilt_axis(roll, s.deadzone_deg, s.full_tilt_deg)
        car.throttle = tilt_axis(pitch, s.deadzone_deg, s.full_tilt_deg)

    def _remember(self, car: Car, field: str, value) -> None:
        if car.source != "mqtt":
            return
        entry = self.remembered.setdefault(car.id, {})
        if value in (None, ""):
            entry.pop(field, None)
            if not entry:
                del self.remembered[car.id]
        else:
            entry[field] = value

    # -- input from devices --

    def touch(self, key: str) -> None:
        with self._lock:
            self._ensure(key)

    def update_tilt(self, key: str, roll: float, pitch: float) -> None:
        with self._lock:
            car = self._ensure(key)
            if car is not None:
                self._controls(car, roll, pitch)

    def set_controls(self, key: str, throttle: float, steer: float, source: str = "keyboard") -> None:
        with self._lock:
            car = self._ensure(key, source)
            if car is not None:
                car.throttle, car.steer = throttle, steer

    def remove(self, key: str) -> None:
        with self._lock:
            self._cars.pop(key, None)

    def drop_channel(self, channel: str) -> None:
        with self._lock:
            for key in [k for k, c in self._cars.items() if c.source == "mqtt" and c.channel == channel]:
                del self._cars[key]

    # -- controller actions --

    def rename(self, key: str, name: str) -> None:
        name = " ".join(str(name).split())[:MAX_NAME_LEN]
        with self._lock:
            car = self._cars.get(key)
            if car is not None:
                car.name = name
                self._remember(car, "name", name)

    def set_color(self, key: str, color) -> None:
        color = normalize_color(color)
        if color is None:
            return
        with self._lock:
            car = self._cars.get(key)
            if car is not None:
                car.color = color
                self._remember(car, "color", color)

    def set_frozen(self, key: str, frozen: bool) -> None:
        with self._lock:
            car = self._cars.get(key)
            if car is not None:
                car.frozen = frozen

    def block(self, key: str) -> None:
        with self._lock:
            self._blocked.add(key)
            self._cars.pop(key, None)

    def unblock(self, key: str) -> None:
        with self._lock:
            self._blocked.discard(key)

    @property
    def blocked(self) -> list:
        with self._lock:
            return sorted(self._blocked)

    def set_bots(self, count: int) -> None:
        count = max(0, min(MAX_BOTS, int(count)))
        wanted = {f"demo/bot-{i}" for i in range(1, count + 1)}
        with self._lock:
            for key in [k for k, c in self._cars.items() if c.source == "bot" and k not in wanted]:
                del self._cars[key]
            for key in sorted(wanted):
                self._ensure(key, "bot")

    def scatter(self) -> None:
        with self._lock:
            for car in self._cars.values():
                self._place(car)
                car.speed = 0.0

    def resize(self, width: float, height: float) -> None:
        with self._lock:
            self.width, self.height = width, height
            for car in self._cars.values():
                car.x %= width
                car.y %= height

    # -- simulation --

    def step(self, dt: float) -> None:
        with self._lock:
            now = self._clock()
            for key in [k for k, c in self._cars.items() if c.source != "bot" and now - c.last_seen > TIMEOUT_S]:
                del self._cars[key]
            cars = list(self._cars.values())
            for car in cars:
                if car.source == "bot":
                    car.last_seen = now
                    self._controls(car, *demo_tilt(now, car.index))
                if car.frozen:
                    car.speed = 0.0
                    continue
                step_car(car, dt, self.width, self.height, self.settings.max_speed)
            resolve_collisions(cars)

    def snapshot(self) -> list:
        with self._lock:
            return [replace(c) for c in self._cars.values()]
