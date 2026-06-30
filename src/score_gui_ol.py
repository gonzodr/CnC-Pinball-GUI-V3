"""Pontszám-kijelző GUI, pygame-mel, framebuffer/DRM kimenetre.

PC-n (VS Code-ban) teszteléskor sima ablakban fut, mert ez a kód
csak Linuxon kényszeríti rá az SDL_VIDEODRIVER=kmsdrm drivert.
Windows-on és macOS-en a normál, natív SDL driver marad érvényben,
ami sima ablakot nyit.

A Raspberry Pi-n (Linux) explicit kmsdrm driverre állítva direktben
a framebufferre/DRM-re rajzol, X11/Wayland nélkül.

Grafikai felépítés:
- htr.png: statikus háttér (dzsungel-keret + "Ball" felhő-buborék
  már beleégetve a képbe), 640x480-ra nyújtva.
- cigip.jpg: cigipapír-textúra, amiből kódból (forgatással) készül
  a játékos-kártyák, rájuk rajzolt névvel és pontszámmal.
"""

import math
import time
import pygame
import os
import sys

# A kmsdrm drivert KIZAROLAG Linuxon allitjuk be
if sys.platform.startswith("linux"):
    os.environ.setdefault("SDL_VIDEODRIVER", "kmsdrm")


ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")


def build_outlined_text_surface(font, text, fill_color, outline_color, outline_width=2):
    """
    Felépít egy KÉSZ surface-t kontúrozott (körvonalas) szöveggel.
    """
    text_surf = font.render(text, True, fill_color)
    outline_surf = font.render(text, True, outline_color)

    padded_w = text_surf.get_width() + outline_width * 2
    padded_h = text_surf.get_height() + outline_width * 2
    result = pygame.Surface((padded_w, padded_h), pygame.SRCALPHA)

    offsets = [(-outline_width, -outline_width), (0, -outline_width), (outline_width, -outline_width),
               (-outline_width, 0),                                    (outline_width, 0),
               (-outline_width, outline_width),  (0, outline_width),  (outline_width, outline_width)]
    for dx, dy in offsets:
        result.blit(outline_surf, (outline_width + dx, outline_width + dy))

    result.blit(text_surf, (outline_width, outline_width))
    return result


def build_drop_shadow(source_surface, opacity=38, blur_radius=6):
    """
    Elkeszit egy fekete "arnyek" verziot a megadott surface-bol.
    """
    shadow = source_surface.copy()
    shadow.fill((0, 0, 0, 255), special_flags=pygame.BLEND_RGBA_MULT)

    if blur_radius > 0:
        padded_w = shadow.get_width() + blur_radius * 2
        padded_h = shadow.get_height() + blur_radius * 2
        padded = pygame.Surface((padded_w, padded_h), pygame.SRCALPHA)
        padded.blit(shadow, (blur_radius, blur_radius))
        shadow = pygame.transform.gaussian_blur(padded, blur_radius)

    shadow.set_alpha(opacity)
    return shadow


def ease_out_cubic(t: float) -> float:
    """
    Ease-out interpolacios fuggveny.
    """
    t = max(0.0, min(1.0, t))
    return 1 - pow(1 - t, 3)


class CardAnimator:
    """
    Nyilvántartja a kártyák láthatóságát és az átlós animációkat.
    """
    ANIMATION_DURATION_SEC = 1.0

    def __init__(self, slide_distances: dict):
        self.slide_distances = slide_distances
        self._visible = {1: True, 2: False, 3: False, 4: False}
        self._anim_start = {1: None, 2: None, 3: None, 4: None}
        self._anim_direction = {1: None, 2: None, 3: None, 4: None}  # "in" / "out"

    def set_active_count(self, count: int):
        now = time.time()
        for slot in range(1, 5):
            should_be_visible = slot <= count
            was_visible = self._visible[slot]

            if should_be_visible and not was_visible:
                self._anim_start[slot] = now
                self._anim_direction[slot] = "in"
            elif not should_be_visible and was_visible:
                self._anim_start[slot] = now
                self._anim_direction[slot] = "out"

            self._visible[slot] = should_be_visible

    def get_offset_y(self, slot: int) -> float:
        slide_distance = self.slide_distances[slot]
        direction = self._anim_direction[slot]

        if direction is None:
            return 0.0 if self._visible[slot] else slide_distance

        elapsed = time.time() - self._anim_start[slot]
        t = elapsed / self.ANIMATION_DURATION_SEC

        if t >= 1.0:
            self._anim_direction[slot] = None
            return 0.0 if self._visible[slot] else slide_distance

        eased = ease_out_cubic(t)

        if direction == "in":
            return slide_distance * (1 - eased)
        else:  # "out"
            return slide_distance * eased

    def is_slot_relevant(self, slot: int) -> bool:
        return self._visible[slot] or self._anim_direction[slot] is not None


class ScoreGUI:
    """
    A pontszám-kijelző alap képernyő 640x480 felbontásra optimalizálva.
    """

    # Új felbontás beállítása
    SCREEN_W = 640
    SCREEN_H = 480

    COLOR_TEXT = (245, 245, 245)
    COLOR_TEXT_OUTLINE = (40, 30, 10)
    COLOR_ACTIVE = (60, 40, 15)
    COLOR_INACTIVE = (110, 100, 90)
    COLOR_MULTIBALL = (220, 40, 40)

    SHADOW_OPACITY = 90
    SHADOW_BLUR_RADIUS = 3  # Kisebb felbontáshoz kicsit visszavett blur

    # 640x480-ra átszámolt fix pozíciók (eredeti * 0.8)
    CARD_LAYOUT = [
        {"pos": (8, 330), "angle": 45},
        {"pos": (152, 330), "angle": 45},
        {"pos": (296, 330), "angle": 45},
        {"pos": (440, 330), "angle": 45},
    ]
    # Arányosan csökkentett kártyaméretek
    CARD_WIDTH = 192   # 240 * 0.8
    CARD_HEIGHT = 80   # 100 * 0.8

    def __init__(self):
        self.screen = None
        self.font_score_big = None
        self.font_label = None
        self.font_small = None
        self.font_card_name = None
        self.font_card_score = None
        self.active = False

        self.background = None
        self.card_texture = None

        # 640x480-hoz igazított átlós animációs úthossz (pixelben)
        distances = {1: 280, 2: 280, 3: 280, 4: 280}
        self.card_animator = CardAnimator(slide_distances=distances)

        self._card_cache = {1: None, 2: None, 3: None, 4: None}
        self._card_cache_key = {1: None, 2: None, 3: None, 4: None}
        self._card_shadow_cache = {1: None, 2: None, 3: None, 4: None}
        self._card_shadow_cache_key = {1: None, 2: None, 3: None, 4: None}
        self._ball_label_cache = None
        self._ball_label_cache_key = None
        self._main_score_cache = None
        self._main_score_cache_key = None

    def acquire_display(self):
        if self.active:
            return

        pygame.init()
        self.screen = pygame.display.set_mode(
            (self.SCREEN_W, self.SCREEN_H),
            pygame.FULLSCREEN if os.environ.get("SDL_VIDEODRIVER") == "kmsdrm" else 0
        )
        pygame.display.set_caption("Cheech & Chong Pinball - Score")

        modak_font_path = os.path.join(ASSETS_DIR, "Modak.ttf")

        # Betűméretek arányos csökkentése (eredeti * 0.8)
        self.font_score_big = pygame.font.Font(modak_font_path, 90)   # 80 * 0.8
        self.font_label = pygame.font.Font(modak_font_path, 40)       # 28 * 0.8
        self.font_small = pygame.font.Font(modak_font_path, 18)       # 22 * 0.8
        self.font_card_name = pygame.font.Font(modak_font_path, 20)   # 20 * 0.8
        self.font_card_score = pygame.font.Font(modak_font_path, 26)  # 26 * 0.8

        self._load_assets()
        self.active = True

    def _load_assets(self):
        bg_path = os.path.join(ASSETS_DIR, "BGR1_Gamemode.png")
        bg_raw = pygame.image.load(bg_path).convert()
        self.background = pygame.transform.smoothscale(
            bg_raw, (self.SCREEN_W, self.SCREEN_H)
        )

        card_path = os.path.join(ASSETS_DIR, "cigip.jpg")
        self.card_texture = pygame.image.load(card_path).convert()

    def release_display(self):
        if not self.active:
            return
        pygame.display.quit()
        pygame.quit()
        self.active = False

    def _build_card_surface(self, player_num, state):
        card_texture_scaled = pygame.transform.smoothscale(
            self.card_texture, (self.CARD_WIDTH, self.CARD_HEIGHT)
        )
        card = pygame.Surface((self.CARD_WIDTH, self.CARD_HEIGHT), pygame.SRCALPHA)
        card.blit(card_texture_scaled, (0, 0))

        is_active = (player_num == state.current_player)
        text_color = self.COLOR_ACTIVE if is_active else self.COLOR_INACTIVE

        # Szövegek belső eltolása a kártyán belül (szintén skálázva)
        name_surf = self.font_card_name.render(f"Player {player_num}", True, text_color)
        card.blit(name_surf, (110, 3)) # PLAYER X

        score_surf = self.font_card_score.render(
            f"{state.players[player_num]:,}", True, text_color
        )
        card.blit(score_surf, (55, 28)) # SCORE X

        return card

    def _get_cached_card_surface(self, player_num, state):
        cache_key = (state.players[player_num], player_num == state.current_player)
        if self._card_cache_key[player_num] != cache_key:
            self._card_cache[player_num] = self._build_card_surface(player_num, state)
            self._card_cache_key[player_num] = cache_key
        return self._card_cache[player_num]

    def _get_cached_card_shadow(self, player_num, rotated_card_surface):
        cache_key = self._card_cache_key[player_num]
        if self._card_shadow_cache_key[player_num] != cache_key:
            self._card_shadow_cache[player_num] = build_drop_shadow(
                rotated_card_surface, opacity=self.SHADOW_OPACITY,
                blur_radius=self.SHADOW_BLUR_RADIUS
            )
            self._card_shadow_cache_key[player_num] = cache_key
        return self._card_shadow_cache[player_num]

    def render(self, state):
        if not self.active:
            return

        self.card_animator.set_active_count(state.active_player_count)
        self.screen.blit(self.background, (0, 0))

        # "Ball: X" felirat pozíciója átszámolva a felhőhöz (190*0.8, 105*0.8 -> 152, 84)
        if self._ball_label_cache_key != state.current_ball:
            self._ball_label_cache = self.font_label.render(
                f"Ball: {state.current_ball}", True, self.COLOR_ACTIVE
            )
            self._ball_label_cache_key = state.current_ball
        ball_rect = self._ball_label_cache.get_rect(center=(135, 90))
        self.screen.blit(self._ball_label_cache, ball_rect)

        # 4 kártya kirajzolása átlós animációval
        for player_num, layout in zip(self.players_order(), self.CARD_LAYOUT):
            slot = player_num
            if not self.card_animator.is_slot_relevant(slot):
                continue

            card = self._get_cached_card_surface(player_num, state)
            rotated = pygame.transform.rotate(card, layout["angle"])

            offset_anim = self.card_animator.get_offset_y(slot)
            pos_x, pos_y = layout["pos"]

            # Átlós mozgás: Y nő (+), X csökken (-)
            actual_x = pos_x - offset_anim
            actual_y = pos_y + offset_anim

            # Drop shadow kirajzolás
            shadow = self._get_cached_card_shadow(player_num, rotated)
            shadow_offset = 5 # Csökkentett offset a kisebb felbontás miatt (7 * 0.8 ≈ 5)
            shadow_x = actual_x + shadow_offset - self.SHADOW_BLUR_RADIUS
            shadow_y = actual_y - self.SHADOW_BLUR_RADIUS
            self.screen.blit(shadow, (shadow_x, shadow_y))

            self.screen.blit(rotated, (actual_x, actual_y))

        # Középső nagy pontszám elhelyezése és kézi igazítása
        main_score_value = state.players[state.current_player]
        if self._main_score_cache_key != main_score_value:
            self._main_score_cache = build_outlined_text_surface(
                self.font_score_big, f"{main_score_value:,}",
                self.COLOR_TEXT, self.COLOR_TEXT_OUTLINE, outline_width=3, # picit vastagabb kontúr a nagyobb betűhöz
            )
            self._main_score_cache_key = main_score_value

        # KÉZI FINOMHANGOLÁS:
        # Ha balra/jobbra akarod tolni: változtasd a 0-t (pl. +20 vagy -20)
        # Ha feljebb/lejjebb akarod tolni: változtasd a -40-et
        korrekcio_x = 0 
        korrekcio_y = 15 

        center_x = (self.SCREEN_W // 2) + korrekcio_x
        center_y = (self.SCREEN_H // 2) + korrekcio_y

        score_rect = self._main_score_cache.get_rect(center=(center_x, center_y))
        self.screen.blit(self._main_score_cache, score_rect)

        # Multiball szöveg eltolása (640 - 128 = 512)
        if state.multiball_active:
            mb_text = self.font_small.render("MULTIBALL!", True, self.COLOR_MULTIBALL)
            self.screen.blit(mb_text, (self.SCREEN_W - 128, 24))

        pygame.display.flip()

    def players_order(self):
        return [1, 2, 3, 4]

    def poll_pygame_events(self):
        if not self.active:
            return []
        return pygame.event.get()

    def has_quit_event(self, pygame_events) -> bool:
        return any(e.type == pygame.QUIT for e in pygame_events)