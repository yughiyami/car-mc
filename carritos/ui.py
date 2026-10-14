"""Tiny immediate-mode widget kit for pygame, plus the app theme.

Every widget is a method you call each frame; it draws itself and returns the new
value / whether it was clicked.
"""
import colorsys

import pygame

# Theme
BG = (15, 19, 20)
PANEL = (22, 28, 28)
CARD = (29, 37, 36)
CARD_HOVER = (38, 49, 46)
BORDER = (49, 63, 59)
TEXT = (239, 242, 229)
MUTED = (160, 177, 169)
DIM = (123, 142, 134)
ACCENT = (185, 237, 112)
OK = (61, 220, 151)
WARN = (255, 176, 32)
DANGER = (255, 92, 108)
WHITE = (255, 255, 255)


def mix(a, b, t: float):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


class Fonts:
    def __init__(self):
        names = "segoeui,inter,helveticaneue,helvetica,arial,dejavusans"

        def make(size, bold=False):
            return pygame.font.SysFont(names, size, bold=bold)

        self.title = make(22, True)
        self.h2 = make(15, True)
        self.body = make(14)
        self.bold = make(14, True)
        self.small = make(12)
        self.tiny = make(11, True)
        self.big = make(26, True)


class UI:
    def __init__(self, fonts: Fonts):
        self.fonts = fonts
        self.surface = None
        self.mouse = (0, 0)
        self.clicked = False
        self.down = False
        self.wheel = 0
        self.typed = ""
        self.keys = []
        self.active = None       # slider being dragged
        self.focus = None        # text input with keyboard focus
        self._clip = None
        self._clips = []
        self._focus_rect = None
        self._focus_seen = False
        self._input_rects = {}
        self._text_mode = False
        self._cache = {}

    # -- frame lifecycle --

    def begin(self, surface, events) -> None:
        self.surface = surface
        self.mouse = pygame.mouse.get_pos()
        self.down = pygame.mouse.get_pressed()[0]
        self.clicked = False
        self.wheel = 0
        self.typed = ""
        self.keys = []
        self._clip = None
        self._clips = []
        self._focus_seen = False
        previous_inputs = self._input_rects
        self._input_rects = {}
        for e in events:
            if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
                self.clicked = True
                self.mouse = e.pos
                self.down = True
                if self._focus_rect is not None and not self._focus_rect.collidepoint(e.pos):
                    self.focus = None
                for wid, rect in previous_inputs.items():
                    if rect.collidepoint(e.pos):
                        self.focus = wid
                        break
            elif e.type == pygame.MOUSEBUTTONUP and e.button == 1:
                self.down = False
                self.active = None
            elif e.type == pygame.WINDOWFOCUSLOST:
                self.active = self.focus = None
                self.down = False
            elif e.type == pygame.MOUSEWHEEL:
                self.wheel += e.y
            elif e.type == pygame.TEXTINPUT:
                self.typed += e.text
            elif e.type == pygame.KEYDOWN and not getattr(e, "repeat", False):
                self.keys.append(e.key)

    def end(self) -> None:
        if self.focus is not None and not self._focus_seen:
            self.focus = None
        if self.focus is not None and not self._text_mode:
            pygame.key.start_text_input()
            self._text_mode = True
        elif self.focus is None and self._text_mode:
            pygame.key.stop_text_input()
            self._text_mode = False

    @property
    def typing(self) -> bool:
        return self.focus is not None

    # -- clipping & hit testing --

    def push_clip(self, rect) -> None:
        self._clips.append(self._clip)
        rect = pygame.Rect(rect)
        self._clip = rect.clip(self._clip) if self._clip is not None else rect
        self.surface.set_clip(self._clip)

    def pop_clip(self) -> None:
        self._clip = self._clips.pop() if self._clips else None
        self.surface.set_clip(self._clip)

    def hit(self, rect) -> bool:
        if not pygame.Rect(rect).collidepoint(self.mouse):
            return False
        return self._clip is None or self._clip.collidepoint(self.mouse)

    # -- primitives --

    def text(self, s, pos, font, color=TEXT, anchor="topleft"):
        key = (s, id(font), color)
        surf = self._cache.get(key)
        if surf is None:
            if len(self._cache) > 1200:
                self._cache.clear()
            surf = self._cache[key] = font.render(s, True, color)
        rect = surf.get_rect(**{anchor: pos})
        self.surface.blit(surf, rect)
        return rect

    def fit(self, s, font, max_w):
        if font.size(s)[0] <= max_w:
            return s
        while s and font.size(s + "…")[0] > max_w:
            s = s[:-1]
        return s + "…"

    def box(self, rect, fill, radius=10, border=None, width=1):
        pygame.draw.rect(self.surface, fill, rect, border_radius=radius)
        if border:
            pygame.draw.rect(self.surface, border, rect, width, border_radius=radius)

    def bar(self, rect, frac, color, bg=BORDER):
        rect = pygame.Rect(rect)
        pygame.draw.rect(self.surface, bg, rect, border_radius=rect.h // 2)
        w = int(rect.w * clamp(frac, 0, 1))
        if w >= 2:
            pygame.draw.rect(self.surface, color, (rect.x, rect.y, w, rect.h), border_radius=rect.h // 2)

    def pill(self, pos, s, font, fg, bg, pad=(10, 4), anchor="topleft", dot=None):
        tw, th = font.size(s)
        dot_w = 14 if dot else 0
        rect = pygame.Rect(0, 0, tw + pad[0] * 2 + dot_w, th + pad[1] * 2)
        setattr(rect, anchor, pos)
        pygame.draw.rect(self.surface, bg, rect, border_radius=rect.h // 2)
        x = rect.x + pad[0]
        if dot:
            pygame.draw.circle(self.surface, dot, (x + 4, rect.centery), 4)
            x += dot_w
        self.text(s, (x, rect.centery), font, fg, "midleft")
        return rect

    # -- widgets --

    def button(self, rect, label, kind="default", enabled=True, active=False):
        rect = pygame.Rect(rect)
        hover = enabled and self.hit(rect)
        border = None
        if kind == "primary":
            fill, fg = (mix(ACCENT, WHITE, 0.15) if hover else ACCENT), (10, 14, 24)
        elif kind == "danger":
            fill, fg, border = ((84, 38, 48) if hover else (60, 30, 38)), DANGER, (112, 50, 62)
        elif kind == "ghost":
            fill, fg, border = (CARD if hover else PANEL), MUTED, BORDER
        else:
            fill, fg = (CARD_HOVER if hover else CARD), TEXT
            border = BORDER
        if active:
            fill, fg, border = (47, 68, 47), TEXT, ACCENT
        if not enabled:
            fg = DIM
        self.box(rect, fill, 9, border)
        self.text(self.fit(label, self.fonts.bold, rect.w - 12), rect.center, self.fonts.bold, fg, "center")
        return hover and self.clicked

    def toggle(self, rect, label, value):
        rect = pygame.Rect(rect)
        hover = self.hit(rect)
        self.text(self.fit(label, self.fonts.body, rect.w - 52), (rect.x, rect.centery), self.fonts.body, TEXT, "midleft")
        sw = pygame.Rect(0, 0, 40, 22)
        sw.midright = rect.midright
        pygame.draw.rect(self.surface, ACCENT if value else (56, 62, 82), sw, border_radius=11)
        knob_x = sw.right - 11 if value else sw.x + 11
        pygame.draw.circle(self.surface, WHITE, (knob_x, sw.centery), 8)
        if hover and self.clicked:
            value = not value
        return value

    def _rainbow(self, w):
        key = ("rainbow", w)
        bar = self._cache.get(key)
        if bar is None:
            bar = pygame.Surface((w, 8), pygame.SRCALPHA)
            for x in range(w):
                r, g, b = colorsys.hsv_to_rgb(x / w, 0.62, 1.0)
                pygame.draw.line(bar, (int(r * 255), int(g * 255), int(b * 255)), (x, 0), (x, 7))
            mask = pygame.Surface((w, 8), pygame.SRCALPHA)
            pygame.draw.rect(mask, (255, 255, 255, 255), (0, 0, w, 8), border_radius=4)
            bar.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
            self._cache[key] = bar
        return bar

    def slider(self, wid, rect, value, lo, hi, label, shown, rainbow=False):
        rect = pygame.Rect(rect)
        self.text(label, (rect.x, rect.y), self.fonts.small, MUTED)
        self.text(shown, (rect.right, rect.y), self.fonts.small, TEXT, "topright")
        track = pygame.Rect(rect.x, rect.bottom - 12, rect.w, 4)
        area = pygame.Rect(rect.x - 8, rect.bottom - 24, rect.w + 16, 26)
        if self.clicked and self.hit(area):
            self.active = wid
        if self.active == wid:
            if self.down:
                value = lo + clamp((self.mouse[0] - rect.x) / rect.w, 0, 1) * (hi - lo)
            else:
                self.active = None
        frac = (value - lo) / (hi - lo)
        if rainbow:
            self.surface.blit(self._rainbow(track.w), (track.x, track.centery - 4))
        else:
            pygame.draw.rect(self.surface, BORDER, track, border_radius=2)
            pygame.draw.rect(self.surface, ACCENT, (track.x, track.y, int(track.w * frac), track.h), border_radius=2)
        kx = track.x + int(track.w * frac)
        hot = self.active == wid or self.hit(area)
        pygame.draw.circle(self.surface, mix(ACCENT, BG, 0.5), (kx, track.centery), 11 if hot else 9)
        pygame.draw.circle(self.surface, WHITE, (kx, track.centery), 7)
        return value

    def text_input(self, wid, rect, text, placeholder="", maxlen=20):
        """Returns (text, submitted). Enter submits, Esc drops focus."""
        rect = pygame.Rect(rect)
        visible = rect.clip(self._clip) if self._clip is not None else rect
        if visible.w and visible.h:
            self._input_rects[wid] = visible
        submitted = False
        if self.clicked:
            if self.hit(rect):
                self.focus = wid
            elif self.focus == wid:
                self.focus = None
        focused = self.focus == wid
        if focused:
            self._focus_rect = rect.copy()
            self._focus_seen = True
            text += "".join(ch for ch in self.typed if ch.isprintable())
            for key in self.keys:
                if key == pygame.K_BACKSPACE:
                    text = text[:-1]
                elif key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                    submitted = True
                    self.focus = None
                elif key in (pygame.K_ESCAPE, pygame.K_TAB):
                    self.focus = None
            text = text[:maxlen]
        self.box(rect, (16, 18, 25), 9, ACCENT if focused else BORDER, 2 if focused else 1)
        inner = rect.inflate(-24, 0)
        if text:
            shown = self.fit(text, self.fonts.body, inner.w)
            r = self.text(shown, (inner.x, rect.centery), self.fonts.body, TEXT, "midleft")
            caret_x = r.right + 2
        else:
            self.text(placeholder, (inner.x, rect.centery), self.fonts.body, DIM, "midleft")
            caret_x = inner.x
        if focused and (pygame.time.get_ticks() // 500) % 2 == 0:
            pygame.draw.line(self.surface, TEXT, (caret_x, rect.y + 8), (caret_x, rect.bottom - 8), 2)
        return text, submitted

    def swatch(self, center, radius, color, selected):
        rect = pygame.Rect(0, 0, radius * 2 + 6, radius * 2 + 6)
        rect.center = center
        hover = self.hit(rect)
        pygame.draw.circle(self.surface, color, center, radius)
        if selected:
            pygame.draw.circle(self.surface, WHITE, center, radius + 4, 2)
        elif hover:
            pygame.draw.circle(self.surface, MUTED, center, radius + 3, 2)
        return hover and self.clicked
