"""Fleet customization, channels, bots, blocking and message routing."""
import pytest

from carritos import core
from carritos.core import (
    Car,
    Fleet,
    Settings,
    handle_message,
    normalize_channel,
    normalize_color,
    parse_topic,
    step_car,
)

W, H = 1000, 600


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def make_fleet(**kwargs):
    clock = FakeClock()
    return Fleet(W, H, clock=clock, **kwargs), clock


def only(fleet):
    (car,) = fleet.snapshot()
    return car


# --- channels & topics ------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("carritos", "carritos"),
    ("  /lab/cars/ ", "lab/cars"),
    ("a-b_c/D9", "a-b_c/D9"),
])
def test_normalize_channel_accepts(raw, expected):
    assert normalize_channel(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "a b", "a/+", "#", "a//b", "x" * 49, None, 7, "ñ"])
def test_normalize_channel_rejects(raw):
    assert normalize_channel(raw) is None


def test_parse_topic_single_and_multi_level_channels():
    assert parse_topic("carritos/esp32-1/tilt", ["carritos"]) == ("carritos", "esp32-1", "tilt")
    assert parse_topic("lab/cars/x/status", ["lab/cars"]) == ("lab/cars", "x", "status")


@pytest.mark.parametrize("topic", [
    "other/esp32-1/tilt",        # channel not subscribed
    "carritos/esp32-1/speed",    # unknown kind
    "carritos/tilt",             # missing id
    "carritos/a/b/tilt",         # extra level, 'carritos/a' is not a channel
    "tilt",
    "",
])
def test_parse_topic_rejects(topic):
    assert parse_topic(topic, ["carritos"]) is None


def test_handle_message_tilt_spawns_car_keyed_by_channel_and_id():
    fleet, _ = make_fleet()
    assert handle_message(fleet, ["carritos"], "carritos/esp32-1/tilt", b'{"roll": 30, "pitch": 30}')
    car = only(fleet)
    assert car.id == "carritos/esp32-1"
    assert car.short_id == "esp32-1" and car.channel == "carritos"
    assert car.roll == 30 and car.pitch == 30


def test_same_id_on_two_channels_makes_two_cars():
    fleet, _ = make_fleet()
    handle_message(fleet, ["a", "b"], "a/x/tilt", b'{"roll": 0, "pitch": 0}')
    handle_message(fleet, ["a", "b"], "b/x/tilt", b'{"roll": 0, "pitch": 0}')
    assert sorted(c.id for c in fleet.snapshot()) == ["a/x", "b/x"]


def test_handle_message_ignores_bad_payload_and_unknown_topics():
    fleet, _ = make_fleet()
    assert not handle_message(fleet, ["carritos"], "carritos/x/tilt", b"garbage")
    assert not handle_message(fleet, ["carritos"], "nope/x/tilt", b'{"roll": 0, "pitch": 0}')
    assert fleet.snapshot() == []


def test_handle_message_status_online_and_offline():
    fleet, _ = make_fleet()
    assert handle_message(fleet, ["c"], "c/x/status", b"online")
    assert len(fleet.snapshot()) == 1
    assert handle_message(fleet, ["c"], "c/x/status", b"offline")
    assert fleet.snapshot() == []
    assert not handle_message(fleet, ["c"], "c/x/status", b"???")


def test_drop_channel_removes_only_its_cars():
    fleet, _ = make_fleet()
    for key in ("a/1", "a/2", "b/1"):
        fleet.update_tilt(key, 0, 0)
    fleet.drop_channel("a")
    assert [c.id for c in fleet.snapshot()] == ["b/1"]


# --- settings ---------------------------------------------------------------

def test_settings_change_tilt_sensitivity():
    fleet, _ = make_fleet(settings=Settings(deadzone_deg=2, full_tilt_deg=20))
    fleet.update_tilt("a", roll=20, pitch=0)
    assert only(fleet).steer == pytest.approx(1.0)


def test_settings_from_dict_clamps_and_ignores_garbage():
    s = Settings.from_dict({"max_speed": 99999, "deadzone_deg": -4, "full_tilt_deg": "x", "trails": "yes"})
    assert s.max_speed == 600.0
    assert s.deadzone_deg == 0.0
    assert s.full_tilt_deg == core.FULL_TILT_DEG
    assert s.trails is True
    assert Settings.from_dict(None) == Settings()


def test_settings_keep_full_tilt_above_deadzone():
    s = Settings.from_dict({"deadzone_deg": 15, "full_tilt_deg": 15})
    assert s.full_tilt_deg >= s.deadzone_deg + 5


def test_top_speed_setting_caps_speed_and_scales_reverse():
    fwd = Car("a", x=500, y=300, throttle=1.0)
    rev = Car("b", x=500, y=300, throttle=-1.0)
    for _ in range(600):
        step_car(fwd, 1 / 60, W, H, max_speed=200.0)
        step_car(rev, 1 / 60, W, H, max_speed=200.0)
    assert fwd.speed == pytest.approx(200.0)
    assert rev.speed == pytest.approx(-200.0 * core.MAX_REVERSE / core.MAX_SPEED)


# --- appearance: names & colors ---------------------------------------------

def test_default_color_cycles_through_palette_and_label_falls_back_to_id():
    fleet, _ = make_fleet()
    for i in range(len(core.PALETTE) + 1):
        fleet.update_tilt(f"c/{i}", 0, 0)
    cars = sorted(fleet.snapshot(), key=lambda c: c.index)
    assert cars[0].color == core.PALETTE[0]
    assert cars[-1].color == core.PALETTE[0]
    assert cars[3].label == "3"


def test_rename_sanitizes_and_is_remembered():
    fleet, _ = make_fleet()
    fleet.update_tilt("c/x", 0, 0)
    fleet.rename("c/x", "   Super    Car   with a really long name   ")
    car = only(fleet)
    assert car.name == "Super Car with a rea"
    assert len(car.name) == core.MAX_NAME_LEN
    assert fleet.remembered["c/x"]["name"] == car.name


def test_empty_rename_clears_the_name():
    fleet, _ = make_fleet()
    fleet.update_tilt("c/x", 0, 0)
    fleet.rename("c/x", "Hello")
    fleet.rename("c/x", "   ")
    assert only(fleet).label == "x"
    assert "name" not in fleet.remembered.get("c/x", {})


def test_set_color_validates_and_is_remembered():
    fleet, _ = make_fleet()
    fleet.update_tilt("c/x", 0, 0)
    fleet.set_color("c/x", (10, 20, 30))
    fleet.set_color("c/x", (999, 0, 0))   # invalid -> ignored
    assert only(fleet).color == (10, 20, 30)
    assert fleet.remembered["c/x"]["color"] == (10, 20, 30)


def test_remembered_appearance_is_applied_when_a_car_returns():
    fleet, _ = make_fleet(remembered={"c/x": {"name": "Zed", "color": (1, 2, 3)}})
    fleet.update_tilt("c/x", 0, 0)
    car = only(fleet)
    assert car.label == "Zed" and car.color == (1, 2, 3)


@pytest.mark.parametrize("value,expected", [
    ((1, 2, 3), (1, 2, 3)), ([255, 0, 128], (255, 0, 128)),
    ((256, 0, 0), None), ((-1, 0, 0), None), ((1, 2), None), ("red", None), ((True, 0, 0), None), (None, None),
])
def test_normalize_color(value, expected):
    assert normalize_color(value) == expected


# --- moderation: freeze & block ---------------------------------------------

def test_frozen_car_does_not_move_but_can_resume():
    fleet, clock = make_fleet()
    fleet.update_tilt("c/x", 0, 30)
    before = only(fleet)
    fleet.set_frozen("c/x", True)
    for _ in range(30):
        clock.t += 1 / 60
        fleet.update_tilt("c/x", 0, 30)
        fleet.step(1 / 60)
    frozen = only(fleet)
    assert (frozen.x, frozen.y) == (before.x, before.y) and frozen.frozen
    fleet.set_frozen("c/x", False)
    for _ in range(30):
        fleet.step(1 / 60)
    assert only(fleet).speed > 0


def test_block_removes_and_ignores_until_unblocked():
    fleet, _ = make_fleet()
    fleet.update_tilt("c/x", 0, 0)
    fleet.block("c/x")
    assert fleet.snapshot() == [] and fleet.blocked == ["c/x"]
    fleet.update_tilt("c/x", 0, 0)
    fleet.touch("c/x")
    assert fleet.snapshot() == []
    fleet.unblock("c/x")
    fleet.update_tilt("c/x", 0, 0)
    assert len(fleet.snapshot()) == 1 and fleet.blocked == []


# --- demo bots & keyboard car -----------------------------------------------

def test_bots_are_created_removed_and_clamped():
    fleet, _ = make_fleet()
    fleet.set_bots(3)
    assert sorted(c.id for c in fleet.snapshot()) == ["demo/bot-1", "demo/bot-2", "demo/bot-3"]
    assert all(c.source == "bot" for c in fleet.snapshot())
    fleet.set_bots(1)
    assert [c.id for c in fleet.snapshot()] == ["demo/bot-1"]
    fleet.set_bots(999)
    assert len(fleet.snapshot()) == core.MAX_BOTS
    fleet.set_bots(-5)
    assert fleet.snapshot() == []


def test_bots_are_never_pruned_and_they_drive():
    fleet, clock = make_fleet()
    fleet.set_bots(2)
    for _ in range(300):
        clock.t += 0.05
        fleet.step(0.05)
    cars = fleet.snapshot()
    assert len(cars) == 2 and all(abs(c.speed) > 0 for c in cars)


def test_bots_are_not_remembered_in_the_profile():
    fleet, _ = make_fleet()
    fleet.set_bots(1)
    fleet.rename("demo/bot-1", "Robo")
    fleet.set_color("demo/bot-1", (1, 2, 3))
    assert fleet.remembered == {}


def test_set_controls_creates_a_keyboard_car():
    fleet, _ = make_fleet()
    fleet.set_controls("local/keyboard", throttle=1.0, steer=-1.0)
    car = only(fleet)
    assert car.source == "keyboard" and car.label == "Keyboard"
    assert (car.throttle, car.steer) == (1.0, -1.0)


# --- misc -------------------------------------------------------------------

def test_scatter_stops_and_repositions_cars():
    fleet, _ = make_fleet()
    fleet.update_tilt("c/x", 0, 30)
    for _ in range(30):
        fleet.step(1 / 60)
    fleet.scatter()
    assert only(fleet).speed == 0


def test_resize_updates_the_arena_bounds():
    fleet, _ = make_fleet()
    fleet.resize(400, 300)
    fleet.update_tilt("c/x", 0, 30)
    for _ in range(200):
        fleet.step(1 / 60)
    car = only(fleet)
    assert 0 <= car.x < 400 and 0 <= car.y < 300
