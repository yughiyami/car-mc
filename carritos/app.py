"""Desktop arena: one car per MQTT device, with a control panel to manage them."""
import argparse
import colorsys
import os
import time
from pathlib import Path

import pygame

from . import profile
from .core import PALETTE, Fleet, Settings, hue_color
from .link import Link
from .render import ArenaView, make_car_sprite
from .ui import (ACCENT, BG, BORDER, CARD, CARD_HOVER, DANGER, DIM, MUTED, OK, PANEL, TEXT, WARN, Fonts, UI,
                 clamp, mix)

DEFAULT_CHANNEL = "carritos"
KB_KEY = "local/keyboard"
TABS = ("Fleet", "Channels", "Settings")
TOP = 68
MARGIN = 16
SIDEBAR_W = 344
CARD_H = 62
GAP = 8
INSPECTOR_H = 316


class App:
    def __init__(self, args):
        os.environ.setdefault("SDL_VIDEO_CENTERED", "1")
        pygame.init()
        pygame.display.set_caption("Carritos MC")
        self.screen = pygame.display.set_mode((max(480, args.width), max(480, args.height)), pygame.RESIZABLE)
        pygame.key.set_repeat(350, 35)
        self.clock = pygame.time.Clock()
        self.fonts = Fonts()
        self.ui = UI(self.fonts)
        self.view = ArenaView(self.fonts)
        self.args = args

        icon = make_car_sprite(ACCENT)
        pygame.display.set_icon(icon)
        self.logo = pygame.transform.smoothscale(icon, (45, 25))

        self.profile_path = Path(args.profile)
        data = profile.load(self.profile_path)
        self.fleet = Fleet(800, 600, settings=Settings.from_dict(data["settings"]), remembered=data["cars"])
        channels = args.channel or data["channels"] or [DEFAULT_CHANNEL]
        self.link = Link(self.fleet, args.host, args.port, channels, args.user, args.password)

        self.tab = 0
        self.show_sidebar = True
        self.selected = None
        self.name_text = ""
        self._name_key = None
        self.channel_text = ""
        self.channel_error = ""
        self.scroll = {}
        self._heights = {}
        self.kb_enabled = False
        self.kb_thr = 0.0
        self.kb_steer = 0.0
        self.dirty = False
        self._last_save = time.monotonic()
        self._rate = 0.0
        self._rate_t = time.monotonic()
        self._rate_n = 0
        self.size = (0, 0)
        self._relayout()
        if args.bots:
            self.fleet.set_bots(args.bots)

    # -- layout --

    def _relayout(self) -> None:
        self.screen = pygame.display.get_surface()
        w, h = self.screen.get_size()
        if w < 480 or h < 480:
            self.screen = pygame.display.set_mode((max(480, w), max(480, h)), pygame.RESIZABLE)
            w, h = self.screen.get_size()
        self.size = (w, h)
        available_h = max(64, h - TOP - MARGIN)
        if self.show_sidebar and w < 850:
            panel_h = min(360, max(260, int(available_h * 0.58)))
            self.arena = pygame.Rect(MARGIN, TOP, max(64, w - 2 * MARGIN), max(64, available_h - panel_h - 12))
            self.sidebar = pygame.Rect(MARGIN, self.arena.bottom + 12, self.arena.w, panel_h)
        else:
            sw = SIDEBAR_W if self.show_sidebar else 0
            gap = MARGIN if sw else 0
            self.arena = pygame.Rect(MARGIN, TOP, max(64, w - 2 * MARGIN - sw - gap), available_h)
            self.sidebar = pygame.Rect(w - MARGIN - sw, TOP, sw, available_h) if sw else None
        self.fleet.resize(self.arena.w, self.arena.h)

    # -- main loop --

    def run(self) -> None:
        running, frames = True, 0
        while running:
            dt = min(self.clock.tick(60) / 1000.0, 0.05)
            events = pygame.event.get()
            for e in events:
                if e.type == pygame.QUIT:
                    running = False
            self.frame(dt, events)
            pygame.display.flip()
            frames += 1
            if self.args.max_frames and frames >= self.args.max_frames:
                running = False
        self.shutdown()

    def shutdown(self) -> None:
        self._save()
        self.link.close()
        pygame.quit()

    def frame(self, dt, events) -> None:
        self.screen = pygame.display.get_surface()
        if self.screen.get_size() != self.size:
            self._relayout()
        ui = self.ui
        ui.begin(self.screen, events)
        self._shortcuts()
        self._drive_keyboard_car(dt)
        self.fleet.step(dt)
        cars = sorted(self.fleet.snapshot(), key=lambda c: c.index)
        by_key = {c.id: c for c in cars}

        if self.selected not in by_key:
            self.selected = None
        if self.selected != self._name_key:
            self._name_key = self.selected
            self.name_text = by_key[self.selected].name if self.selected else ""

        if ui.clicked and self.arena.collidepoint(ui.mouse):
            local = (ui.mouse[0] - self.arena.x, ui.mouse[1] - self.arena.y)
            self.selected = self.view.pick(cars, local)
            ui.focus = None
            if self.selected:
                self.tab = 0

        self._update_rate()
        self.screen.fill(BG)
        self._draw_topbar(cars)
        self.view.set_grid(self.fleet.settings.show_grid)
        self.view.draw(self.screen, self.arena, cars, self.fleet.settings, self.selected, time.monotonic())
        if self.sidebar:
            self._draw_sidebar(cars, by_key)
        ui.end()
        self._autosave()

    def _shortcuts(self) -> None:
        if self.ui.typing:
            return
        for key in self.ui.keys:
            if key == pygame.K_ESCAPE:
                if self.selected:
                    self.selected = None
                else:
                    pygame.event.post(pygame.event.Event(pygame.QUIT))
            elif key == pygame.K_TAB:
                self.show_sidebar = not self.show_sidebar
                self.ui.focus = self.ui.active = None
                self._relayout()
            elif key == pygame.K_F11:
                pygame.display.toggle_fullscreen()
            elif key == pygame.K_b:
                bots = any(c.source == "bot" for c in self.fleet.snapshot())
                self.fleet.set_bots(0 if bots else 4)
            elif key == pygame.K_k:
                self._toggle_keyboard()
            elif key == pygame.K_SPACE and self.selected:
                car = next((c for c in self.fleet.snapshot() if c.id == self.selected), None)
                if car:
                    self.fleet.set_frozen(car.id, not car.frozen)

    def _toggle_keyboard(self) -> None:
        self.kb_enabled = not self.kb_enabled
        self.kb_thr = self.kb_steer = 0.0
        if not self.kb_enabled:
            self.fleet.remove(KB_KEY)

    def _update_rate(self) -> None:
        now = time.monotonic()
        if now - self._rate_t >= 0.5:
            total = self.link.messages
            self._rate = (total - self._rate_n) / (now - self._rate_t)
            self._rate_n, self._rate_t = total, now

    def _drive_keyboard_car(self, dt) -> None:
        if not self.kb_enabled:
            return
        thr = steer = 0
        if not self.ui.typing:
            k = pygame.key.get_pressed()
            thr = int(k[pygame.K_w] or k[pygame.K_UP]) - int(k[pygame.K_s] or k[pygame.K_DOWN])
            steer = int(k[pygame.K_d] or k[pygame.K_RIGHT]) - int(k[pygame.K_a] or k[pygame.K_LEFT])
        self.kb_thr += clamp(thr - self.kb_thr, -5 * dt, 5 * dt)
        self.kb_steer += clamp(steer - self.kb_steer, -6 * dt, 6 * dt)
        self.fleet.set_controls(KB_KEY, self.kb_thr, self.kb_steer)

    # -- persistence --

    def _autosave(self) -> None:
        if self.dirty and time.monotonic() - self._last_save > 1.0:
            self._save()

    def _save(self) -> None:
        self._last_save = time.monotonic()
        self.dirty = False
        try:
            profile.save(self.profile_path, self.link.channels, self.fleet.remembered, self.fleet.settings)
        except OSError as err:
            print(f"could not save profile: {err}")

    # -- top bar --

    def _draw_topbar(self, cars) -> None:
        ui, f = self.ui, self.fonts
        self.screen.blit(self.logo, (MARGIN + 2, 19))
        ui.text("CARRITOS / MC", (MARGIN + 60, 11), f.title)
        ui.text("LIVE CONTROL  /  TILT ARENA", (MARGIN + 61, 38), f.small, MUTED)

        state = self.link.state
        color = OK if state == "connected" else (DANGER if state.startswith("refused") else WARN)
        x = self.size[0] - MARGIN
        if ui.button((x - 112, 15, 112, 34), "Hide panel" if self.show_sidebar else "Show panel",
                     active=self.show_sidebar):
            self.show_sidebar = not self.show_sidebar
            ui.focus = ui.active = None
            self._relayout()
        x -= 124
        if self.size[0] < 850:
            return
        rect = ui.pill((x, 32), ui.fit(f"{self.link.host}:{self.link.port}  ·  {state}", f.small, max(120, self.size[0] - 754)), f.small, TEXT, CARD, dot=color,
                       pad=(12, 6), anchor="midright")
        x = rect.left - 8
        rect = ui.pill((x, 32), f"{len(cars)} {'car' if len(cars) == 1 else 'cars'}", f.small, TEXT, CARD,
                       pad=(12, 6), anchor="midright")
        x = rect.left - 8
        rect = ui.pill((x, 32), f"{self._rate:.0f} msg/s", f.small, MUTED, CARD, pad=(12, 6), anchor="midright")

    # -- sidebar --

    def _draw_sidebar(self, cars, by_key) -> None:
        ui, r = self.ui, self.sidebar
        ui.box(r, PANEL, 16, BORDER)
        tab_w = (r.w - 24 - 12) // 3
        y = r.y + 12
        for i, name in enumerate(TABS):
            label = f"Fleet · {len(cars)}" if name == "Fleet" else name
            if ui.button((r.x + 12 + i * (tab_w + 6), y, tab_w, 34), label, active=i == self.tab):
                self.tab = i
                ui.focus = None
        area = pygame.Rect(r.x + 12, y + 48, r.w - 24, r.bottom - (y + 48) - 12)
        if self.tab == 0:
            self._tab_fleet(area, cars, by_key)
        elif self.tab == 1:
            self._scrolled(area, "channels", lambda a: self._tab_channels(a, cars))
        else:
            self._scrolled(area, "settings", self._tab_settings)

    def _scrolled(self, area, key, draw) -> None:
        """Run draw(shifted_area) inside a clipped, wheel-scrollable region."""
        ui = self.ui
        max_scroll = max(0, self._heights.get(key, 0) - area.h)
        if ui.hit(area) and ui.wheel:
            self.scroll[key] = clamp(self.scroll.get(key, 0) - ui.wheel * 50, 0, max_scroll)
        offset = clamp(self.scroll.get(key, 0), 0, max_scroll)
        self.scroll[key] = offset
        ui.push_clip(area)
        height = draw(pygame.Rect(area.x, area.y - offset, area.w - (10 if max_scroll else 0), area.h))
        ui.pop_clip()
        self._heights[key] = height
        if max_scroll:
            bar_h = max(30, int(area.h * area.h / height))
            bar_y = area.y + int(offset / max_scroll * (area.h - bar_h))
            pygame.draw.rect(ui.surface, BORDER, (area.right - 4, bar_y, 4, bar_h), border_radius=2)

    # -- fleet tab --

    def _tab_fleet(self, area, cars, by_key) -> None:
        car = by_key.get(self.selected)
        if car:
            inspector_h = min(INSPECTOR_H, max(100, area.h - 90))
            list_area = pygame.Rect(area.x, area.y, area.w, max(0, area.h - inspector_h - 10))
            self._scrolled(list_area, "fleet", lambda a: self._car_list(a, cars))
            inspector_area = pygame.Rect(area.x, area.bottom - inspector_h, area.w, inspector_h)
            self._scrolled(inspector_area, "inspector", lambda a: self._draw_inspector_content(a, car))
        else:
            self._scrolled(area, "fleet", lambda a: self._car_list(a, cars))

    def _draw_inspector_content(self, area, car) -> int:
        self._inspector(pygame.Rect(area.x, area.y, area.w, INSPECTOR_H), car)
        return INSPECTOR_H

    def _car_list(self, area, cars) -> int:
        ui, f = self.ui, self.fonts
        if not cars:
            cx = area.centerx
            ui.text("No cars yet", (cx, area.y + 60), f.h2, TEXT, "midtop")
            ui.text("Publish tilt to", (cx, area.y + 92), f.small, MUTED, "midtop")
            channel = (self.link.channels or [DEFAULT_CHANNEL])[0]
            ui.pill((cx, area.y + 112), f"{channel}/<id>/tilt", f.small, ACCENT, (28, 36, 62), anchor="midtop")
            if ui.button((area.x + 12, area.y + 154, area.w - 24, 36), "Launch demo fleet  [B]", "primary"):
                self.fleet.set_bots(4)
            if ui.button((area.x + 12, area.y + 200, area.w - 24, 36), "Drive with keyboard  [K]"):
                self._toggle_keyboard()
            return 250
        for i, car in enumerate(cars):
            self._card(pygame.Rect(area.x, area.y + i * (CARD_H + GAP), area.w, CARD_H), car)
        return len(cars) * (CARD_H + GAP) - GAP

    def _card(self, rect, car) -> None:
        ui, f, s = self.ui, self.fonts, self.fleet.settings
        selected = car.id == self.selected
        hover = ui.hit(rect)
        ui.box(rect, CARD_HOVER if hover else CARD, 12, ACCENT if selected else BORDER, 2 if selected else 1)
        c = (rect.x + 26, rect.y + 29)
        pygame.draw.circle(ui.surface, mix(car.color, CARD, 0.72), c, 17)
        pygame.draw.circle(ui.surface, car.color, c, 11)

        text_w = rect.w - 52 - 84
        ui.text(ui.fit(car.label, f.bold, text_w), (rect.x + 52, rect.y + 10), f.bold)
        if car.frozen:
            ui.text("paused", (rect.x + 52, rect.y + 30), f.small, WARN)
        else:
            sub = {"mqtt": car.channel, "bot": "demo bot", "keyboard": "keyboard"}[car.source]
            if car.source == "mqtt" and car.name:
                sub = f"{car.channel} · {car.short_id}"
            ui.text(ui.fit(sub, f.small, text_w), (rect.x + 52, rect.y + 30), f.small, MUTED)
        ui.text(f"{abs(car.speed):.0f}", (rect.right - 14, rect.y + 8), f.h2, TEXT, "topright")
        ui.text("px/s", (rect.right - 14, rect.y + 27), f.tiny, DIM, "topright")
        ui.bar((rect.x + 52, rect.bottom - 13, rect.w - 66, 4), abs(car.speed) / s.max_speed, car.color)
        if hover and ui.clicked:
            self.selected = None if selected else car.id
            ui.focus = None

    def _inspector(self, area, car) -> None:
        ui, f, fleet = self.ui, self.fonts, self.fleet
        ui.box(area, (26, 34, 32), 14, BORDER)
        x, w, y = area.x + 14, area.w - 28, area.y + 12
        ui.text("VEHICLE / TELEMETRY", (x, y + 3), f.tiny, DIM)
        if ui.button((area.right - 14 - 26, y - 2, 26, 22), "×", "ghost"):
            self.selected = None
            return
        y += 22
        text, _ = ui.text_input("name", (x, y, w, 34), self.name_text, placeholder=car.short_id)
        if text != self.name_text:
            self.name_text = text
            fleet.rename(car.id, text)
            self.dirty = True
        y += 46

        ui.text("COLOR", (x, y), f.tiny, DIM)
        y += 20
        step = w / len(PALETTE)
        for i, color in enumerate(PALETTE):
            if ui.swatch((int(x + step * (i + 0.5)), y + 10), 9, color, car.color == color):
                fleet.set_color(car.id, color)
                self.dirty = True
        y += 30
        hue = colorsys.rgb_to_hsv(*[v / 255 for v in car.color])[0]
        new_hue = ui.slider("hue", (x, y, w, 30), hue, 0.0, 1.0, "Custom hue", "", rainbow=True)
        if ui.active == "hue" and abs(new_hue - hue) > 1e-4:
            fleet.set_color(car.id, hue_color(new_hue))
            self.dirty = True
        y += 44

        self._tilt_pad(pygame.Rect(x, y, 84, 84), car)
        s = fleet.settings
        rows = [("Roll", f"{car.roll:+.0f}°"), ("Pitch", f"{car.pitch:+.0f}°"), ("Speed", f"{abs(car.speed):.0f} px/s")]
        if car.source == "keyboard":
            rows[0], rows[1] = ("Steer", f"{car.steer:+.2f}"), ("Throttle", f"{car.throttle:+.2f}")
        for i, (k, v) in enumerate(rows):
            ui.text(k, (x + 102, y + 8 + i * 26), f.small, MUTED)
            ui.text(v, (x + w, y + 6 + i * 26), f.bold, TEXT, "topright")
        y += 96

        btn_w = (w - 8) // 2 if car.source == "mqtt" else w
        if ui.button((x, y, btn_w, 34), "Resume" if car.frozen else "Pause", active=car.frozen):
            fleet.set_frozen(car.id, not car.frozen)
        if car.source == "mqtt" and ui.button((x + btn_w + 8, y, btn_w, 34), "Kick", "danger"):
            fleet.block(car.id)
            self.selected = None
            self.dirty = True

    def _tilt_pad(self, rect, car) -> None:
        ui, s = self.ui, self.fleet.settings
        ui.box(rect, (16, 18, 25), 12, BORDER)
        cx, cy = rect.center
        pygame.draw.line(ui.surface, BORDER, (rect.x + 8, cy), (rect.right - 8, cy))
        pygame.draw.line(ui.surface, BORDER, (cx, rect.y + 8), (cx, rect.bottom - 8))
        reach = rect.w / 2 - 12
        dz = reach * s.deadzone_deg / s.full_tilt_deg
        pygame.draw.rect(ui.surface, BORDER, (cx - dz, cy - dz, dz * 2, dz * 2), 1, border_radius=3)
        roll, pitch = car.roll, car.pitch
        if car.source == "keyboard":
            roll, pitch = car.steer * s.full_tilt_deg, car.throttle * s.full_tilt_deg
        dx = clamp(roll / s.full_tilt_deg, -1, 1) * reach
        dy = clamp(-pitch / s.full_tilt_deg, -1, 1) * reach
        pygame.draw.circle(ui.surface, mix(car.color, (16, 18, 25), 0.6), (cx + dx, cy + dy), 11)
        pygame.draw.circle(ui.surface, car.color, (cx + dx, cy + dy), 6)

    # -- channels tab --

    def _tab_channels(self, area, cars) -> int:
        ui, f, link = self.ui, self.fonts, self.link
        x, w, y = area.x, area.w, area.y

        card = pygame.Rect(x, y, w, 76)
        ui.box(card, CARD, 12, BORDER)
        ui.text("BROKER", (card.x + 14, card.y + 10), f.tiny, DIM)
        state = link.state
        color = OK if state == "connected" else (DANGER if state.startswith("refused") else WARN)
        pygame.draw.circle(ui.surface, color, (card.x + 20, card.y + 41), 5)
        ui.text(ui.fit(f"{link.host}:{link.port}", f.bold, w - 130), (card.x + 34, card.y + 32), f.bold)
        ui.text(state, (card.x + 34, card.y + 52), f.small, MUTED)
        ui.text(f"{self._rate:.0f}", (card.right - 14, card.y + 22), f.title, TEXT, "topright")
        ui.text("msg/s", (card.right - 14, card.y + 52), f.small, DIM, "topright")
        y += 92

        ui.text("CHANNELS", (x, y), f.tiny, DIM)
        y += 22
        for channel in link.channels:
            row = pygame.Rect(x, y, w, 46)
            ui.box(row, CARD, 12, BORDER)
            count = sum(1 for c in cars if c.source == "mqtt" and c.channel == channel)
            pygame.draw.circle(ui.surface, ACCENT, (row.x + 18, row.centery), 5)
            ui.text(ui.fit(channel, f.bold, w - 130), (row.x + 34, row.y + 8), f.bold)
            ui.text(f"{count} {'car' if count == 1 else 'cars'}", (row.x + 34, row.y + 26), f.small, MUTED)
            if ui.button((row.right - 40, row.y + 9, 28, 28), "×", "ghost"):
                link.remove_channel(channel)
                self.dirty = True
            y += 54
        if not link.channels:
            ui.text("No channels: nothing is subscribed.", (x, y + 4), f.small, WARN)
            y += 28

        y += 4
        text, submitted = ui.text_input("channel", (x, y, w - 72, 36), self.channel_text,
                                        placeholder="e.g. garage/carritos", maxlen=48)
        self.channel_text = text
        if (ui.button((x + w - 64, y, 64, 36), "Add", "primary") or submitted) and text.strip():
            added = link.add_channel(text)
            if added:
                self.channel_text, self.channel_error = "", ""
                self.dirty = True
            else:
                self.channel_error = "Use letters, numbers, - _ and / only"
        y += 46
        if self.channel_error:
            ui.text(self.channel_error, (x, y), f.small, DANGER)
        else:
            ui.text("Cars publish to  <channel>/<id>/tilt", (x, y), f.small, DIM)
        y += 34

        blocked = self.fleet.blocked
        if blocked:
            ui.text("BLOCKED DEVICES", (x, y), f.tiny, DIM)
            y += 22
            for key in blocked:
                row = pygame.Rect(x, y, w, 40)
                ui.box(row, CARD, 10, BORDER)
                ui.text(ui.fit(key, f.body, w - 110), (row.x + 14, row.centery), f.body, MUTED, "midleft")
                if ui.button((row.right - 84, row.y + 6, 74, 28), "Unblock", "ghost"):
                    self.fleet.unblock(key)
                y += 48
        return y - area.y

    # -- settings tab --

    def _tab_settings(self, area) -> int:
        ui, f, fleet = self.ui, self.fonts, self.fleet
        s = fleet.settings
        x, w, y = area.x, area.w, area.y

        def section(title):
            nonlocal y
            ui.text(title, (x, y), f.tiny, DIM)
            y += 22

        section("CONTROL")
        full = ui.slider("full", (x, y, w, 34), s.full_tilt_deg, 15.0, 60.0, "Full-tilt angle", f"{s.full_tilt_deg:.0f}°")
        y += 46
        dead = ui.slider("dead", (x, y, w, 34), s.deadzone_deg, 0.0, 15.0, "Dead zone", f"{s.deadzone_deg:.0f}°")
        y += 46
        top = ui.slider("top", (x, y, w, 34), s.max_speed, 120.0, 600.0, "Top speed", f"{s.max_speed:.0f} px/s")
        y += 46
        if ui.active in ("full", "dead", "top"):
            s.full_tilt_deg, s.deadzone_deg, s.max_speed = full, dead, top
            s.full_tilt_deg = max(s.full_tilt_deg, s.deadzone_deg + 5.0)
            s.deadzone_deg = min(s.deadzone_deg, s.full_tilt_deg - 5.0)
            self.dirty = True

        y += 6
        section("DISPLAY")
        for attr, label in (("trails", "Glowing trails"), ("show_names", "Name tags"), ("show_grid", "Arena grid")):
            value = ui.toggle((x, y, w, 32), label, getattr(s, attr))
            if value != getattr(s, attr):
                setattr(s, attr, value)
                self.dirty = True
            y += 36

        y += 8
        section("DEMO")
        bots = sum(1 for c in fleet.snapshot() if c.source == "bot")
        ui.text("Demo bots", (x, y + 15), f.body, TEXT, "midleft")
        if ui.button((x + w - 120, y, 34, 30), "−", enabled=bots > 0):
            fleet.set_bots(bots - 1)
        ui.text(str(bots), (x + w - 63, y + 15), f.bold, TEXT, "center")
        if ui.button((x + w - 34, y, 34, 30), "+"):
            fleet.set_bots(bots + 1)
        y += 40
        kb = ui.toggle((x, y, w, 32), "Keyboard car  (WASD / arrows)", self.kb_enabled)
        if kb != self.kb_enabled:
            self._toggle_keyboard()
        y += 40
        if ui.button((x, y, w, 36), "Scatter cars"):
            fleet.scatter()
        y += 56

        section("SHORTCUTS")
        for key, desc in (("Tab", "hide / show this panel"), ("F11", "fullscreen"), ("Esc", "deselect / quit"),
                          ("B / K", "demo fleet / keyboard car"), ("Space", "pause selected car"),
                          ("Click", "select a car in the arena")):
            ui.pill((x, y), key, f.tiny, TEXT, CARD, pad=(8, 3))
            ui.text(desc, (x + 64, y + 2), f.small, MUTED)
            y += 26
        return y - area.y


def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="carritos", description="MQTT-driven multi-car arena")
    p.add_argument("--host", default="localhost", help="MQTT broker host (default: localhost)")
    p.add_argument("--port", type=int, default=1883)
    p.add_argument("--user")
    p.add_argument("--password")
    p.add_argument("-c", "--channel", action="append",
                   help=f"MQTT topic prefix to listen on, repeatable (default: {DEFAULT_CHANNEL})")
    p.add_argument("--bots", type=int, default=0, help="start with N demo bots")
    p.add_argument("--profile", default=str(profile.DEFAULT_PATH), help="where names, colors and settings are saved")
    p.add_argument("--width", type=int, default=1360)
    p.add_argument("--height", type=int, default=820)
    p.add_argument("--max-frames", type=int, default=0, help=argparse.SUPPRESS)
    return p.parse_args(argv)


def main(argv=None) -> None:
    App(parse_args(argv)).run()


if __name__ == "__main__":
    main()
