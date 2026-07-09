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

import colorsys
import math
import random
import time
import pygame
import os
import sys

# A kmsdrm drivert KIZAROLAG Linuxon allitjuk be
# A kmsdrm drivert KIZAROLAG akkor allitjuk be, ha Linuxon vagyunk
# ES nincs futo X11/Wayland desktop session. Ez kulonbozteti meg a
# tenyleges, headless Raspberry Pi OS Lite-ot (nincs DISPLAY/WAYLAND_
# DISPLAY valtozo, mert nincs grafikus felulet) a Linux desktop/VM
# rendszerektol (pl. VirtualBox-os Debian asztali feluettel) - ott a
# DISPLAY valtozo letezik, mert fut X11/Wayland, es a kmsdrm driver
# nem is mukodne (a virtualis videokartya nem ad DRM/KMS hozzaferest,
# es a desktop session amugy is lefoglalja a DRM master jogot).
#
# Igy ugyanez a kod automatikusan:
# - sima ablakot nyit Windows-on, macOS-en, ES Linux desktop/VM-en
# - kmsdrm-et hasznal CSAK a tenyleges, headless Pi-n
_has_x11_or_wayland = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
if sys.platform.startswith("linux") and not _has_x11_or_wayland:
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


def build_outlined_text_surface_spaced(font, text, fill_color, outline_color, outline_width=2, extra_spacing=0):
    """
    Ugyanaz, mint a build_outlined_text_surface, csak betunkent kulon
    rajzolva, extra_spacing pixel tobblet-ressel a karakterek kozott
    (pygame.font nem tamogat betukoz-allitast kozvetlenul).
    """
    if not text:
        return pygame.Surface((0, 0), pygame.SRCALPHA)

    char_surfaces = [build_outlined_text_surface(font, ch, fill_color, outline_color, outline_width) for ch in text]
    total_w = sum(s.get_width() for s in char_surfaces) + extra_spacing * (len(char_surfaces) - 1)
    max_h = max(s.get_height() for s in char_surfaces)

    result = pygame.Surface((total_w, max_h), pygame.SRCALPHA)
    x = 0
    for s in char_surfaces:
        result.blit(s, (x, 0))
        x += s.get_width() + extra_spacing
    return result


def _blur_supported() -> bool:
    """32 bites ARM-on (armv7l/armv6l) a blur Bus Error-t okoz, kizarjuk."""
    import platform
    machine = platform.machine().lower()
    if machine in ("armv7l", "armv6l"):
        return False
    return hasattr(pygame.transform, 'box_blur')

def _smoothscale_supported() -> bool:
    import platform
    return platform.machine().lower() not in ("armv7l", "armv6l")


def build_drop_shadow(source_surface, opacity=38, blur_radius=6):
    """
    Elkeszit egy fekete "arnyek" verziot a megadott surface-bol.
    """
    shadow = source_surface.copy()
    shadow.fill((0, 0, 0, 255), special_flags=pygame.BLEND_RGBA_MULT)

    if blur_radius > 0 and _blur_supported():
        padded_w = shadow.get_width() + blur_radius * 2
        padded_h = shadow.get_height() + blur_radius * 2
        padded = pygame.Surface((padded_w, padded_h), pygame.SRCALPHA)
        padded.blit(shadow, (blur_radius, blur_radius))
        shadow = pygame.transform.box_blur(padded, blur_radius)

    shadow.set_alpha(opacity)
    return shadow


def ease_out_cubic(t: float) -> float:
    """
    Ease-out interpolacios fuggveny.
    """
    t = max(0.0, min(1.0, t))
    return 1 - pow(1 - t, 3)


class ParticleBurst:
    """
    Egyszeru, idoalapu (nem frame-fuggo) particle-effekt: egy adott
    pillanatban indulo "robbanas", aminek minden szemcseje onnantol
    szamitott egyenes vonalu mozgassal (+ enyhe gravitacioval) repul es
    zsugorodik, amig el nem eri az elettartamat. Sima `pygame.draw.circle`-
    lel rajzol (nincs per-particle surface/alpha blend), hogy a Pi-n is
    olcso maradjon.
    """

    def __init__(self, origin, count, colors, speed_range, lifetime_range, size_range, gravity=260.0):
        now = time.time()
        self.gravity = gravity
        self.particles = []
        for _ in range(count):
            angle = random.uniform(0, 2 * math.pi)
            speed = random.uniform(*speed_range)
            self.particles.append({
                "x0": origin[0], "y0": origin[1],
                "vx": math.cos(angle) * speed,
                "vy": math.sin(angle) * speed,
                "spawn": now,
                "lifetime": random.uniform(*lifetime_range),
                "color": random.choice(colors),
                "size": random.uniform(*size_range),
            })

    def is_alive(self) -> bool:
        now = time.time()
        return any(0 <= (now - p["spawn"]) <= p["lifetime"] for p in self.particles)

    def draw(self, surface):
        now = time.time()
        for p in self.particles:
            t = now - p["spawn"]
            if t < 0 or t > p["lifetime"]:
                continue
            progress = t / p["lifetime"]
            x = p["x0"] + p["vx"] * t
            y = p["y0"] + p["vy"] * t + 0.5 * self.gravity * t * t
            radius = int(p["size"] * (1 - progress))
            if radius >= 1:
                pygame.draw.circle(surface, p["color"], (int(x), int(y)), radius)


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

    # Allapotvaltaskori crossfade (lasd start_fade_transition/draw_fade_overlay).
    # Ugyanaz a biztonsagos technika (surface.set_alpha() + kozvetlen blit a
    # self.screen-re, NEM kozbenso SRCALPHA feluletre), mint amit a LOGO
    # kepernyo pszichedelikus hattere mar bizonyitottan hasznal ARM-on.
    FADE_DURATION_SEC = 0.25

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

    # --- HIGHSCORE képernyő layout ---
    # Ezek a koordináták a referencia képernyőkép (1111x829) alapján lettek
    # átszámolva 640x480-ra, hogy a szöveg pontosan a HiScoreBg.png keretére
    # illeszkedjen (cím, fejléc, oszlopok, sortávolság).
    HISCORE_TITLE_Y = 76
    HISCORE_HEADER_Y = 131
    HISCORE_ROW_START_Y = 159
    HISCORE_ROW_SPACING = 26

    HISCORE_LEAF_X = 172        # levél ikon közepe
    HISCORE_COL_POS_X = 190     # "POS" / "1ST" stb. - balra igazítva
    HISCORE_COL_SCORE_X = 295   # "SCORE" fejléc - középre igazítva
    HISCORE_COL_SCORE_RIGHT_X = 340  # a pontszám ERTEKEK jobbra igazitva ehhez
    HISCORE_COL_NAME_X = 370    # "NAME" - balra igazítva

    HISCORE_LEAF_SIZE = 22
    # Pontos színek a Unity referenciából (HighscoreTable.cs)
    HISCORE_GOLD = (255, 210, 0)      # FFD200
    HISCORE_SILVER = (198, 198, 198)  # C6C6C6
    HISCORE_BRONZE = (183, 111, 86)   # B76F56

    # EGY nagy, félig áttetsző, zöld keretes panel az egész tábla mögött
    # (a HiScoreBg.png maga csak a nyers autós/dzsungeles háttér, ezt a
    # panelt kódból rajzoljuk rá, hogy a szöveg elváljon a háttértől).
    # Méretek a referencia képernyőkép (1111x829) pixeleiből számolva,
    # átskálázva 640x480-ra.
    HISCORE_PANEL_RECT = (150, 38, 340, 425)   # x, y, w, h
    HISCORE_PANEL_RADIUS = 20
    HISCORE_PANEL_BORDER_WIDTH = 10
    HISCORE_PANEL_BORDER_COLOR = (2, 90, 15)
    HISCORE_PANEL_FILL_COLOR = (20, 25, 20)
    HISCORE_PANEL_FILL_ALPHA = 150

    # --- NAME ENTRY (hiscore beíró) képernyő layout ---
    # A referencia mockup (693x517) alapján, átskálázva 640x480-ra.
    NAME_TITLE_Y = 90
    NAME_LETTERS_Y = 220
    NAME_LETTER_X = [263, 320, 377]   # a 3 karakter-pozíció középpontjai
    NAME_ARROW_LEFT_X = 151
    NAME_ARROW_RIGHT_X = 489
    NAME_ARROW_SIZE = 46
    NAME_CARET_SIZE = 22
    NAME_CARET_OFFSET_Y = 40           # a betű alatt ennyivel lejjebb
    NAME_CARET_COLOR = (40, 200, 40)
    NAME_HINT_Y = 305
    NAME_HINT_LEFT_X = 190
    NAME_HINT_RIGHT_X = 450
    NAME_HINT_START_COLOR = (230, 40, 40)
    NAME_HINT_SHOOT_COLOR = (60, 210, 60)

    # --- SUMMARY (bónusz-összegző) képernyő layout ---
    # Tömörített elrendezés, hogy az 5 sor (PLAYER/SCORE/BONUS/+bonus/TOTAL)
    # a régi 4-soros elrendezéshez hasonló, szűkebb sávba férjen bele.
    SUMMARY_PLAYER_Y = 140
    SUMMARY_SCORE_Y = 185
    SUMMARY_BONUS_LABEL_Y = 223
    SUMMARY_BONUS_VALUE_Y = 261
    SUMMARY_TOTAL_Y = 325

    # Szikra-effekt a TOTAL sor megjelenesekor (lasd ParticleBurst) - ez
    # mindig van, szemben a bonusszal, ami lehet 0
    SUMMARY_SPARK_REVEAL_TIME = 4.0  # egyezik a TOTAL reveal-indulasaval
    SUMMARY_SPARK_COLORS = [(255, 245, 180), (255, 210, 60), (255, 160, 40)]
    SUMMARY_SPARK_COUNT = 18
    SUMMARY_SPARK_SPEED_RANGE = (60, 170)
    SUMMARY_SPARK_LIFETIME_RANGE = (0.5, 0.9)
    SUMMARY_SPARK_SIZE_RANGE = (2, 4)

    # --- FINAL SCORES (tobb-jatekos vegeredmeny) kepernyo layout ---
    # 2x2 racs, a regi Unity GUI elrendezeset koveti: 1-es balfent,
    # 3-as jobbfent, 2-es ballent, 4-es joblent. Csak annyi slot latszik,
    # ahany jatekos tenylegesen jatszott (final_player_count).
    FINAL_SCORES_SLOT_POS = {
        1: (190, 150),
        3: (450, 150),
        2: (190, 310),
        4: (450, 310),
    }
    FINAL_SCORES_VALUE_OFFSET_Y = 42
    FINAL_SCORES_WINNER_COLOR = (255, 215, 0)
    FINAL_SCORES_LEAF_OFFSET_Y = 58  # a level-ikon a "PLAYER N" felirat FOLOTT, ennyivel feljebb (oldalra nem fer el a 2 oszlop kozott)
    # A gyoztes pulzalasa - ugyanaz a keplet/idozites, mint a PRESS_START kepernyon
    FINAL_SCORES_WINNER_PULSE_PERIOD_SEC = 1.0
    FINAL_SCORES_WINNER_PULSE_MIN_SCALE = 0.7
    FINAL_SCORES_WINNER_PULSE_MAX_SCALE = 1.0

    # Ismetlodo kis "tuzijatek" robbanasok a gyoztes korul (lasd ParticleBurst)
    FINAL_SCORES_FIREWORK_INTERVAL_RANGE = (0.7, 1.3)  # ennyi ido telik ket robbanas kozott
    FINAL_SCORES_FIREWORK_SPREAD = 45  # a robbanas kozeppontja ennyi pixellel terhet el a gyoztes korul
    FINAL_SCORES_FIREWORK_COLORS = [(255, 90, 90), (255, 210, 60), (120, 220, 255), (140, 255, 140)]
    FINAL_SCORES_FIREWORK_COUNT = 16
    FINAL_SCORES_FIREWORK_SPEED_RANGE = (50, 150)
    FINAL_SCORES_FIREWORK_LIFETIME_RANGE = (0.5, 0.9)
    FINAL_SCORES_FIREWORK_SIZE_RANGE = (2, 4)

    # --- LOGO (attract-mode) képernyő ---
    # introscr.png (átlátszó "Cheech & Chong Pinball" logó) egy előre
    # legenerált, folyamatosan hullámzó pszichedelikus háttér fölött.
    # A hátteret egy kis felbontású rácson (LOGO_BG_GRID_*) számoljuk ki
    # HSV szín-eltolással, EGYSZER, betöltéskor - futásidőben csak a már
    # kész képkockák között lágy alfa-áttűnés van, hogy a Pi-n is olcsó
    # maradjon (nincs per-pixel szamolas minden frame-ben, es a felskalazas
    # is sima `scale`-lel tortenik, nem smoothscale-lel).
    LOGO_BG_GRID_W = 64
    LOGO_BG_GRID_H = 48
    LOGO_BG_FRAME_COUNT = 12
    LOGO_BG_HUE_SPREAD = 0.6      # a szinarnyalat "csavarodasa" a racs atlojan
    LOGO_BG_SATURATION = 0.65
    LOGO_BG_VALUE = 0.85
    LOGO_BG_CYCLE_SEC = 6.0       # a teljes N-frame-es ciklus idotartama

    # --- BEAT THIS SCORE (attract-mode) képernyő layout ---
    # A beatstate.png mar tartalmazza a cimet es a ket ures dobozt - csak
    # a #1 hiscore szamat es a jatekos nevet kell beleirni. Pozicioink
    # becsultek, finomithatok, ha eles kepen nem stimmelnek.
    BEAT_SCORE_NUMBER_Y = 250
    BEAT_SCORE_NUMBER_COLOR = (110, 20, 20)
    BEAT_SCORE_PLAYER_Y = 347
    BEAT_SCORE_PLAYER_COLOR = (40, 45, 20)
    BEAT_SCORE_PLAYER_LETTER_SPACING = 6

    # A cim (beattitle.png) kozeppontja az ures doboz mert kozepen, es
    # ugyanaz a pulzalasi keplet, mint a PRESS_START/FINAL_SCORES gyoztesnel.
    BEAT_TITLE_X = 320
    BEAT_TITLE_Y = 155
    BEAT_TITLE_PULSE_PERIOD_SEC = 1.0
    BEAT_TITLE_PULSE_MIN_SCALE = 0.9
    BEAT_TITLE_PULSE_MAX_SCALE = 1.0

    # Egyszeri (nem ismetlodo) szikraszoras a pontszam korul, amikor a
    # kepernyo megjelenik - visszafogott, nem akarja elvinni a hangsulyt
    # a pulzalo cimrol.
    BEAT_SCORE_SPARK_COLORS = [(255, 245, 180), (255, 210, 60), (255, 160, 40)]
    BEAT_SCORE_SPARK_COUNT = 16
    BEAT_SCORE_SPARK_SPEED_RANGE = (50, 150)
    BEAT_SCORE_SPARK_LIFETIME_RANGE = (0.5, 0.9)
    BEAT_SCORE_SPARK_SIZE_RANGE = (4, 7)

    # --- SZERVIZ MENU (Ctrl+M) ---
    SERVICE_MENU_BG_COLOR = (10, 20, 60)

    # --- PRESS START (attract-mode) képernyő layout ---
    # Zöld sugárirányú háttér (ugyanaz, mint a name entry képernyőn),
    # rajta pulzáló "Press Start / to / Play!" felirat. Egy videóreferencia
    # alapján visszamért ciklusidő/amplitúdó, finomítható.
    PRESS_START_LINES = ("Press Start", "to", "Play!")
    # Unity referencia: Line Spacing = 0.5 (a normal ~121px sortavolsag fele).
    # A build_outlined_text_surface mar hozzaad korvonal-paddinget a
    # sormagashoz, ezert a tenyleges gap negativ (osszehuzza a padding-et
    # is), hogy a sorok kozotti tavolsag kb. a fele legyen a normalnak.
    PRESS_START_LINE_GAP = -62
    # Unity-referencia (AnimationCurve, "Clamped Auto" tangens): 1 masodperces
    # teljes ciklus, 0s/1s-nel 100%, 0.5s-nel 70%. A "Clamped Auto" a
    # szelsoertekeknel (min/max) lapos erintot ad, hogy ne lojjon tul - ezt
    # egyetlen koszinusz-hullam pontosan visszaadja (annak is lapos az
    # erintoje minden szelsoertekben), ezert nincs szukseg kulon
    # keyframe-interpolaciora.
    PRESS_START_PULSE_PERIOD_SEC = 1.0
    PRESS_START_PULSE_MIN_SCALE = 0.7
    PRESS_START_PULSE_MAX_SCALE = 1.0

    # --- SPECIAL THANKS (attract-mode) képernyő layout ---
    # Unity referencia: Modak size 70, Line Spacing 2.89 (a normál
    # sormagasság - font.get_linesize() - szorzója), a cím ("Special
    # Thanks to") a képernyő közepéről indul, és 8 másodperc alatt
    # lineárisan (nincs easing/keyframe) felfelé csúszik, amíg az utolsó
    # sor is át nem ér a cím induló pozícióján.
    # A nevlista mostmar a ThanksNamesManager-bol jon (szerviz menuben
    # szerkesztheto), a "Special Thanks to" cim fix marad.
    THX_LINE_SPACING_MULT = 2.89
    THX_SCROLL_DURATION_SEC = 8.0

    def __init__(self):
        self.screen = None
        self.font_score_big = None
        self.font_active_player = None
        self.font_label = None
        self.font_small = None
        self.font_card_name = None
        self.font_card_score = None
        self.active = False

        self.background = None
        self.card_texture = None
        self.summary_anim_start = None

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
        self._active_player_cache = None
        self._active_player_cache_key = None
        self._press_start_cache = None
        self.thx_scroll_start = None
        self._thx_text_cache = None
        self._thx_line_gap = None
        self._thx_line_count = None

        self._bonus_spark_burst = None
        self._bonus_spark_spawned = False

        self.final_scores_start = None
        self._firework_bursts = []
        self._next_firework_time = 0.0

        self.logo_anim_start = None

        self.beat_score_start = None
        self._beat_score_spark_burst = None
        self._beat_score_spark_spawned = False

        self._fade_snapshot = None
        self._fade_start = None

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
        self.font_active_player = pygame.font.Font(modak_font_path, 40)       # 28 * 0.8
        self.font_label = pygame.font.Font(modak_font_path, 40)       # 28 * 0.8
        self.font_small = pygame.font.Font(modak_font_path, 18)       # 22 * 0.8
        self.font_card_name = pygame.font.Font(modak_font_path, 20)   # 20 * 0.8
        self.font_card_score = pygame.font.Font(modak_font_path, 26)  # 26 * 0.8

        # A SUMMARY (kor-vegi bonusz osszegzo) kepernyohoz hasznalt
        # fontok - ezek hianyoztak, a render_summary() hivatkozott
        # rajuk, de sosem lettek letrehozva, ezert AttributeError-t
        # dobott amikor NEXT/GAMEOVER utan a SUMMARY allapotba lepett
        # a program.
        self.font_summary_title = pygame.font.Font(modak_font_path, 56)
        self.font_summary_mid = pygame.font.Font(modak_font_path, 36)
        self.font_summary_score = pygame.font.Font(modak_font_path, 75)

        # FINAL SCORES (tobb-jatekos vegeredmeny) kepernyo fontjai - kulon
        # peldanyok, NEM ugyanazok mint a SUMMARY-e, hogy kulon hangolhatok
        # legyenek (10-zel kisebbek, szellosebb elrendezeshez).
        self.font_final_label = pygame.font.Font(modak_font_path, 46)
        self.font_final_value = pygame.font.Font(modak_font_path, 26)

        # BEAT THIS SCORE kepernyo fontjai - kulon peldanyok, a beatstate.png
        # ket doboz-meretehez igazitva (a nev-doboz alacsonyabb).
        self.font_beat_number = pygame.font.Font(modak_font_path, 60)
        self.font_beat_player = pygame.font.Font(modak_font_path, 45)

        # Titkos szerviz menu fontjai - a pygame beepitett alapertelmezett
        # fontja (nem Modak), mert ez sur, sok soros listakhoz valo, nem
        # dizajnelemnek szant kepernyo.
        self.font_service_title = pygame.font.Font(None, 32)
        self.font_service_item = pygame.font.Font(None, 24)
        self.font_service_hint = pygame.font.Font(None, 18)

        # HIGHSCORE képernyő fontjai - kisebb méretek, hogy a 10 sor +
        # fejléc + cím ráférjen a HiScoreBg.png keretére.
        self.font_hiscore_title = pygame.font.Font(modak_font_path, 40)
        self.font_hiscore_header = pygame.font.Font(modak_font_path, 20)
        self.font_hiscore_row = pygame.font.Font(modak_font_path, 18)

        # NAME ENTRY képernyő fontjai
        self.font_name_title = pygame.font.Font(modak_font_path, 36)
        self.font_name_letters = pygame.font.Font(modak_font_path, 48)
        self.font_name_hint = pygame.font.Font(modak_font_path, 16)

        # PRESS START (attract-mode) képernyő fontja
        self.font_press_start = pygame.font.Font(modak_font_path, 80)

        # SPECIAL THANKS (attract-mode) képernyő fontja
        self.font_thx = pygame.font.Font(modak_font_path, 70)

        self._load_assets()
        self.active = True

    def _load_assets(self):
        bg_path = os.path.join(ASSETS_DIR, "BGR1_Gamemode.png")
        bg_raw = pygame.image.load(bg_path).convert()
        self.background = pygame.transform.smoothscale(
            bg_raw, (self.SCREEN_W, self.SCREEN_H)
        )
        
        bg2_path = os.path.join(ASSETS_DIR, "BGR2_Scoremode.png")
        bg_raw2 = pygame.image.load(bg2_path).convert()
        self.background2 = pygame.transform.smoothscale(
            bg_raw2, (self.SCREEN_W, self.SCREEN_H)
        )

        bg3_path = os.path.join(ASSETS_DIR, "HiScoreBg.png")
        bg_raw3 = pygame.image.load(bg3_path).convert()
        self.background3 = pygame.transform.smoothscale(
            bg_raw3, (self.SCREEN_W, self.SCREEN_H)
        )

        card_path = os.path.join(ASSETS_DIR, "cigip.jpg")
        self.card_texture = pygame.image.load(card_path).convert()

        # TOP3 levél ikon - a TrophyStar.png egy szürkeárnyalatos levél,
        # amit itt színezünk arany/ezüst/bronzra (multiply blend), ahogy
        # a referencia képen is utólag lett színezve.
        leaf_path = os.path.join(ASSETS_DIR, "TrophyStar.png")
        leaf_raw = pygame.image.load(leaf_path).convert_alpha()
        if _smoothscale_supported():
            leaf_base = pygame.transform.smoothscale(
                leaf_raw, (self.HISCORE_LEAF_SIZE, self.HISCORE_LEAF_SIZE)
            )
        else:
            leaf_base = pygame.transform.scale(
                leaf_raw, (self.HISCORE_LEAF_SIZE, self.HISCORE_LEAF_SIZE)
            )
        self.leaf_gold = self._tint_surface(leaf_base, self.HISCORE_GOLD)
        self.leaf_silver = self._tint_surface(leaf_base, self.HISCORE_SILVER)
        self.leaf_bronze = self._tint_surface(leaf_base, self.HISCORE_BRONZE)

        # Egy nagy, félig áttetsző, zöld keretes panel az egész
        # HIGHSCORES táblázat mögé - csak egyszer épül fel, utána cache-elve
        # blit-eljük rá a háttérre minden render_highscore() hívásnál.
        self.hiscore_panel = self._build_hiscore_panel()

        # --- NAME ENTRY assetek ---
        name_bg_path = os.path.join(ASSETS_DIR, "BGR2_Scoremode.png")
        name_bg_raw = pygame.image.load(name_bg_path).convert()
        self.name_entry_bg = pygame.transform.smoothscale(
            name_bg_raw, (self.SCREEN_W, self.SCREEN_H)
        )

        # --- SUMMARY assetek ---
        summary_bg_path = os.path.join(ASSETS_DIR, "bg640.png")
        summary_bg_raw = pygame.image.load(summary_bg_path).convert()
        self.summary_bg = pygame.transform.smoothscale(
            summary_bg_raw, (self.SCREEN_W, self.SCREEN_H)
        )

        # frame640.png: level-keret, atlatszo (alpha=0) kozeppel, mar eleve
        # pontosan 640x480 - nincs szukseg skalazasra. Legfelul kerul ra
        # minden mas utan, hogy tenyleg ratakarjon a szovegre/particle-okra
        # a szeleknel.
        summary_frame_path = os.path.join(ASSETS_DIR, "frame640.png")
        self.summary_frame = pygame.image.load(summary_frame_path).convert_alpha()

        # --- LOGO assetek ---
        logo_path = os.path.join(ASSETS_DIR, "introscr.png")
        self.logo_img = pygame.image.load(logo_path).convert_alpha()
        self.logo_bg_frames = self._build_logo_bg_frames()

        # --- BEAT THIS SCORE assetek ---
        # beatstate.png a ket ures dobozt (szam + jatekosnev) tartalmazza,
        # a cim (beattitle.png) kulon, atlatszo reteg - kozpontba rakva,
        # pulzalva (lasd render_beat_score). A keret ugyanaz a
        # Thanksframe.png vignette, mint a Special Thanks kepernyon (mar
        # be van toltve self.thx_vignette-kent).
        beat_bg_path = os.path.join(ASSETS_DIR, "beatstate.png")
        self.beat_score_bg = pygame.image.load(beat_bg_path).convert()

        beat_title_path = os.path.join(ASSETS_DIR, "beattitle.png")
        self.beat_title_img = pygame.image.load(beat_title_path).convert_alpha()

        arrow_path = os.path.join(ASSETS_DIR, "arrow.png")
        arrow_raw = pygame.image.load(arrow_path).convert_alpha()
        scale_fn = pygame.transform.smoothscale if _smoothscale_supported() else pygame.transform.scale
        # arrow.png alapból balra mutat
        self.name_arrow_left = scale_fn(arrow_raw, (self.NAME_ARROW_SIZE, self.NAME_ARROW_SIZE))
        self.name_arrow_right = pygame.transform.flip(self.name_arrow_left, True, False)

        # Zöld "kurzor" nyíl a betű alá - ugyanaz az arrow.png, felfelé
        # forgatva (rotate -90) és zöldre tintelve.
        caret_base = scale_fn(arrow_raw, (self.NAME_CARET_SIZE, self.NAME_CARET_SIZE))
        caret_up = pygame.transform.rotate(caret_base, -90)
        self.name_caret = self._tint_surface(caret_up, self.NAME_CARET_COLOR)

        # --- SPECIAL THANKS assetek ---
        thx_bg_path = os.path.join(ASSETS_DIR, "THX_Scr", "ThanksBgr.png")
        thx_bg_raw = pygame.image.load(thx_bg_path).convert()
        self.thx_background = pygame.transform.smoothscale(
            thx_bg_raw, (self.SCREEN_W, self.SCREEN_H)
        )

        # Thanksframe.png: fekete kep, VALTOZO ALFAVAL (a kozepen atlatszo,
        # a szelek fele egyre atlatszatlanabb fekete) - legfelul kerul ra
        # sima alfa-kompozitalassal, hogy besotetitse a kepernyo szeleit
        # (a hatteret ES a szoveget is), a kozep valtozatlan marad.
        thx_frame_path = os.path.join(ASSETS_DIR, "THX_Scr", "Thanksframe.png")
        thx_frame_raw = pygame.image.load(thx_frame_path).convert_alpha()
        scale_fn = pygame.transform.smoothscale if _smoothscale_supported() else pygame.transform.scale
        self.thx_vignette = scale_fn(thx_frame_raw, (self.SCREEN_W, self.SCREEN_H))

    def _build_hiscore_panel(self):
        x, y, w, h = self.HISCORE_PANEL_RECT
        panel = pygame.Surface((w, h), pygame.SRCALPHA)

        fill_color = (*self.HISCORE_PANEL_FILL_COLOR, self.HISCORE_PANEL_FILL_ALPHA)
        pygame.draw.rect(
            panel, fill_color, panel.get_rect(),
            border_radius=self.HISCORE_PANEL_RADIUS
        )
        pygame.draw.rect(
            panel, (*self.HISCORE_PANEL_BORDER_COLOR, 255), panel.get_rect(),
            width=self.HISCORE_PANEL_BORDER_WIDTH, border_radius=self.HISCORE_PANEL_RADIUS
        )
        return panel

    def _build_logo_bg_frames(self):
        """Elore legenerall LOGO_BG_FRAME_COUNT db teljes kepernyos
        "pszichedelikus" hatteret: mindegyik egy kis (LOGO_BG_GRID_W x
        LOGO_BG_GRID_H) racson szamolt HSV szinatmenet, amit felskalazunk
        640x480-ra. A racs atlojan vegigfuto szin-eltolas (LOGO_BG_HUE_SPREAD)
        ad neki teruleti valtozatossagot, a kepkockak kozotti alap-szinarnyalat
        pedig egyenletesen fedi le a teljes szinkort, hogy a ciklus zokkeno-
        mentesen visszaerjen az elejere."""
        gw, gh = self.LOGO_BG_GRID_W, self.LOGO_BG_GRID_H
        scale_fn = pygame.transform.smoothscale if _smoothscale_supported() else pygame.transform.scale
        frames = []
        for i in range(self.LOGO_BG_FRAME_COUNT):
            base_hue = i / self.LOGO_BG_FRAME_COUNT
            small = pygame.Surface((gw, gh))
            for gy in range(gh):
                for gx in range(gw):
                    hue = (base_hue + (gx + gy) / (gw + gh) * self.LOGO_BG_HUE_SPREAD) % 1.0
                    r, g, b = colorsys.hsv_to_rgb(hue, self.LOGO_BG_SATURATION, self.LOGO_BG_VALUE)
                    small.set_at((gx, gy), (int(r * 255), int(g * 255), int(b * 255)))
            frames.append(scale_fn(small, (self.SCREEN_W, self.SCREEN_H)))
        return frames

    @staticmethod
    def _tint_surface(source_surface, color):
        """Egy szürkeárnyalatos (fehér-szürke) surface-t adott színűre tintel
        (BLEND_RGBA_MULT), az alpha csatorna és az árnyalás megmarad."""
        tinted = source_surface.copy()
        tinted.fill((color[0], color[1], color[2], 255), special_flags=pygame.BLEND_RGBA_MULT)
        return tinted

    @staticmethod
    def _cosine_pulse_scale(period_sec, min_scale, max_scale):
        """Lelegzesszeru fel-le pulzalo scale-erek: period_sec alatt egy
        teljes kor, max_scale-rol indul, period_sec/2-nel eri el a
        min_scale-t, majd vissza. Ugyanaz a keplet, mint a PRESS_START
        kepernyon (lasd PRESS_START_PULSE_* konstansok)."""
        phase = (time.time() % period_sec) / period_sec
        mid = (max_scale + min_scale) / 2
        half_range = (max_scale - min_scale) / 2
        return mid + half_range * math.cos(phase * 2 * math.pi)

    def _blit_scaled_centered(self, surface, center, scale):
        """Egy surface-t adott scale-lel (kozeppont megtartasaval) rajzol ki."""
        if scale != 1.0:
            w = max(1, int(surface.get_width() * scale))
            h = max(1, int(surface.get_height() * scale))
            scale_fn = pygame.transform.smoothscale if _smoothscale_supported() else pygame.transform.scale
            surface = scale_fn(surface, (w, h))
        self.screen.blit(surface, surface.get_rect(center=center))

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

    def get_bounce_scale(self, t):
        """Egy egyszerű 'overshoot' (bounce) animáció."""
        # 0.0 -> 1.0 közötti érték, t = idő (0-1)
        # Ez egy sima back-out easing
        if t >= 1.0: return 1.0
        return 1.0 + 0.3 * math.sin(t * math.pi) * (1.0 - t)

    def _draw_animated(self, surface, center, start_time, elapsed):
        """
        Kirajzolja az elemet lassan induló (ease-in) animációval.
        start_time: mikor kell elindulnia ennek az elemnek (0.0, 1.0, 2.0...)
        """
        duration = 0.6 # Animáció hossza
        t = (elapsed - start_time) / duration
        
        # Ha még nem jött el az idő, ne rajzoljunk semmit
        if t < 0:
            return 
            
        # Clamp t 0 és 1 közé
        t = min(1.0, t)
        
        # Ease-in: t * t * t (gyorsuló hatás)
        scale = t * t * t
        
        # Rajzolás
        w = max(1, int(surface.get_width() * scale))
        h = max(1, int(surface.get_height() * scale))
        if _smoothscale_supported():
            scaled = pygame.transform.smoothscale(surface, (w, h))
        else:
            scaled = pygame.transform.scale(surface, (w, h))
        rect = scaled.get_rect(center=center)
        self.screen.blit(scaled, rect)

    # Score Screen Rendering
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

        # "player" felirat pozíciója átszámolva a felhőhöz (190*0.8, 105*0.8 -> 152, 84)
        if self._active_player_cache_key != state.current_player:
            self._active_player_cache = self.font_active_player.render(
                f"Player: {state.current_player}", True, self.COLOR_ACTIVE
            )
            self._active_player_cache_key = state.current_player
        active_player_rect = self._active_player_cache.get_rect(center=(515, 90))
        self.screen.blit(self._active_player_cache, active_player_rect)

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
            blur_correction = self.SHADOW_BLUR_RADIUS if _blur_supported() else 0
            shadow_x = actual_x + shadow_offset - blur_correction
            shadow_y = actual_y - blur_correction
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

        
        pygame.display.flip()

    def players_order(self):
        return [1, 2, 3, 4]

    def poll_pygame_events(self):
        if not self.active:
            return []
        return pygame.event.get()

    def has_quit_event(self, pygame_events) -> bool:
        return any(e.type == pygame.QUIT for e in pygame_events)

    def has_quit_key_event(self, pygame_events) -> bool:
        """Q billentyu - kilepes a progibol a parancssorba. Csak akkor
        ellenorizzuk, ha NEM vagyunk a szerviz menuben (lasd main.py),
        hogy ne utkozzon egy oda begepelt "Q"-val."""
        return any(e.type == pygame.KEYDOWN and e.key == pygame.K_q for e in pygame_events)

    def start_fade_transition(self):
        """Pillanatkepet keszit a JELENLEGI kepernyotartalomrol (az elozo
        allapot utolso kirajzolt kepe), hogy a kovetkezo nehany frame-ben
        draw_fade_overlay() elhalvanyithassa fole az uj allapot tartalmat.
        A main.py hivja allapotvaltaskor, MIELOTT az uj allapot render_*
        fuggvenye lefut."""
        if not self.active or self.screen is None:
            return
        self._fade_snapshot = self.screen.copy()
        self._fade_start = time.time()

    def draw_fade_overlay(self):
        """Ha van folyamatban levo crossfade, ratolja a regi kepernyokepet
        (csokkeno set_alpha-val) a mar kirajzolt uj tartalomra, majd ujra
        flip-el. A main.py minden frame-ben hivja, a rendes render_* hivas
        UTAN - ha nincs aktiv fade, azonnal visszater, nincs extra koltseg.
        Szandekosan surface.set_alpha()-t hasznal (nem SRCALPHA feluletre
        komponalast) - lasd FADE_DURATION_SEC kommentje."""
        if self._fade_snapshot is None:
            return
        elapsed = time.time() - self._fade_start
        if elapsed >= self.FADE_DURATION_SEC:
            self._fade_snapshot = None
            return
        alpha = max(0, min(255, int(255 * (1 - elapsed / self.FADE_DURATION_SEC))))
        self._fade_snapshot.set_alpha(alpha)
        self.screen.blit(self._fade_snapshot, (0, 0))
        pygame.display.flip()

    # SUMMARY SCREEN RENDERING
    def render_summary(self, summary_data):
        """
        Kirajzolja a bónusz összegző felületet, ha SUMMARY állapotban vagyunk.
        """
        now = time.time()

        if self.summary_anim_start is None:
            self.summary_anim_start = now
            self._bonus_spark_burst = None
            self._bonus_spark_spawned = False

        elapsed = now - self.summary_anim_start

        # Háttér (bg640.png), a keret (frame640.png) legfelul, a fuggveny vegen kerul ra
        self.screen.blit(self.summary_bg, (0, 0))

        # Adatok kibontása
        p_num = summary_data.get("player", 1)
        score = summary_data.get("old_score", 0)
        mult = summary_data.get("multiplier", 1)
        bonus = summary_data.get("bonus_points", 0)
        total = score + bonus

        # 1. PLAYER (0-1 mp)
        p_surf = build_outlined_text_surface(self.font_summary_title, f"PLAYER {p_num}", (255, 215, 0), self.COLOR_TEXT_OUTLINE, 3)
        self._draw_animated(p_surf, (self.SCREEN_W // 2, self.SUMMARY_PLAYER_Y), 0.0, elapsed)


        # 2. SCORE (1-2 mp)
        score_surf = build_outlined_text_surface(self.font_summary_mid, f"SCORE: {score:,}", self.COLOR_TEXT, self.COLOR_TEXT_OUTLINE, 2)
        self._draw_animated(score_surf, (self.SCREEN_W // 2, self.SUMMARY_SCORE_Y), 1.0, elapsed)


        # 3. BONUS (2-3 mp)
        mult_surf = build_outlined_text_surface(self.font_summary_mid, f"BONUS {mult}x", (240, 190, 40), self.COLOR_TEXT_OUTLINE, 2)
        self._draw_animated(mult_surf, (self.SCREEN_W // 2, self.SUMMARY_BONUS_LABEL_Y), 2.0, elapsed)


        # 4. +BONUS (3-4 mp) - ugyanakkora, mint az elotte levo sorok
        bonus_surf = build_outlined_text_surface(self.font_summary_mid, f"+{bonus:,}", (255, 255, 255), self.COLOR_TEXT_OUTLINE, 2)
        self._draw_animated(bonus_surf, (self.SCREEN_W // 2, self.SUMMARY_BONUS_VALUE_Y), 3.0, elapsed)

        # 5. TOTAL (4-5 mp) - a vegleges (pontszam+bonusz) osszeg, ez a
        # kiemelt nagy sor (ott, ahol korabban a +bonus volt), utana meg
        # tobb masodpercig allva marad a kepernyo (lasd SUMMARY_DURATION_SEC)
        total_surf = build_outlined_text_surface(self.font_summary_score, f"{total:,}", (255, 215, 0), self.COLOR_TEXT_OUTLINE, 3)
        self._draw_animated(total_surf, (self.SCREEN_W // 2, self.SUMMARY_TOTAL_Y), 4.0, elapsed)

        # Szikra-effekt, amikor a TOTAL sor megjelenik - ez mindig van
        # (a bonusz lehet 0, akkor nem lenne mit unnepelni), csak egyszer indul
        if elapsed >= self.SUMMARY_SPARK_REVEAL_TIME and not self._bonus_spark_spawned:
            self._bonus_spark_burst = ParticleBurst(
                (self.SCREEN_W // 2, self.SUMMARY_TOTAL_Y),
                self.SUMMARY_SPARK_COUNT, self.SUMMARY_SPARK_COLORS,
                self.SUMMARY_SPARK_SPEED_RANGE, self.SUMMARY_SPARK_LIFETIME_RANGE, self.SUMMARY_SPARK_SIZE_RANGE,
            )
            self._bonus_spark_spawned = True
        if self._bonus_spark_burst is not None:
            self._bonus_spark_burst.draw(self.screen)

        # Level-keret legfelul - mindenre (szoveg, szikrak) ratakar a szeleknel
        self.screen.blit(self.summary_frame, (0, 0))

        pygame.display.flip()

    def render_final_scores(self, final_scores: dict, player_count: int):
        """Tobb-jatekos vegeredmeny kepernyo: csak akkor jon elo, ha 2+
        jatekos jatszott es a jatek valodi GAMEOVER-rel ert veget. Minden
        jatekos vegso pontszamat mutatja 2x2 racsban, a gyoztest (legmagasabb
        pontszam) arany szinnel es level-ikonnal kiemelve."""
        if not self.active:
            return

        now = time.time()
        if self.final_scores_start is None:
            self.final_scores_start = now
            self._firework_bursts = []
            self._next_firework_time = now  # az elso robbanas szinte azonnal induljon

        self.screen.blit(self.summary_bg, (0, 0))

        active_scores = {p: final_scores.get(p, 0) for p in range(1, player_count + 1)}
        winner_score = max(active_scores.values()) if active_scores else 0

        winner_scale = self._cosine_pulse_scale(
            self.FINAL_SCORES_WINNER_PULSE_PERIOD_SEC,
            self.FINAL_SCORES_WINNER_PULSE_MIN_SCALE,
            self.FINAL_SCORES_WINNER_PULSE_MAX_SCALE,
        )

        winner_positions = []

        for player_num, (x, y) in self.FINAL_SCORES_SLOT_POS.items():
            if player_num > player_count:
                continue

            score = active_scores.get(player_num, 0)
            is_winner = score == winner_score
            # A gyoztes a jelenlegi (nagyobb) meretet kapja es pulzal, a
            # tobbi jatekos 10-zel kisebb, statikus betuvel jelenik meg.
            label_font = self.font_summary_title if is_winner else self.font_final_label
            value_font = self.font_summary_mid if is_winner else self.font_final_value
            color = self.FINAL_SCORES_WINNER_COLOR if is_winner else (255, 255, 255)
            scale = winner_scale if is_winner else 1.0

            label_surf = build_outlined_text_surface(
                label_font, f"PLAYER {player_num}", color, self.COLOR_TEXT_OUTLINE, 2
            )
            self._blit_scaled_centered(label_surf, (x, y), scale)

            score_surf = build_outlined_text_surface(
                value_font, f"{score:,}", color, self.COLOR_TEXT_OUTLINE, 2
            )
            self._blit_scaled_centered(score_surf, (x, y + self.FINAL_SCORES_VALUE_OFFSET_Y), scale)

            if is_winner:
                self._blit_scaled_centered(
                    self.leaf_gold, (x, y - self.FINAL_SCORES_LEAF_OFFSET_Y), scale
                )
                winner_positions.append((x, y))

        # Ismetlodo kis tuzijatek-robbanasok a gyoztes(ek) korul
        if winner_positions and now >= self._next_firework_time:
            spread = self.FINAL_SCORES_FIREWORK_SPREAD
            ox, oy = random.choice(winner_positions)
            origin = (ox + random.uniform(-spread, spread), oy + random.uniform(-spread, spread))
            self._firework_bursts.append(ParticleBurst(
                origin, self.FINAL_SCORES_FIREWORK_COUNT, self.FINAL_SCORES_FIREWORK_COLORS,
                self.FINAL_SCORES_FIREWORK_SPEED_RANGE, self.FINAL_SCORES_FIREWORK_LIFETIME_RANGE,
                self.FINAL_SCORES_FIREWORK_SIZE_RANGE, gravity=120.0,
            ))
            self._next_firework_time = now + random.uniform(*self.FINAL_SCORES_FIREWORK_INTERVAL_RANGE)

        self._firework_bursts = [b for b in self._firework_bursts if b.is_alive()]
        for burst in self._firework_bursts:
            burst.draw(self.screen)

        # Level-keret legfelul - a pulzalo gyoztes-szoveg/tuzijatek se logjon ki alola
        self.screen.blit(self.summary_frame, (0, 0))

        pygame.display.flip()

    @staticmethod
    def _hiscore_rank_label(rank: int) -> str:
        """Angol sorszám-utótagok, a Unity referencia (HighscoreTable.cs)
        CreateHighscoreEntryTransform() logikáját követve."""
        if rank == 1:
            return "1ST"
        if rank == 2:
            return "2ND"
        if rank == 3:
            return "3RD"
        return f"{rank}TH"

    def _hiscore_leaf_for_rank(self, index: int):
        if index == 0:
            return self.leaf_gold
        if index == 1:
            return self.leaf_silver
        if index == 2:
            return self.leaf_bronze
        return None

    def render_highscore(self, scores):
        self.screen.blit(self.background3, (0, 0))  # HiScoreBg.png - nyers autós/dzsungel háttér

        # A nagy panel, ami elválasztja a táblázatot a háttértől
        panel_x, panel_y, _, _ = self.HISCORE_PANEL_RECT
        self.screen.blit(self.hiscore_panel, (panel_x, panel_y))

        # Cím
        title = build_outlined_text_surface(
            self.font_hiscore_title, "HIGHSCORES",
            (255, 255, 255), self.COLOR_TEXT_OUTLINE, 2
        )
        self.screen.blit(title, title.get_rect(center=(self.SCREEN_W // 2, self.HISCORE_TITLE_Y)))

        # Fejléc - külön-külön rajzolva, pontosan az oszlopok fölé igazítva
        pos_h = build_outlined_text_surface(
            self.font_hiscore_header, "POS", self.HISCORE_GOLD, self.COLOR_TEXT_OUTLINE, 2
        )
        self.screen.blit(pos_h, (self.HISCORE_COL_POS_X, self.HISCORE_HEADER_Y - pos_h.get_height() // 2))

        score_h = build_outlined_text_surface(
            self.font_hiscore_header, "SCORE", self.HISCORE_GOLD, self.COLOR_TEXT_OUTLINE, 2
        )
        self.screen.blit(score_h, score_h.get_rect(center=(self.HISCORE_COL_SCORE_X, self.HISCORE_HEADER_Y)))

        name_h = build_outlined_text_surface(
            self.font_hiscore_header, "NAME", self.HISCORE_GOLD, self.COLOR_TEXT_OUTLINE, 2
        )
        self.screen.blit(name_h, (self.HISCORE_COL_NAME_X, self.HISCORE_HEADER_Y - name_h.get_height() // 2))

        # Sorok
        for i, entry in enumerate(scores[:10]):
            y = self.HISCORE_ROW_START_Y + i * self.HISCORE_ROW_SPACING
            color = (50, 255, 50) if i == 0 else (255, 255, 255)  # csak az 1. hely zöld

            # POS
            pos_surf = build_outlined_text_surface(
                self.font_hiscore_row, self._hiscore_rank_label(i + 1), color, self.COLOR_TEXT_OUTLINE, 1
            )
            self.screen.blit(pos_surf, (self.HISCORE_COL_POS_X, y - pos_surf.get_height() // 2))

            # SCORE (nincs ezres tagoló, ahogy a referencián sem)
            score_surf = build_outlined_text_surface(
                self.font_hiscore_row, f"{entry['score']}", color, self.COLOR_TEXT_OUTLINE, 1
            )
            self.screen.blit(score_surf, score_surf.get_rect(right=self.HISCORE_COL_SCORE_RIGHT_X, centery=y))

            # NAME
            name_surf = build_outlined_text_surface(
                self.font_hiscore_row, entry['name'], color, self.COLOR_TEXT_OUTLINE, 1
            )
            self.screen.blit(name_surf, (self.HISCORE_COL_NAME_X, y - name_surf.get_height() // 2))

            # Levél ikon a TOP3-nak
            leaf = self._hiscore_leaf_for_rank(i)
            if leaf is not None:
                leaf_rect = leaf.get_rect(center=(self.HISCORE_LEAF_X, y))
                self.screen.blit(leaf, leaf_rect)

        pygame.display.flip()

    def _blit_multicolor_line(self, parts, font, center_x, y):
        """parts: [(szöveg, szín), ...] - egy sorba rajzolja, a teljes sor
        középre igazítva center_x körül (pl. "Press " fehér + "Start" piros)."""
        surfaces = [
            build_outlined_text_surface(font, text, color, self.COLOR_TEXT_OUTLINE, 1)
            for text, color in parts
        ]
        total_w = sum(s.get_width() for s in surfaces)
        x = center_x - total_w // 2
        for s in surfaces:
            self.screen.blit(s, (x, y - s.get_height() // 2))
            x += s.get_width()

    def render_name_entry(self, name_entry, player_num=1):
        """A hiscore név-beíró képernyő: 3 karakter, bal/jobb nyilakkal
        váltva, zöld kurzorral az aktuális pozíció alatt."""
        self.screen.blit(self.name_entry_bg, (0, 0))

        # Cím: "Player X"
        title = build_outlined_text_surface(
            self.font_name_title, f"Player {player_num}",
            (255, 255, 255), self.COLOR_TEXT_OUTLINE, 2
        )
        self.screen.blit(title, title.get_rect(center=(self.SCREEN_W // 2, self.NAME_TITLE_Y)))

        # Bal/jobb nyilak
        left_rect = self.name_arrow_left.get_rect(center=(self.NAME_ARROW_LEFT_X, self.NAME_LETTERS_Y))
        self.screen.blit(self.name_arrow_left, left_rect)
        right_rect = self.name_arrow_right.get_rect(center=(self.NAME_ARROW_RIGHT_X, self.NAME_LETTERS_Y))
        self.screen.blit(self.name_arrow_right, right_rect)

        # 3 karakter + kurzor az aktuális pozíció alatt
        chars = name_entry.get_chars()
        for i, ch in enumerate(chars):
            x = self.NAME_LETTER_X[i]
            letter_surf = build_outlined_text_surface(
                self.font_name_letters, ch, (255, 255, 255), self.COLOR_TEXT_OUTLINE, 2
            )
            self.screen.blit(letter_surf, letter_surf.get_rect(center=(x, self.NAME_LETTERS_Y)))

            if i == name_entry.cursor and not name_entry.done:
                caret_rect = self.name_caret.get_rect(
                    center=(x, self.NAME_LETTERS_Y + self.NAME_CARET_OFFSET_Y)
                )
                self.screen.blit(self.name_caret, caret_rect)

        # Hint szövegek: "Press Start to Skip" / "Press Shoot to Next"
        # (a fizikai gomb nálunk a Player (P) gomb - lásd state_machine)
        self._blit_multicolor_line(
            [("Press ", (255, 255, 255)), ("Start", self.NAME_HINT_START_COLOR), (" to Skip", (255, 255, 255))],
            self.font_name_hint, self.NAME_HINT_LEFT_X, self.NAME_HINT_Y
        )
        self._blit_multicolor_line(
            [("Press ", (255, 255, 255)), ("Player", self.NAME_HINT_SHOOT_COLOR), (" to Next", (255, 255, 255))],
            self.font_name_hint, self.NAME_HINT_RIGHT_X, self.NAME_HINT_Y
        )

        pygame.display.flip()

    def render_logo(self):
        """LOGO (attract-mode) képernyő: a "Cheech & Chong Pinball" logó
        (introscr.png, átlátszó) egy folyamatosan hullámzó pszichedelikus
        háttér fölött. A háttér előre legenerált képkockák (logo_bg_frames)
        közötti lágy alfa-áttűnéssel ciklizál, futásidőben nincs per-pixel
        számolás."""
        if not self.active:
            return

        now = time.time()
        if self.logo_anim_start is None:
            self.logo_anim_start = now
        elapsed = now - self.logo_anim_start

        n = len(self.logo_bg_frames)
        frame_duration = self.LOGO_BG_CYCLE_SEC / n
        pos = (elapsed % self.LOGO_BG_CYCLE_SEC) / frame_duration
        idx = int(pos) % n
        next_idx = (idx + 1) % n
        blend = pos - int(pos)

        current_frame = self.logo_bg_frames[idx]
        current_frame.set_alpha(255)
        self.screen.blit(current_frame, (0, 0))

        next_frame = self.logo_bg_frames[next_idx]
        next_frame.set_alpha(int(blend * 255))
        self.screen.blit(next_frame, (0, 0))

        self.screen.blit(self.logo_img, (0, 0))

        pygame.display.flip()

    def render_beat_score(self, scores):
        """BEAT THIS SCORE (attract-mode) képernyő: a beatstate.png hátteret
        (Cheech & Chong + felirat + két üres doboz) egészíti ki a #1 hiscore
        pontszámával és a nevével, majd a Thanksframe.png vignette kerül
        legfelülre."""
        if not self.active:
            return

        if self.beat_score_start is None:
            self.beat_score_start = time.time()
            self._beat_score_spark_burst = None
            self._beat_score_spark_spawned = False

        self.screen.blit(self.beat_score_bg, (0, 0))

        title_scale = self._cosine_pulse_scale(
            self.BEAT_TITLE_PULSE_PERIOD_SEC, self.BEAT_TITLE_PULSE_MIN_SCALE, self.BEAT_TITLE_PULSE_MAX_SCALE
        )
        self._blit_scaled_centered(self.beat_title_img, (self.BEAT_TITLE_X, self.BEAT_TITLE_Y), title_scale)

        top_entry = scores[0] if scores else {"name": "---", "score": 0}

        number_surf = build_outlined_text_surface(
            self.font_beat_number, f"{top_entry['score']:,}",
            self.BEAT_SCORE_NUMBER_COLOR, self.COLOR_TEXT_OUTLINE, 2
        )
        elapsed = time.time() - self.beat_score_start
        self._draw_animated(number_surf, (self.SCREEN_W // 2, self.BEAT_SCORE_NUMBER_Y), 0.0, elapsed)

        if not self._beat_score_spark_spawned:
            self._beat_score_spark_burst = ParticleBurst(
                (self.SCREEN_W // 2, self.BEAT_SCORE_NUMBER_Y),
                self.BEAT_SCORE_SPARK_COUNT, self.BEAT_SCORE_SPARK_COLORS,
                self.BEAT_SCORE_SPARK_SPEED_RANGE, self.BEAT_SCORE_SPARK_LIFETIME_RANGE, self.BEAT_SCORE_SPARK_SIZE_RANGE,
            )
            self._beat_score_spark_spawned = True
        if self._beat_score_spark_burst is not None:
            self._beat_score_spark_burst.draw(self.screen)

        player_surf = build_outlined_text_surface_spaced(
            self.font_beat_player, top_entry["name"],
            self.BEAT_SCORE_PLAYER_COLOR, self.COLOR_TEXT_OUTLINE, 2,
            extra_spacing=self.BEAT_SCORE_PLAYER_LETTER_SPACING,
        )
        self.screen.blit(player_surf, player_surf.get_rect(center=(self.SCREEN_W // 2, self.BEAT_SCORE_PLAYER_Y)))

        self.screen.blit(self.thx_vignette, (0, 0))

        pygame.display.flip()

    def render_press_start(self):
        """Attract-mode "Press Start to Play!" képernyő: zöld sugárirányú
        háttér (bg640.png, ugyanaz, mint a SUMMARY/FINAL_SCORES képernyőn),
        rajta lélegzésszerűen pulzáló (fel-le méretező) felirat."""
        if not self.active:
            return

        self.screen.blit(self.summary_bg, (0, 0))

        if self._press_start_cache is None:
            line_surfaces = [
                build_outlined_text_surface(
                    self.font_press_start, line, (255, 255, 255), self.COLOR_TEXT_OUTLINE, 3
                )
                for line in self.PRESS_START_LINES
            ]
            # NE egy kozos SRCALPHA "block" feluletre komponaljuk ossze a
            # sorokat (lasd render_special_thanks kommentje - 32 bites
            # ARM-on Bus Error-t okoz a szeles SRCALPHA-ra-SRCALPHA blit).
            # Helyette minden sort kulon tarolunk, a teljes "block" kozepehez
            # viszonyitott fuggoleges eltolassal, es pulzalaskor mindegyiket
            # kulon-kulon skalazzuk/rajzoljuk kozvetlenul a kepernyore.
            total_h = sum(s.get_height() for s in line_surfaces) + self.PRESS_START_LINE_GAP * (len(line_surfaces) - 1)
            offsets = []
            y = 0
            for s in line_surfaces:
                offsets.append((y + s.get_height() / 2) - total_h / 2)
                y += s.get_height() + self.PRESS_START_LINE_GAP
            self._press_start_cache = list(zip(line_surfaces, offsets))

        scale = self._cosine_pulse_scale(
            self.PRESS_START_PULSE_PERIOD_SEC, self.PRESS_START_PULSE_MIN_SCALE, self.PRESS_START_PULSE_MAX_SCALE
        )

        center_x = self.SCREEN_W // 2
        center_y = self.SCREEN_H // 2
        for surf, offset in self._press_start_cache:
            self._blit_scaled_centered(surf, (center_x, center_y + offset * scale), scale)

        pygame.display.flip()

    def render_special_thanks(self, names):
        """Special Thanks képernyő: htr (ThanksBgr.png) -> felfelé görgetett
        névlista -> vignette (Thanksframe.png) legfelül (BLEND_MULT-tal
        besötétítve a szélek). A cím a képernyő közepéről indul, és a teljes
        THX_SCROLL_DURATION_SEC alatt lineárisan (nincs easing) felfelé
        csúszik, amíg az utolsó sor a cím induló pozíciójára nem ér.

        names: a szerviz menüben szerkeszthető névlista (a "Special Thanks
        to" cím mindig fix, elé kerül)."""
        if not self.active:
            return

        now = time.time()
        if self.thx_scroll_start is None:
            self.thx_scroll_start = now
        elapsed = now - self.thx_scroll_start

        self.screen.blit(self.thx_background, (0, 0))

        lines = ("Special Thanks to",) + tuple(names)

        if self._thx_text_cache is None:
            line_surfaces = [
                build_outlined_text_surface(
                    self.font_thx, line, (255, 255, 255), self.COLOR_TEXT_OUTLINE, 3
                )
                for line in lines
            ]
            # NE egy szeles kozos SRCALPHA "block" feluletre komponaljuk ossze
            # a sorokat, majd azt blit-eljuk egyben - ez 32 bites ARM-on
            # (armv7l/armv6l) Bus Error-t okoz szeles (~500px+) SRCALPHA-ra-
            # SRCALPHA blitnel (ugyanaz a kategoria hardveres/SDL-bug, mint a
            # mar ismert smoothscale/blur ARM-problema). Helyette minden sort
            # KULON, kozvetlenul a kepernyore (self.screen, ami NEM SRCALPHA)
            # rajzolunk - ez mar bizonyítottan biztonsagos minta a tobbi
            # kepernyon.
            self._thx_text_cache = line_surfaces
            self._thx_line_gap = round(self.font_thx.get_linesize() * self.THX_LINE_SPACING_MULT)
            self._thx_line_count = len(lines)

        line_surfaces = self._thx_text_cache
        progress = min(elapsed / self.THX_SCROLL_DURATION_SEC, 1.0)
        scroll_distance = self._thx_line_gap * (self._thx_line_count - 1)
        y_offset = progress * scroll_distance

        first_line_center_y = (self.SCREEN_H // 2) - y_offset
        for i, s in enumerate(line_surfaces):
            line_center_y = first_line_center_y + i * self._thx_line_gap
            self.screen.blit(s, s.get_rect(center=(self.SCREEN_W // 2, line_center_y)))

        self.screen.blit(self.thx_vignette, (0, 0))

        pygame.display.flip()

    def _draw_service_line(self, text, y, selected):
        color = (255, 230, 90) if selected else (220, 220, 225)
        prefix = "> " if selected else "  "
        surf = self.font_service_item.render(prefix + text, True, color)
        self.screen.blit(surf, (30, y))

    def render_service_menu(self, controller):
        """Titkos szerviz menu (Ctrl+M) - egyszeru, sotet, sok-soros
        listakent renderelt kepernyo (nem Modak font, ez nem a jatek
        markazott felulete, hanem egy rejtett utility)."""
        if not self.active:
            return

        self.screen.fill(self.SERVICE_MENU_BG_COLOR)

        title_map = {
            "main": "SZERVIZ MENU",
            "hiscore_edit": "HISCORE SZERKESZTES",
            "hiscore_delete_confirm": "TOROLJUK?",
            "thanks_edit": "SPECIAL THANKS NEVEK",
            "thanks_add_input": "UJ NEV",
            "input_test": "INPUT / GOMB TESZT",
            "serial_monitor": "SERIAL MONITOR (RAW)",
            "reset_confirm": "OSSZES HISCORE TORLESE",
            "version_info": "VERZIO INFO",
        }
        title_surf = self.font_service_title.render(title_map.get(controller.screen, ""), True, (255, 255, 255))
        self.screen.blit(title_surf, (30, 24))
        pygame.draw.line(self.screen, (80, 80, 90), (30, 60), (self.SCREEN_W - 30, 60), 2)

        y = 80
        line_h = 28
        hint = ""

        if controller.screen == "main":
            for i, (_, label) in enumerate(controller.MAIN_ITEMS):
                self._draw_service_line(label, y + i * line_h, i == controller.cursor)
            hint = "Fel/Le: navigalas   Enter: kivalaszt   Esc: kilepes"

        elif controller.screen == "hiscore_edit":
            for i, entry in enumerate(controller.score_manager.scores):
                selected = i == controller.cursor
                color = (255, 230, 90) if selected else (220, 220, 225)
                row_y = y + i * line_h
                left_surf = self.font_service_item.render(
                    f"{'> ' if selected else '  '}{i + 1:2d}.  {entry['name']:<4}", True, color
                )
                self.screen.blit(left_surf, (30, row_y))
                score_surf = self.font_service_item.render(f"{entry['score']:,}", True, color)
                score_rect = score_surf.get_rect(right=self.SCREEN_W - 30, top=row_y)
                self.screen.blit(score_surf, score_rect)
            hint = "Fel/Le: navigalas   Enter/Del: torles   Esc: kilepes attract modba"

        elif controller.screen == "hiscore_delete_confirm":
            idx = controller._pending_delete_index
            entry = controller.score_manager.scores[idx] if idx is not None else {"name": "---", "score": 0}
            self._draw_service_line(f"Torlod: {entry['name']}  {entry['score']:,} ?", y, False)
            self._draw_service_line("Y - Igen      N - Nem", y + line_h * 2, False)
            hint = "Y: torles   N/Esc: megse"

        elif controller.screen == "thanks_edit":
            names = controller.thanks_manager.names
            if not names:
                self._draw_service_line("(nincs meg nev)", y, False)
            for i, name in enumerate(names):
                self._draw_service_line(f"{i + 1}. {name}", y + i * line_h, i == controller.cursor)
            hint = "Fel/Le: navigalas   A: uj nev   Del: torles   Esc: vissza"

        elif controller.screen == "thanks_add_input":
            self._draw_service_line("Uj nev:", y, False)
            self._draw_service_line(controller._text_input_buffer + "_", y + line_h, True)
            hint = "Enter: mentes   Esc: megse"

        elif controller.screen == "input_test":
            now = time.time()
            events = list(reversed(controller.recent_events))
            if not events:
                self._draw_service_line("(meg nincs esemeny)", y, False)
            for i, (ts, kind) in enumerate(events):
                ago = now - ts
                self._draw_service_line(f"{kind:<18} {ago:5.1f}s", y + i * line_h, i == 0)
            hint = "Esc: vissza"

        elif controller.screen == "serial_monitor":
            now = time.time()
            if controller.serial_reader is None:
                self._draw_service_line("(nincs soros kapcsolat konfiguralva)", y, False)
            else:
                raw_lines = list(reversed(controller.serial_reader.get_raw_log()))
                if not raw_lines:
                    self._draw_service_line("(meg nem erkezett adat)", y, False)
                max_rows = 13
                for i, (ts, line) in enumerate(raw_lines[:max_rows]):
                    ago = now - ts
                    display = line if len(line) <= 40 else line[:37] + "..."
                    self._draw_service_line(f"{ago:5.1f}s  {display}", y + i * line_h, i == 0)
            hint = "Esc: vissza"

        elif controller.screen == "reset_confirm":
            self._draw_service_line("Biztosan torlod az OSSZES hiscore-t?", y, False)
            self._draw_service_line("Y - Igen      N - Nem", y + line_h * 2, False)
            hint = "Y: torles   N/Esc: megse"

        elif controller.screen == "version_info":
            for i, line in enumerate(controller.get_version_info_lines()):
                self._draw_service_line(line, y + i * line_h, False)
            hint = "Esc: vissza"

        if controller.status_message:
            status_surf = self.font_service_item.render(controller.status_message, True, (120, 220, 120))
            self.screen.blit(status_surf, (30, self.SCREEN_H - 60))

        hint_surf = self.font_service_hint.render(hint, True, (140, 140, 150))
        self.screen.blit(hint_surf, (30, self.SCREEN_H - 28))

        pygame.display.flip()