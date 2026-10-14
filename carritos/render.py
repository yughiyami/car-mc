"""Arena rendering: background, glowing trails, car sprites, labels."""
import math
from collections import deque

import pygame

from .ui import BG, MUTED, TEXT, WARN, WHITE, clamp, mix

SPRITE_SIZE = (64, 36)
CONE_LEN = 120
TRAIL_LEN = 26
TRAIL_BG = (17, 20, 28)  # roughly the arena color the trail fades into


def shade(color, factor):
    return tuple(clamp(int(v * factor), 0, 255) for v in color)


def make_car_sprite(color) -> pygame.Surface:
    """Top-down car facing right, drawn once per color."""
    s = pygame.Surface(SPRITE_SIZE, pygame.SRCALPHA)
    for x in (12, 40):
        for y in (2, 28):
            pygame.draw.rect(s, (16, 18, 24), (x, y, 14, 6), border_radius=3)
    body = pygame.Rect(3, 6, 58, 24)
    pygame.draw.rect(s, shade(color, 0.7), body, border_radius=10)
    pygame.draw.rect(s, color, body.inflate(-4, -6).move(0, -2), border_radius=8)
    pygame.draw.rect(s, shade(color, 0.48), (20, 10, 22, 16), border_radius=6)
    pygame.draw.rect(s, (22, 27, 38), (38, 11, 9, 14), border_radius=4)
    pygame.draw.rect(s, (32, 38, 52), (15, 11, 6, 14), border_radius=3)
    for y in (9, 22):
        pygame.draw.rect(s, (255, 244, 200), (58, y, 5, 5), border_radius=2)
        pygame.draw.rect(s, (255, 72, 72), (1, y, 4, 5), border_radius=2)
    return s


def make_shadow_sprite() -> pygame.Surface:
    small = pygame.Surface((16, 9), pygame.SRCALPHA)
    pygame.draw.rect(small, (0, 0, 0, 150), (2, 1, 12, 7), border_radius=3)
    return pygame.transform.smoothscale(small, (SPRITE_SIZE[0] + 8, SPRITE_SIZE[1] + 6))


def make_cone_sprite() -> pygame.Surface:
    """Headlight beam; the light points right from the exact center so rotation is about the car."""
    half = 46
    s = pygame.Surface((CONE_LEN * 2, half * 2), pygame.SRCALPHA)
    for d in range(CONE_LEN):
        alpha = int(64 * (1 - d / CONE_LEN) ** 1.6)
        hw = 6 + d * 0.33
        pygame.draw.line(s, (255, 238, 190, alpha), (CONE_LEN + d, half - hw), (CONE_LEN + d, half + hw))
    return s


class ArenaView:
    RADIUS = 18

    def __init__(self, fonts):
        self.fonts = fonts
        self.size = (0, 0)
        self.show_grid = True
        self._bg = None
        self._surf = None
        self._trail_pts = {}
        self._sprites = {}
        self._labels = {}
        self._corners = None
        self._shadow = make_shadow_sprite()
        self._cone = make_cone_sprite()

    # -- caches --

    def resize(self, w: int, h: int) -> None:
        w, h = max(w, 64), max(h, 64)
        if (w, h) == self.size:
            return
        self.size = (w, h)
        self._surf = pygame.Surface((w, h))
        self._bg = self._build_bg()
        self._corners = self._build_corners()

    def set_grid(self, show: bool) -> None:
        if show != self.show_grid:
            self.show_grid = show
            self._bg = self._build_bg()

    def _build_bg(self) -> pygame.Surface:
        w, h = self.size
        bg = pygame.Surface((w, h))
        top, bottom = (22, 27, 38), (12, 14, 20)
        for y in range(h):
            pygame.draw.line(bg, mix(top, bottom, y / max(h - 1, 1)), (0, y), (w, y))
        if self.show_grid:
            grid = pygame.Surface((w, h), pygame.SRCALPHA)
            for x in range(0, w, 50):
                pygame.draw.line(grid, (255, 255, 255, 22 if x % 250 == 0 else 9), (x, 0), (x, h))
            for y in range(0, h, 50):
                pygame.draw.line(grid, (255, 255, 255, 22 if y % 250 == 0 else 9), (0, y), (w, y))
            bg.blit(grid, (0, 0))
        small = pygame.Surface((48, 32), pygame.SRCALPHA)
        for px in range(48):
            for py in range(32):
                d = math.hypot((px - 23.5) / 24, (py - 15.5) / 16)
                small.set_at((px, py), (0, 0, 0, int(clamp((d - 0.55) / 0.6, 0, 1) ** 1.6 * 150)))
        bg.blit(pygame.transform.smoothscale(small, (w, h)), (0, 0))
        return bg

    def _build_corners(self):
        r = self.RADIUS
        tile = pygame.Surface((r, r), pygame.SRCALPHA)
        tile.fill((*BG, 255))
        pygame.draw.circle(tile, (0, 0, 0, 0), (r, r), r)  # top-left corner
        return {
            "tl": tile,
            "tr": pygame.transform.flip(tile, True, False),
            "bl": pygame.transform.flip(tile, False, True),
            "br": pygame.transform.flip(tile, True, True),
        }

    def _sprite(self, color):
        sprite = self._sprites.get(color)
        if sprite is None:
            sprite = self._sprites[color] = make_car_sprite(color)
        return sprite

    def _label(self, text, color, selected, frozen):
        key = (text, color, selected, frozen)
        surf = self._labels.get(key)
        if surf is None:
            if len(self._labels) > 300:
                self._labels.clear()
            font = self.fonts.small
            tw, th = font.size(text)
            extra = 14 if frozen else 0
            w, h = tw + 30 + extra, 22
            surf = pygame.Surface((w, h), pygame.SRCALPHA)
            pygame.draw.rect(surf, (10, 12, 18, 215), (0, 0, w, h), border_radius=11)
            if selected:
                pygame.draw.rect(surf, (*color, 255), (0, 0, w, h), 2, border_radius=11)
            pygame.draw.circle(surf, color, (12, h // 2), 4)
            surf.blit(font.render(text, True, TEXT), (22, (h - th) // 2))
            if frozen:
                for dx in (0, 4):
                    pygame.draw.rect(surf, WARN, (w - 17 + dx, 6, 2, 10))
            self._labels[key] = surf
        return surf

    # -- interaction --

    def pick(self, cars, pos, radius=34):
        best, best_d = None, radius
        for car in cars:
            d = math.hypot(car.x - pos[0], car.y - pos[1])
            if d < best_d:
                best, best_d = car.id, d
        return best

    # -- drawing --

    def draw(self, screen, rect, cars, settings, selected, now) -> None:
        self.resize(rect.w, rect.h)
        surf = self._surf
        surf.blit(self._bg, (0, 0))

        self._draw_trails(surf, cars, settings.trails)

        for car in cars:
            deg = -math.degrees(car.heading)
            shadow = pygame.transform.rotate(self._shadow, deg)
            surf.blit(shadow, shadow.get_rect(center=(car.x + 3, car.y + 6)))
        for car in cars:
            if car.speed > 30:
                cone = pygame.transform.rotate(self._cone, -math.degrees(car.heading))
                surf.blit(cone, cone.get_rect(center=(car.x, car.y)))
        for car in cars:
            body = pygame.transform.rotate(self._sprite(car.color), -math.degrees(car.heading))
            surf.blit(body, body.get_rect(center=(car.x, car.y)))

        for car in cars:
            if car.id == selected:
                pulse = 2.5 * math.sin(now * 4.5)
                pygame.draw.circle(surf, mix(car.color, BG, 0.55), (car.x, car.y), 40 + pulse, 1)
                pygame.draw.circle(surf, car.color, (car.x, car.y), 36 + pulse, 2)
            if settings.show_names:
                label = self._label(car.label, car.color, car.id == selected, car.frozen)
                surf.blit(label, label.get_rect(midbottom=(car.x, car.y - 30)))

        if not cars:
            self._empty_state(surf)

        screen.blit(surf, rect.topleft)
        r = self.RADIUS
        screen.blit(self._corners["tl"], rect.topleft)
        screen.blit(self._corners["tr"], (rect.right - r, rect.y))
        screen.blit(self._corners["bl"], (rect.x, rect.bottom - r))
        screen.blit(self._corners["br"], (rect.right - r, rect.bottom - r))
        pygame.draw.rect(screen, (46, 53, 72), rect, 1, border_radius=r)

    def _draw_trails(self, surf, cars, enabled) -> None:
        """Recent rear positions per car, faded toward the background (cheap, no alpha surface)."""
        alive = {car.id for car in cars}
        for key in [k for k in self._trail_pts if k not in alive or not enabled]:
            del self._trail_pts[key]
        if not enabled:
            return
        for car in cars:
            pts = self._trail_pts.setdefault(car.id, deque(maxlen=TRAIL_LEN))
            if abs(car.speed) > 25:
                pts.append((car.x - math.cos(car.heading) * 24, car.y - math.sin(car.heading) * 24))
            elif pts:
                pts.popleft()  # let the trail shrink when the car stops
            n = len(pts)
            for i, p in enumerate(pts):
                t = (i + 1) / n
                pygame.draw.circle(surf, mix(TRAIL_BG, car.color, 0.55 * t), p, 2 + 4 * t)
            if n:
                pygame.draw.circle(surf, mix(car.color, WHITE, 0.35), pts[-1], 3)

    def _empty_state(self, surf) -> None:
        w, h = self.size
        title = self.fonts.big.render("Waiting for cars…", True, TEXT)
        sub = self.fonts.body.render("Tilt an ESP32 or a phone, or add demo bots in Settings.", True, MUTED)
        surf.blit(title, title.get_rect(center=(w // 2, h // 2 - 14)))
        surf.blit(sub, sub.get_rect(center=(w // 2, h // 2 + 22)))
