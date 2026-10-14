import pytest

from carritos.core import Fleet
from carritos.link import Link


@pytest.fixture
def link():
    fleet = Fleet(800, 600)
    # Port 1 refuses connections; the link keeps retrying in the background, which is all we need here.
    lnk = Link(fleet, "127.0.0.1", 1, ["carritos", "bad channel", "carritos"])
    yield lnk
    lnk.close()


def test_initial_channels_are_normalized_and_deduplicated(link):
    assert link.channels == ["carritos"]


def test_add_channel_validates_and_ignores_duplicates(link):
    assert link.add_channel(" /lab/cars/ ") == "lab/cars"
    assert link.add_channel("lab/cars") == "lab/cars"
    assert link.add_channel("not valid!") is None
    assert link.channels == ["carritos", "lab/cars"]


def test_remove_channel_drops_its_cars_and_keeps_the_others(link):
    link.add_channel("b")
    link.fleet.update_tilt("carritos/1", 0, 0)
    link.fleet.update_tilt("b/1", 0, 0)
    link.remove_channel("b")
    assert link.channels == ["carritos"]
    assert [c.id for c in link.fleet.snapshot()] == ["carritos/1"]
    link.remove_channel("b")  # removing twice is harmless


def test_messages_are_routed_only_for_subscribed_channels(link):
    class Msg:
        def __init__(self, topic, payload):
            self.topic, self.payload = topic, payload

    link._on_message(None, None, Msg("carritos/esp32/tilt", b'{"roll": 1, "pitch": 2}'))
    link._on_message(None, None, Msg("other/esp32/tilt", b'{"roll": 1, "pitch": 2}'))
    assert [c.id for c in link.fleet.snapshot()] == ["carritos/esp32"]
    assert link.messages == 2
