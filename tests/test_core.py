import math

import pytest

from carritos import core
from carritos.core import Car, Fleet, parse_tilt, resolve_collisions, step_car, tilt_axis

W, H = 1000, 600


# --- tilt -> axis -----------------------------------------------------------

def test_tilt_inside_deadzone_is_zero():
    assert tilt_axis(0) == 0
    assert tilt_axis(core.DEADZONE_DEG) == 0
    assert tilt_axis(-core.DEADZONE_DEG + 0.1) == 0


def test_tilt_full_scale_and_clamp():
    assert tilt_axis(core.FULL_TILT_DEG) == pytest.approx(1.0)
    assert tilt_axis(90) == pytest.approx(1.0)
    assert tilt_axis(-90) == pytest.approx(-1.0)


def test_tilt_is_linear_between_deadzone_and_full():
    mid = (core.DEADZONE_DEG + core.FULL_TILT_DEG) / 2
    assert tilt_axis(mid) == pytest.approx(0.5)
    assert tilt_axis(-mid) == pytest.approx(-0.5)


# --- payload parsing --------------------------------------------------------

def test_parse_tilt_valid_bytes_and_str():
    assert parse_tilt(b'{"roll": 12.5, "pitch": -3}') == (12.5, -3.0)
    assert parse_tilt('{"roll": 1, "pitch": 2}') == (1.0, 2.0)


@pytest.mark.parametrize("payload", [
    b"", b"not json", b"[1, 2]", b"42", b'{"roll": 1}', b'{"roll": "x", "pitch": 2}',
    b'{"roll": NaN, "pitch": 0}', b'{"roll": Infinity, "pitch": 0}', b"\xff\xfe",
])
def test_parse_tilt_rejects_garbage(payload):
    assert parse_tilt(payload) is None


# --- car physics ------------------------------------------------------------

def test_full_throttle_accelerates_and_caps_at_max_speed():
    car = Car("a", x=500, y=300, heading=0.0, throttle=1.0)
    for _ in range(600):
        step_car(car, 1 / 60, W, H)
    assert car.speed == pytest.approx(core.MAX_SPEED)


def test_reverse_is_slower_than_forward():
    car = Car("a", x=500, y=300, throttle=-1.0)
    for _ in range(600):
        step_car(car, 1 / 60, W, H)
    assert car.speed == pytest.approx(-core.MAX_REVERSE)
    assert core.MAX_REVERSE < core.MAX_SPEED


def test_car_moves_along_its_heading():
    car = Car("a", x=500, y=300, heading=math.pi / 2, speed=100.0, throttle=1.0)
    step_car(car, 0.1, W, H)
    assert car.x == pytest.approx(500, abs=1e-6)
    assert car.y > 300


def test_steer_right_increases_heading_only_when_moving():
    moving = Car("a", x=500, y=300, speed=core.MAX_SPEED, throttle=1.0, steer=1.0)
    parked = Car("b", x=500, y=300, speed=0.0, throttle=0.0, steer=1.0)
    step_car(moving, 0.1, W, H)
    step_car(parked, 0.1, W, H)
    assert moving.heading > 0
    assert parked.heading == 0


def test_steering_flips_when_reversing():
    car = Car("a", x=500, y=300, speed=-core.MAX_REVERSE, throttle=-1.0, steer=1.0)
    step_car(car, 0.1, W, H)
    assert car.heading < 0


def test_coasts_to_a_stop_without_throttle():
    car = Car("a", x=500, y=300, speed=core.MAX_SPEED)
    for _ in range(600):
        step_car(car, 1 / 60, W, H)
    assert car.speed == 0


def test_arena_wraps_around():
    car = Car("a", x=W - 1, y=300, heading=0.0, speed=200.0, throttle=1.0)
    step_car(car, 0.1, W, H)
    assert 0 <= car.x < 50


def test_overlapping_cars_are_pushed_apart_and_slowed():
    a = Car("a", x=500, y=300, speed=200.0)
    b = Car("b", x=510, y=300, speed=200.0)
    resolve_collisions([a, b])
    assert math.hypot(b.x - a.x, b.y - a.y) == pytest.approx(2 * core.CAR_RADIUS)
    assert a.speed < 200.0 and b.speed < 200.0


def test_cars_at_same_position_do_not_crash():
    a = Car("a", x=500, y=300)
    b = Car("b", x=500, y=300)
    resolve_collisions([a, b])
    assert (a.x, a.y) != (b.x, b.y)


# --- fleet ------------------------------------------------------------------

class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def make_fleet():
    clock = FakeClock()
    return Fleet(W, H, clock=clock), clock


def test_first_tilt_spawns_a_car_with_mapped_controls():
    fleet, _ = make_fleet()
    fleet.update_tilt("esp32-1", roll=core.FULL_TILT_DEG, pitch=-core.FULL_TILT_DEG)
    (car,) = fleet.snapshot()
    assert car.id == "esp32-1"
    assert car.steer == pytest.approx(1.0)
    assert car.throttle == pytest.approx(-1.0)


def test_n_cars_can_join_with_distinct_indexes():
    fleet, _ = make_fleet()
    for i in range(8):
        fleet.update_tilt(f"esp32-{i}", 0, 0)
    cars = fleet.snapshot()
    assert len(cars) == 8
    assert len({c.index for c in cars}) == 8


def test_silent_car_is_dropped_after_timeout():
    fleet, clock = make_fleet()
    fleet.update_tilt("a", 0, 0)
    clock.t = core.TIMEOUT_S - 0.1
    fleet.step(0.016)
    assert len(fleet.snapshot()) == 1
    clock.t = core.TIMEOUT_S + 0.1
    fleet.step(0.016)
    assert fleet.snapshot() == []


def test_remove_drops_the_car_and_is_idempotent():
    fleet, _ = make_fleet()
    fleet.update_tilt("a", 0, 0)
    fleet.remove("a")
    fleet.remove("a")
    assert fleet.snapshot() == []


def test_snapshot_returns_copies():
    fleet, _ = make_fleet()
    fleet.update_tilt("a", 0, 0)
    fleet.snapshot()[0].x = -999
    assert fleet.snapshot()[0].x != -999
