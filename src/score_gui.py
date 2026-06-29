"""Pontszám-kijelző GUI, pygame-mel, framebuffer/DRM kimenetre.

PC-n (VS Code-ban) teszteléskor sima ablakban fut, mert ez a kód
csak Linuxon kényszeríti rá az SDL_VIDEODRIVER=kmsdrm drivert.
Windows-on és macOS-en a normál, natív SDL driver marad érvényben,
ami sima ablakot nyit.

A Raspberry Pi-n (Linux) explicit kmsdrm driverre állítva direktben
a framebufferre/DRM-re rajzol, X11/Wayland nélkül.

Grafikai felépítés:
- htr.png: statikus háttér (dzsungel-keret + "Ball" felhő-buborék
  már beleégetve a képbe), 800x600-ra nyújtva.
- cigip.jpg: cigipapír-textúra, amiből kódból (forgatással) készül
  a játékos-kártyák, rájuk rajzolt névvel és pontszámmal.

Player selector / kártya-animáció:
- A state.active_player_count (1-4) mondja meg, hány kártya legyen
  látható egyszerre. Amikor ez nő, az új kártya alulról csúszik be
  a képbe (ease-out animáció). Amikor visszaugrik 1-re, minden
  "extra" kártya lecsúszik a képből.
- A CardAnimator osztály kezeli ezt: minden kártyához nyilvántart
  egy animáció-kezdési időt és irányt (be/ki), és a render() ezt
  használja az aktuális Y-eltolás kiszámításához.
"""

import math
import time
import pygame
import os
import sys

# A kmsdrm drivert KIZAROLAG Linuxon allitjuk be - ez letezik csak
# Linux DRM/KMS rendszereken (mint a Raspberry Pi OS Lite). Windows-on
# és macOS-en ez a driver nem letezik, es hibara futna, ha mindig
# beallitanank. Igy PC-n a sima, natív SDL ablak-driver mukodik tovabb.
if sys.platform.startswith("linux"):
    os.environ.setdefault("SDL_VIDEODRIVER", "kmsdrm")


ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")


def build_outlined_text_surface(font, text, fill_color, outline_color, outline_width=2):
    """
    Felépít egy KÉSZ surface-t kontúrozott (körvonalas) szöveggel -
    olyan stílusban, mint a screenshot fehér szövege sötét körvonallal.

    A trükk: a szöveget körvonal-színnel egy kicsit nagyobb surface-re
    rajzoljuk 8 irányba elcsúsztatva, majd a tetejére kerül a tényleges
    szín. Surface-t ad vissza (nem rajzol semmire kívülről), hogy a
    hívó cache-elhesse, ha a szöveg nem változik gyakran.
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
    Elkeszit egy fekete "arnyek" verziot a megadott (mar elforgatott)
    surface-bol, ugyanazzal az alpha-maszkkal, csak fekete szinnel es
    csokkentett opacitassal.

    Igy az arnyek PONTOSAN a kartya alakjat koveti (a forgatott
    cigipapir korvonalat, beleertve az SRCALPHA miatt transzparens
    sarkokat), nem egy szögletes negyzetet - ez termeszetesebb hatast
    ad, mint egy sima fekete teglalap.

    opacity: 0-255 kozotti ertek. 15% opacitas kb. 38 (255 * 0.15).

    Modszer: BLEND_RGBA_MULT-tal a szineket feketere visszuk (minden
    csatornat 0-val szorzunk), majd a surface egeszere ráallitjuk az
    atlathatosagot set_alpha()-val, ami a per-pixel alpha-t MEGTARTVA
    (csak skalazza) adja a vegso athalvanyitast - igy a kartya alakja
    (a transzparens sarkok) megmarad, de az egesz arnyek halvanyabb.

    blur_radius: a gaussian blur sugara pixelben. 0 = nincs blur (eles
    arnyek). Minel nagyobb, annal lagyabb/szelesebb az elmosodas.
    pygame-ce >= 2.2.0 szukseges hozza (pygame.transform.gaussian_blur).
    """
    shadow = source_surface.copy()
    # minden szin-csatornat 0-ra (feketere) viszunk, az alpha-csatorna
    # ERINTETLEN marad ezzel a blend mod-dal
    shadow.fill((0, 0, 0, 255), special_flags=pygame.BLEND_RGBA_MULT)

    if blur_radius > 0:
        # A blur "kifolyna" a surface eredeti hataran tul is, ezert
        # elobb egy nagyobb, paddelt surface-re masoljuk kozepre, majd
        # ARRA alkalmazzuk a blurt - kulonben a blur level vagva
        # maradna a szeleknel, es nem lenne igazi lagy elmosodas.
        padded_w = shadow.get_width() + blur_radius * 2
        padded_h = shadow.get_height() + blur_radius * 2
        padded = pygame.Surface((padded_w, padded_h), pygame.SRCALPHA)
        padded.blit(shadow, (blur_radius, blur_radius))
        shadow = pygame.transform.gaussian_blur(padded, blur_radius)

    # a TELJES surface athalvanyitasa - ez nem irja felul a per-pixel
    # alpha-t, hanem SKALAZZA azt blit-eleskor
    shadow.set_alpha(opacity)
    return shadow


def ease_out_cubic(t: float) -> float:
    """
    Ease-out interpolacios fuggveny: gyors inditas, lassu erkezes.
    t: 0.0 (animacio eleje) -> 1.0 (animacio vege).
    Visszaadott ertek is 0.0 -> 1.0, de nem linearisan.
    """
    t = max(0.0, min(1.0, t))
    return 1 - pow(1 - t, 3)


class CardAnimator:
    """
    Nyilvántartja, hogy az 1-4 kártya közül melyik látható, és ha
    épp állapotot vált (megjelenik/eltűnik), milyen fázisban van az
    animáció.

    Minden kártyaszlotnak (1-4) van egy "target" állapota (látható
    vagy nem), és egy animáció-kezdési időpontja, amikor ez utoljára
    változott. A get_offset_y() ebből számolja ki, mennyivel kell
    lentebbről indulnia a kártyának az adott pillanatban.

    A slide_distances szótár SLOT-SPECIFIKUS induló-eltolást ad meg:
    annyit, hogy a kártya pontosan a látható terület aljáról (vagy
    épp alóla) induljon, NE sokkal lentebbről. Igy a teljes
    ANIMATION_DURATION_SEC alatt FOLYAMATOSAN látható a mozgás - ha a
    kártya sokkal lentebbről indulna, az animáció nagy része a
    látómezőn kívül történne, és csak a vége tűnne fel hirtelen
    "beugrásként".
    """

    ANIMATION_DURATION_SEC = 1.0

    def __init__(self, slide_distances: dict):
        """
        slide_distances: {slot: pixel_tavolsag} - mennyivel induljon
        lentebb a kartya a vegso pozicio-jahoz kepest, slotonkent
        kulon-kulon megadva (mert minden kartya mas alap Y poziciot
        es magassagot hasznalhat).
        """
        self.slide_distances = slide_distances
        self._visible = {1: True, 2: False, 3: False, 4: False}
        self._anim_start = {1: None, 2: None, 3: None, 4: None}
        self._anim_direction = {1: None, 2: None, 3: None, 4: None}  # "in" / "out"

    def set_active_count(self, count: int):
        """
        Beallitja, hany kartyanak kell lathatonak lennie (1-4), és
        elindítja az animációt azokra a slotokra, amelyeknek a
        láthatósági állapota változik.
        """
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
        """
        Visszaadja, mennyi Y-eltolást kell hozzáadni a slot véglegese
        pozíciójához EBBEN a pillanatban.

        0.0 = nincs eltolás (teljesen a végső helyén van).
        slide_distances[slot] = teljesen lent van, nem látható.
        """
        slide_distance = self.slide_distances[slot]
        direction = self._anim_direction[slot]

        if direction is None:
            # Nincs folyamatban animáció ezen a sloton: vagy mindig
            # látható volt (offset 0), vagy mindig rejtett (lent van).
            return 0.0 if self._visible[slot] else slide_distance

        elapsed = time.time() - self._anim_start[slot]
        t = elapsed / self.ANIMATION_DURATION_SEC

        if t >= 1.0:
            # animacio veget ert, takaritsuk el az allapotot
            self._anim_direction[slot] = None
            return 0.0 if self._visible[slot] else slide_distance

        eased = ease_out_cubic(t)

        if direction == "in":
            # 1.0 (teljesen lent) -> 0.0 (vegso helyen), ease-out-tal
            return slide_distance * (1 - eased)
        else:  # "out"
            # 0.0 (vegso helyen) -> 1.0 (teljesen lent), ease-out-tal
            return slide_distance * eased

    def is_slot_relevant(self, slot: int) -> bool:
        """
        Igaz, ha a slotot egyaltalan rajzolni kell (lathato, VAGY
        eppen animal ki/be). Ha False, a kartyat teljesen ki lehet
        hagyni a rajzolasbol (performancia).
        """
        return self._visible[slot] or self._anim_direction[slot] is not None


class ScoreGUI:
    """
    A pontszám-kijelző alap képernyő. Mindig kész állapotban van,
    csak akkor rajzol, ha a state machine SCORE állapotban van.

    A kijelzőt (DRM master jogot) fel- és leengedi az
    acquire_display() / release_display() hívásokkal, hogy az mpv
    VIDEO állapotban kizárólagosan birtokolhassa a DRM kimenetet.
    """

    SCREEN_W = 800
    SCREEN_H = 600

    COLOR_TEXT = (245, 245, 245)
    COLOR_TEXT_OUTLINE = (40, 30, 10)
    COLOR_ACTIVE = (60, 40, 15)       # sötétbarna szöveg az aktív kártyán
    COLOR_INACTIVE = (110, 100, 90)   # halványabb az inaktív kártyákon
    COLOR_MULTIBALL = (220, 40, 40)

    # Drop shadow opacitasa: 0-255 kozott. 15% opacitas = kb. 38.
    SHADOW_OPACITY = 90
    # Gaussian blur sugara pixelben a drop shadow-hoz. pygame-ce kell
    # hozza (pygame.transform.gaussian_blur) - sima pygame-ben nincs.
    SHADOW_BLUR_RADIUS = 4

    # 4 kártya VÉGSŐ pozíciója (bal felső sarok x,y, forgatás előtti
    # koordinátarendszerben) és dőlésszöge fokban. A kártya hosszú
    # (CARD_HEIGHT), és úgy van pozícionálva, hogy az alja a kép alja
    # ALATT legyen - csak a felső, szöveges része "lóg be" a képbe,
    # mintha egy hosszú cigipapír csak részben látszódna.
    CARD_LAYOUT = [
        {"pos": (10, 435), "angle": 45},
        {"pos": (190, 435), "angle": 45},
        {"pos": (370, 435), "angle": 45},
        {"pos": (550, 435), "angle": 45},
    ]
    CARD_WIDTH = 240
    CARD_HEIGHT = 100   # hosszú papír, hogy belógjon a képernyő alja alá

    def __init__(self):
        self.screen = None
        self.font_score_big = None
        self.font_label = None
        self.font_small = None
        self.font_card_name = None
        self.font_card_score = None
        self.active = False

        self.background = None     # htr.png, 800x600-ra skálázva
        self.card_texture = None   # cigip.jpg, nyers betöltve

        distances = {1: 180, 2: 180, 3: 180, 4: 180}
        self.card_animator = CardAnimator(slide_distances=distances)

        # Kartya-surface CACHE: slotonkent tarolja az utoljara epitett
        # (forgatas elotti) surface-t es a hozza tartozo "cache key"-t
        # (azokat az adatokat, amik alapjan epult). Ha a kovetkezo
        # frame-ben ugyanaz a cache key, NEM epitjuk ujra a surface-t
        # (nincs uj smoothscale + font render), csak a mar meglevot
        # forgatjuk el ujra - ez sokkal olcsobb a Pi 3B+ CPU-jan.
        self._card_cache = {1: None, 2: None, 3: None, 4: None}
        self._card_cache_key = {1: None, 2: None, 3: None, 4: None}

        # Drop shadow cache - kulon tarolo, mert az arnyek az
        # ELFORGATOTT kartyabol epul, nem a forgatas elottibol.
        self._card_shadow_cache = {1: None, 2: None, 3: None, 4: None}
        self._card_shadow_cache_key = {1: None, 2: None, 3: None, 4: None}

        # "Ball: X" felirat cache - ugyanazon okbol, mint a kartyak:
        # ne rendereljunk fontot minden frame-ben, ha nem valtozott.
        self._ball_label_cache = None
        self._ball_label_cache_key = None

        # Kozepso nagy pontszam cache - ez a draw_outlined_text() hivast
        # sporolja meg, ha az aktualis jatekos pontszama nem valtozott.
        self._main_score_cache = None
        self._main_score_cache_key = None

    def acquire_display(self):
        """
        Felveszi a kijelzőt (DRM master jogot). Csak akkor hívjuk,
        amikor SCORE állapotba lépünk, hogy az mpv addigra már
        elengedte a DRM-et.
        """
        if self.active:
            return

        pygame.init()
        self.screen = pygame.display.set_mode(
            (self.SCREEN_W, self.SCREEN_H),
            pygame.FULLSCREEN if os.environ.get("SDL_VIDEODRIVER") == "kmsdrm" else 0
        )
        pygame.display.set_caption("Cheech & Chong Pinball - Score")

        # Elérési út a Modak fontfájlhoz az assets mappában
        modak_font_path = os.path.join(ASSETS_DIR, "Modak.ttf")

        # A pygame.font.Font-nak átadjuk a fájlt és a méretet
        self.font_score_big = pygame.font.Font(modak_font_path, 80)
        self.font_label = pygame.font.Font(modak_font_path, 28)
        self.font_small = pygame.font.Font(modak_font_path, 22)
        self.font_card_name = pygame.font.Font(modak_font_path, 20)
        self.font_card_score = pygame.font.Font(modak_font_path, 26)

        self._load_assets()
        self.active = True

    def _load_assets(self):
        """
        Betölti a háttérképet és a kártya-textúrát. Csak akkor hívjuk,
        amikor már van aktív pygame display, mert image.load()/convert()
        ehhez kész display surface-t igényel.
        """
        bg_path = os.path.join(ASSETS_DIR, "htr.png")
        # FONTOS: convert() -tel toltjuk be, NEM convert_alpha()-val.
        # A hatter teljesen opak, statikus kep - nincs szuksege
        # alpha-csatornara. Ha convert_alpha()-t hasznalnank, a PNG
        # szelein levo finom el-simitas (anti-aliasing) miatt nem
        # teljesen 255-os alpha ertekek "athalvanyitanak" a korabbi
        # frame tartalmaval blit-eleskor, ami szellemszeru
        # maradvany-pixeleket hagy a kepernyon (pl. a kozepso pontszam
        # korabbi, hosszabb szovegenek nyoma maradt a hatteren at).
        # convert()-tel a blit mindig teljesen felulirja a pixeleket.
        bg_raw = pygame.image.load(bg_path).convert()
        self.background = pygame.transform.smoothscale(
            bg_raw, (self.SCREEN_W, self.SCREEN_H)
        )

        card_path = os.path.join(ASSETS_DIR, "cigip.jpg")
        self.card_texture = pygame.image.load(card_path).convert()

    def release_display(self):
        """
        Elengedi a kijelzőt (DRM master jog felszabadítása), hogy
        az mpv tudjon rajzolni VIDEO állapotban.
        """
        if not self.active:
            return
        pygame.display.quit()
        pygame.quit()
        self.active = False

    def _build_card_surface(self, player_num, state):
        """
        Felépít egy KÉSZ (még forgatás előtti) kártya-surface-t a
        cigip.jpg textúrából, rárajzolva a játékos nevét és pontszámát.

        FONTOS: a surface-t SRCALPHA flag-gel hozzuk létre, hogy
        legyen alpha-csatornája. A cigip.jpg maga sima JPG (nincs
        alpha), és ha simán pygame.transform.rotate()-ot hívnánk egy
        sima RGB surface-en, a forgatás által keletkező új sarkokat
        egy szürke "üres" színnel töltené ki SDL. Az alpha-csatornás
        surface-en a sarkok transzparensek maradnak.
        """
        card_texture_scaled = pygame.transform.smoothscale(
            self.card_texture, (self.CARD_WIDTH, self.CARD_HEIGHT)
        )
        card = pygame.Surface((self.CARD_WIDTH, self.CARD_HEIGHT), pygame.SRCALPHA)
        card.blit(card_texture_scaled, (0, 0))

        is_active = (player_num == state.current_player)
        text_color = self.COLOR_ACTIVE if is_active else self.COLOR_INACTIVE

        # A szöveg a kártya TETEJÉRE kerül, mert csak ez a rész lesz
        # látható a képernyőn - a kártya alja kilóg a kép alja alá.
        name_surf = self.font_card_name.render(f"Player {player_num}", True, text_color)
        card.blit(name_surf, (80, 5))

        score_surf = self.font_card_score.render(
            f"{state.players[player_num]:,}", True, text_color
        )
        card.blit(score_surf, (80, 35))

        return card

    def _get_cached_card_surface(self, player_num, state):
        """
        Visszaadja a player_num kártyájának (forgatás előtti)
        surface-ét, gyorsítótárazva.

        A cache key azokat az adatokat foglalja össze, amik a kártya
        KINÉZETÉT befolyásolják: a játékos pontszáma, és hogy ő-e
        az aktív játékos (ez szín-váltást okoz). Ha ezek nem
        változtak az előző frame óta, a korábban felépített
        surface-t adjuk vissza újrarajzolás nélkül - elkerülve a
        smoothscale()-t és a font.render()-t, amik a Pi 3B+ gyengébb
        CPU-ján érdemben lassabbak, mint egy PC-n.
        """
        cache_key = (state.players[player_num], player_num == state.current_player)

        if self._card_cache_key[player_num] != cache_key:
            self._card_cache[player_num] = self._build_card_surface(player_num, state)
            self._card_cache_key[player_num] = cache_key

        return self._card_cache[player_num]

    def _get_cached_card_shadow(self, player_num, rotated_card_surface):
        """
        Visszaadja a player_num kártyájának drop shadow-ját, gyorsítótárazva.

        Az árnyék az ELFORGATOTT kártya-surface-ből készül (nem a
        forgatás előttiből), mert így pontosan illeszkedik a kártya
        megjelenő alakjához és dőlésszögéhez, anélkül, hogy külön
        forgatnunk kellene az árnyékot is.

        A cache key itt egyszerűen a kártya-cache key-ét használja
        (ugyanaz, mint a _get_cached_card_surface-ben), mert az
        árnyék kinézete pontosan ugyanazoktól az adatoktól függ, mint
        a kártya tartalma.
        """
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
            return  # biztonsági korlát: ne próbáljon rajzolni, ha nincs kijelző

        # state.active_player_count alapjan frissitjuk az animatort -
        # ha valtozott, ez inditja el a be/kicsuszast a megfelelo slotokon
        self.card_animator.set_active_count(state.active_player_count)

        # háttér: a betöltött htr.png
        self.screen.blit(self.background, (0, 0))

        # "Ball: X" felirat a felhő-buborékba - cache-elve, csak akkor
        # rendereljuk ujra a fontot, ha a labda szama valtozott
        if self._ball_label_cache_key != state.current_ball:
            self._ball_label_cache = self.font_label.render(
                f"Ball: {state.current_ball}", True, self.COLOR_ACTIVE
            )
            self._ball_label_cache_key = state.current_ball
        ball_rect = self._ball_label_cache.get_rect(center=(190, 105))
        self.screen.blit(self._ball_label_cache, ball_rect)

        # 4 kartya-slot: csak azokat rajzoljuk, amelyek lathatok VAGY
        # eppen animalnak (be- vagy kicsuszas kozben vannak)
        for player_num, layout in zip(self.players_order(), self.CARD_LAYOUT):
            slot = player_num
            if not self.card_animator.is_slot_relevant(slot):
                continue

            card = self._get_cached_card_surface(player_num, state)
            rotated = pygame.transform.rotate(card, layout["angle"])

            offset_anim = self.card_animator.get_offset_y(slot)

            pos_x, pos_y = layout["pos"]

            actual_x = pos_x - offset_anim
            actual_y = pos_y + offset_anim

            # drop shadow: a kartya alakjat koveto, halvany fekete
            # sziluett, blurrel lagyitva, 7px-szel eltolva jobbra-le.
            # Elobb az arnyek kerul a kepre, utana a kartya MAGA, hogy
            # a kartya felulre kerul az arnyek tetejere.
            #
            # FONTOS: a shadow surface a blur-padding miatt NAGYOBB,
            # mint a rotated kartya (BLUR_RADIUS-szal mindket iranyban),
            # ezert a blit poziciojat BLUR_RADIUS-szal "korabbra" kell
            # tenni mindket tengelyen, hogy a benne levo (elmosott)
            # kartya-korvonal pontosan a kartya alatt legyen, ne
            # csusszon el a padding miatt.
            shadow = self._get_cached_card_shadow(player_num, rotated)
            shadow_offset = 7
            shadow_x = actual_x + shadow_offset - self.SHADOW_BLUR_RADIUS
            shadow_y = actual_y - self.SHADOW_BLUR_RADIUS
            self.screen.blit(shadow, (shadow_x, shadow_y))

            self.screen.blit(rotated, (actual_x, actual_y))

        # középső nagy pontszám: az AKTUÁLIS játékos pontszáma, kontúrozott,
        # cache-elve - csak akkor epitjuk ujra, ha a pontszam valtozott
        main_score_value = state.players[state.current_player]
        if self._main_score_cache_key != main_score_value:
            self._main_score_cache = build_outlined_text_surface(
                self.font_score_big, f"{main_score_value:,}",
                self.COLOR_TEXT, self.COLOR_TEXT_OUTLINE, outline_width=3,
            )
            self._main_score_cache_key = main_score_value

        score_rect = self._main_score_cache.get_rect(
            center=(self.SCREEN_W // 2, self.SCREEN_H // 2 - 60)
        )
        self.screen.blit(self._main_score_cache, score_rect)

        if state.multiball_active:
            mb_text = self.font_small.render("MULTIBALL!", True, self.COLOR_MULTIBALL)
            self.screen.blit(mb_text, (self.SCREEN_W - 160, 30))

        pygame.display.flip()

    def players_order(self):
        return [1, 2, 3, 4]

    def poll_pygame_events(self):
        """
        Lekérdezi a pygame eseménysorát EGYSZER, és visszaadja a teljes
        listát. Ezt csak EGY helyen (itt) szabad meghívni egy frame-en
        belül, mert pygame.event.get() kiüríti a sort.

        Ha a kijelző nincs aktív (VIDEO állapotban vagyunk), nincs
        honnan eseményt lekérdezni - ilyenkor üres listát adunk vissza.
        """
        if not self.active:
            return []
        return pygame.event.get()

    def has_quit_event(self, pygame_events) -> bool:
        """Megnézi, van-e QUIT esemény a megadott eseménylistában."""
        return any(e.type == pygame.QUIT for e in pygame_events)