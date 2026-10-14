"""Headless UI smoke tests: drive the real App with synthetic pygame events."""
import types

import pygame
import pytest

from carritos import app as app_mod
from carritos import profile


def make_app(tmp_path, bots=0):
    args = types.SimpleNamespace(
        host="127.0.0.1", port=1, user=None, password=None, channel=["carritos"], bots=bots,
        profile=str(tmp_path / "profile.json"), width=1360, height=820, max_frames=0,
    )
    return app_mod.App(args)


@pytest.fixture
def make(tmp_path):
    apps = []

    def factory(bots=0):
        a = make_app(tmp_path, bots)
        apps.append(a)
        return a

    yield factory
    for a in apps:
        a.shutdown()


def click(app, pos):
    app.frame(1 / 60, [pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=pos)])


def typed(app, text):
    app.frame(1 / 60, [pygame.event.Event(pygame.TEXTINPUT, text=text)])


def press(app, key):
    app.frame(1 / 60, [pygame.event.Event(pygame.KEYDOWN, key=key)])


def first_card_center(app):
    return app.sidebar.x + 12 + 100, app.sidebar.y + 60 + 30


def test_every_tab_renders_with_and_without_a_selection(make):
    app = make(bots=4)
    for tab in range(3):
        app.tab = tab
        for selected in (None, "demo/bot-2"):
            app.selected = selected
            for _ in range(5):
                app.frame(1 / 60, [])


def test_empty_arena_renders(make):
    app = make(bots=0)
    for _ in range(5):
        app.frame(1 / 60, [])


def test_clicking_a_card_selects_and_clicking_again_deselects(make):
    app = make(bots=3)
    app.frame(1 / 60, [])
    click(app, first_card_center(app))
    assert app.selected == "demo/bot-1"
    click(app, first_card_center(app))
    assert app.selected is None


def test_clicking_a_car_in_the_arena_selects_it(make):
    app = make(bots=1)
    app.frame(1 / 60, [])
    (car,) = app.fleet.snapshot()
    click(app, (app.arena.x + int(car.x), app.arena.y + int(car.y)))
    assert app.selected == "demo/bot-1"
    click(app, (app.arena.x + 2, app.arena.y + 2))  # empty corner
    assert app.selected is None


def test_renaming_through_the_inspector_updates_the_car_and_persists(make, tmp_path):
    app = make(bots=0)
    app.fleet.update_tilt("carritos/esp32-x", 0, 0)
    app.frame(1 / 60, [])
    click(app, first_card_center(app))
    assert app.selected == "carritos/esp32-x"

    inspector_y = app.sidebar.bottom - 12 - app_mod.INSPECTOR_H
    click(app, (app.sidebar.x + 12 + 60, inspector_y + 12 + 22 + 17))  # name field
    assert app.ui.typing
    typed(app, "Zed")
    assert app.fleet.snapshot()[0].name == "Zed"
    press(app, pygame.K_RETURN)
    assert not app.ui.typing

    app.shutdown()
    app.link = types.SimpleNamespace(close=lambda: None, channels=[])  # already closed; avoid double close
    saved = profile.load(tmp_path / "profile.json")
    assert saved["cars"]["carritos/esp32-x"]["name"] == "Zed"


def test_typing_in_the_name_field_does_not_drive_the_keyboard_car(make):
    app = make(bots=0)
    app.kb_enabled = True
    app.fleet.update_tilt("carritos/x", 0, 0)
    app.frame(1 / 60, [])
    click(app, first_card_center(app))
    inspector_y = app.sidebar.bottom - 12 - app_mod.INSPECTOR_H
    click(app, (app.sidebar.x + 12 + 60, inspector_y + 12 + 22 + 17))
    assert app.ui.typing
    for _ in range(5):
        app.frame(1 / 60, [])
    kb = [c for c in app.fleet.snapshot() if c.source == "keyboard"][0]
    assert kb.throttle == 0 and kb.steer == 0


def test_tab_key_hides_the_panel_and_resizes_the_arena(make):
    app = make(bots=1)
    app.frame(1 / 60, [])
    width_with_panel = app.arena.w
    app.show_sidebar = False
    app._relayout()
    assert app.sidebar is None and app.arena.w > width_with_panel
    app.frame(1 / 60, [])
