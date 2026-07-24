# CnC-Pinball-GUI-V3 — fejlesztési összefoglaló (hiscore + name entry kör)

Ez a fájl azért készült, hogy egy új Claude Code munkamenet gyorsan kontextusba kerüljön
anélkül, hogy mindent újra el kellene magyarázni. A projekt egy Cheech & Chong témájú
flippergép GUI-ja Raspberry Pi 3B+-ra (Python, pygame-ce, mpv videó, SQLite/JSON hiscore),
PC-n fejlesztve, Pi-re deploy-olva.

## Architektúra (6 modul + újak)

- `protocol.py` — soros parancsokat (vagy mock inputot) `GameEvent`-té alakít
- `serial_reader.py` — Teensy soros port olvasása külön szálon
- `mpv_controller.py` — mpv IPC vezérlés (DRM/KMS kimenet Pi-n, offline mód PC-n)
- `state_machine.py` — a fő állapotgép: SCORE / VIDEO / SUMMARY / **NAME_ENTRY (új)** / HIGHSCORE
- `score_gui.py` — pygame renderelés minden képernyőhöz (640x480)
- `score_manager.py` — hiscore lista (`hiscores.json`, top 10, `is_highscore()`)
- `mock_input.py` — billentyűzet-alapú fejlesztői input, ugyanolyan `GameEvent`-eket
  generál, mint a Teensy majd fogja
- `name_entry.py` — **új modul**, a hiscore névbeíró logikája (`NameEntryController`)
- `main.py` — a fő event loop, összeköti a fentieket

## Amit ebben a körben megcsináltunk

### 1. Hiscore tábla (`render_highscore`)

Referencia: egy screenshot a kívánt kinézetről (zöld autós/dzsungeles háttér, "HIGHSCORES"
cím, POS/SCORE/NAME fejléc, 10 sor, TOP3-nál levél ikon).

- Pontos pozíciók/betűméretek a referencia screenshot pixeleiből visszaszámolva
  640x480-ra (cím, fejléc, sortávolság).
- Helyezés-formátum javítva: **1ST / 2ND / 3RD / nTH** (a Unity-s `HighscoreTable.cs`
  referenciából derült ki, hogy nem "1TH" a helyes minta, ahogy elsőre pixelről leolvastam).
- TOP3-hoz `TrophyStar.png` (szürkeárnyalatos levél) betöltve és **kódból tintelve**
  arany/ezüst/bronz színre (`BLEND_RGBA_MULT`), pontos hex értékek a Unity kódból:
  arany `FFD200`, ezüst `C6C6C6`, bronz `B76F56`.
- **Egy nagy, félig áttetsző, zöld szegélyű, lekerekített panel** az egész tábla mögött
  (nem soronkénti keret — ezt próbáltuk előbb `Border.png`-vel, de a user nem ezt kérte).
  Procedurálisan rajzolva (`pygame.draw.rect`, `border_radius`), mert nincs rá kész asset.
  Finomhangolva: keskenyebb legyen, ne lógjon rá a háttér-karakterek arcára, az oszlopok
  (POS/SCORE/NAME) közötti táv kisebb legyen — ezek most a `HISCORE_*` konstansokban vannak
  a `ScoreGUI` osztály tetején, könnyen tovább hangolhatók.
- `Border.png` asset be van töltve a projektbe, de **jelenleg nincs használva** a hiscore
  táblában — esetleg egy jövőbeli képernyőhöz (pl. kiemelés) még jó lehet.
- A pontszám formázása: **nincs ezres tagoló** (`1282786`, nem `1,282,786`), mert a
  referencia képen sincs.

### 2. Hiscore névbeíró képernyő (`render_name_entry` + `name_entry.py`)

Referencia: egy mockup kép (zöld gradiens háttér, "Player 1" cím, "A A A" 3 betűs
monogram, sárga bal/jobb nyilak, zöld "kurzor" háromszög az aktuális pozíció alatt,
"Press Start to Skip" / "Press Shoot to Next" feliratok).

**Fontos döntés:** nálunk nincs "Shoot" gomb (csak bal/jobb flipper, Start, tartott
Player/P gomb) — a user explicit döntése alapján a **Player (P) gomb lett a "következő
betű"** funkció, ezért a hint szöveg is át lett írva **"Press Player to Next"**-re.

- `name_entry.py` — új modul, `NameEntryController` osztály:
  - 3 karakteres monogram (`A-Z` + `0-9`), pozíciónként külön index
  - `prev_char()` / `next_char()` — az aktuális pozíció betűjét lépteti (bal/jobb flipper)
  - `confirm()` — lezárja az aktuális betűt, kurzor a következőre; 3. betű után `done=True`
  - `skip()` — Start gomb: azonnal lezárja a jelenlegi állással
- `score_gui.py` — `render_name_entry()`: "Player X" cím, 3 betű + zöld kurzor a `arrow.png`
  90°-kal elforgatva és zöldre tintelve, sárga bal/jobb nyilak (`arrow.png` + tükrözött
  párja), színes hint-sorok (`Start` piros, `Player` zöld szó a szövegben).
  Háttér: `bg640.png` (zöld radiális gradiens).
- **`state_machine.py` viselkedésváltozás**: eddig a `GAMEOVER`-nél azonnal, hardkódolt
  `"MRC"` névvel mentődött a hiscore. Ez most **megszűnt** — a `GAMEOVER` csak eltárolja a
  pontot (`pending_highscore_check`) és a játékost (`pending_highscore_player`). A SUMMARY
  vége után, ha a pont rekord, a state `NAME_ENTRY`-be lép; a tényleges
  `score_manager.add_score(name, score)` csak akkor fut le, amikor a `NameEntryController`
  végzett (gomb-nyomással vagy skip-pel).
  Új állapot: `AppState.NAME_ENTRY`.
- `mock_input.py`: `K_LEFT`/`K_RIGHT` → `FLIPPER_LEFT`/`FLIPPER_RIGHT`, `K_RETURN` → `START`,
  a `P` gomb mostantól a régi `PLAYERCOUNT_NEXT` mellett `PLAYER_PRESS`-t is küld (ez utóbbit
  a state machine csak `NAME_ENTRY` állapotban veszi figyelembe, máshol figyelmen kívül
  hagyja — nincs ütközés a régi player-count logikával).
- `protocol.py`: a valódi Teensy felé is fel van készítve a `START`, `FLIPPER_LEFT`,
  `FLIPPER_RIGHT`, `PLAYER_PRESS` parancsok fogadása.
- `main.py`: render dispatch kiegészítve `AppState.NAME_ENTRY`-re.

### Ebben a körben hozzáadott/módosított fájlok

Módosítva: `score_gui.py`, `state_machine.py`, `mock_input.py`, `protocol.py`, `main.py`
Új: `name_entry.py`
Új assetek (`assets/` mappába): `HiScoreBg.png`, `TrophyStar.png`, `Border.png` (jelenleg
nem használt), `bg640.png`, `arrow.png`

## Nyitott pontok / még hangolható

- A hiscore panel és a name entry layout konstansai (`HISCORE_*`, `NAME_*` a `ScoreGUI`
  osztály elején) még finomíthatók, ha valami nem stimmel Pi-n élesben.
- A `Border.png` asset egyelőre parkolópályán van, nincs felhasználva sehol.

## Következő tervezett lépések (ebben a sorrendben javasolt, de nyitott)

1. **`mock_input.py` teljes átdolgozása** (ez a mostani minimál bal/jobb-nyíl + P-gomb
   trükk csak a name entry teszteléséhez lett belőve, ideiglenes):
   - bal/jobb flipper gombok, Start gomb, tartott Player (P) gomb rendes leképezése
   - az 1/2/3/4 billentyűk mostantól **+1000 pontot adjanak az aktív játékosnak**,
     NE játékost váltsanak (jelenleg még nincs így implementálva)
2. **Attract-mode állapotgép**: Logo (~5s) → Special Thanks (film-credits-szerű lassú
   scroll) → Hiscore képernyő (~5s) → vissza Logo-ra, loop; a Start gomb bármikor
   megszakíthatja és a fő SCORE képernyőre ugrik.
   - A régi Unity GUI kód még átnézésre vár referenciaként az attract-mode időzítéshez
     (a hiscore/name-entry résznél a `HighscoreTable.cs` már sokat segített).
3. Real hardware teszt: amint a Teensy firmware tudja küldeni a `START` / `FLIPPER_LEFT` /
   `FLIPPER_RIGHT` / `PLAYER_PRESS` parancsokat, a `mock_input.py`-ban belőtt ideiglenes
   billentyű-leképezés valós gombokra cserélhető anélkül, hogy a `state_machine.py`-t
   vagy a `score_gui.py`-t hozzá kéne nyúlni (az esemény-kind-ok már stimmelnek).

## Platform-specifikus dolgok, amikre figyelni kell (korábbi körökből, még mindig érvényes)

- 32 bites ARM-on (armv7l/armv6l) a `pygame.transform.smoothscale` és a `box_blur` Bus
  Error-t okoz — a kód automatikusan `scale`-re és blur-kikapcsolásra vált ezeken a
  platformokon (lásd `_smoothscale_supported()` / `_blur_supported()` a `score_gui.py`-ban).
- mpv videó: `--hwdec=v4l2m2m-copy`, `--drm-mode=640x480@60`, `--ao=alsa`, `gpu_mem=128` —
  a zero-copy `--vo=gpu` rendszerfagyást okozott, ne menjünk vissza arra.

## 2026-07-10: F-gombos szervizmenu + UFO pontlopas szinkron + soros feldolgozas visszakapcsolva

- **F1..F10 gyorsgombok a szervizmenuhoz** (vak, monitor nelkuli hasznalatra —
  powerbankos pince-firmware-frissiteshez): a fomenü minden pontja F-gombot kapott
  (a cimkekben lathato), es az F-gombok **globalisan is elnek** — barmely nyugalmi
  allapotbol (attract, SCORE) egyetlen gombnyomas megnyitja a menut ES vegrehajtja
  a pontot. **F7 = Firmware update**, F6 = Arduino keresese, F10 = kilepes.
  A jatek kozbeni allapotokbol tovabbra sem nyilik (SERVICE_MENU_ALLOWED_STATES ved).
- **Ufo10..13 trigger** (az UFO "pontlopas" nyeremenye az uj V4 firmware-ben):
  a state_machine most mar a kirabolt jatekos kijelzett pontjabol is levon 10000-et
  (0-nal klammelve), mert a score uzenet csak az aktualis jatekos pontjat hozza.
- **VIDEO_NAME_REMAP**: a Unity-korszakbol orokolt elcsuszas kezelese — "Ufo6"
  trigger -> Ufofuck.mp4, "Ufo7" -> Ufo6.mp4 (Ufo7.mp4 soha nem letezett).
  Reszletek: firmware repo (gonzodr/CnC-Pinball-Firmware), VIDEO_MAP.md.
- **A soros esemeny-feldolgozas a main loopban VISSZA LETT KAPCSOLVA** (ki volt
  kommentelve!) — a probapadi Mega + SIM_MODE-os V4 firmware-rel most mar a teljes
  lanc tesztelheto (a firmware repo f_sim_mode.ino-ja egy teljes demojatekot jatszik).
- A firmware oldal teljes tortenete a firmware repoban es a Claude memoriajaban van
  (V4 = a gepben futott "fullos" fw + az osszes stabilitasi javitas egyesitve).

## 2026-07-10 (este): a "szaggat es kifagy a video utan" saga - MEGOLDVA

Tunet: a bench-en (Pi + SIM-demos Mega) a GUI video utan kilepett/lefagyott.
A nyomozas soran talalt es javitott retegek (mind commitolva):
1. **finally: sys.exit(0) elnyelte a kiveteleket** -> a crash "tiszta kilepesnek"
   latszott, a naploban semmi. Most: traceback a naploba + exit 1 -> a systemd
   Restart=on-failure ujrainditja (kiosk-szintu ongyogyitas).
2. **eof-reached hamis igaz fajlbetoltes kozben** -> a GUI a video legelejen
   visszavette a kijelzot, mikozben az mpv eppen elfoglalta -> "kmsdrm not
   available" crash. Most: az EOF-ot csak time-pos > 0 utan hisszuk el
   (_playback_confirmed), 5 mp nem-indulas = feladas (hianyzo fajl eset).
3. **IPC "Connection reset" utan nem volt ujracsatlakozas** -> orok VIDEO
   allapot. Most: _query_property() ujracsatlakozik; + VIDEO watchdog (45 mp).
4. **mpv teardown-beragadas**: stop utan az mpv neha masodpercekig (neha
   orokre) fogta a DRM kijelzot. Retegek: stop() megvarja az idle-t (3 mp),
   a kijelzo-visszavetel 12x ujraprobal (3 mp), ha az sem megy -> **mpv hard
   reset** (process kill+restart = a kernel garantaltan visszaadja a kijelzot).
5. pygame.QUIT kmsdrm alatt ignoralva (nincs bezarhato ablak; kilepes: Q).
6. PYTHONUNBUFFERED=1 drop-in a systemd unitban -> ELO naplo a journalban.
Vegallapot a bench-en: a GUI egyetlen processzkent tuleli a video-ciklusokat;
rossz esetben ~3-4 mp fekete a video utan (hard reset), majd megy tovabb.
A gepbeli (640x480) kijelzon az atadas varhatoan gyorsabb/stabilabb.

## 2026-07-22: CnC Light Editor a szervizmenubol inditva

A kulon repoban elo **CnC Light Editor** (Codex-fele pygame fenyeffekt-szerkeszto,
`gonzodr/CnC-Light-Editor`) beemelve a kabinetbe:
- A Pi-n `~/CnC-Light-Editor`-ba klonozva. NEM kell kulon telepites/venv: a GUI
  sajat venv-je (pygame-ce 2.5.7) futtatja, a csomagot `PYTHONPATH=<repo>/src`
  teszi importalhatova. Igazolva: import OK + `main.py --smoke-test` exit 0.
- **Szervizmenu: uj `F10 - Light editor` pont** (a Kilepes emiatt F10 -> **F11**).
  Ugyanaz a launch-minta, mint a firmware update-nel: `service_menu` egy
  `should_launch_light_editor` flaget allit, a `main.py` figyeli, elengedi a DRM
  kijelzot + soros portot (`run_light_editor()`), elinditja a szerkesztot
  `--fullscreen`-nel (KMSDRM), majd visszaveszi oket. Uj F11 a FKEYS-ben.
- **FONTOS UX-korlat**: a szerkeszto 1280x1024-re van tervezve, a kabinet kijelzo
  640x480. Ervi szerkeszteshez kulso monitor+eger+billentyuzet kell a Pi-re; a
  640x480 panelen a modevaltas elbukhat vagy hasznalhatatlanul zsufolt. Felbontas
  a `CNC_LIGHT_EDITOR_RESOLUTION` env-vel hangolhato.
- Export: a szerkeszto a sajat repojaba ir (`exports/effect_data.h`), onnan kell a
  firmware-be masolni (mint eddig). A firmware oldali formatum stabil, sentinel
  = magenta (255,0,255) = atlatszo overlaynel.
Kovetkezo: a szerkeszto LED-terkepe (led_map.json) a firmware LEDMAP.md-hez
kalibralando; effekt-trigger bekotesek (PlayOverlay/effectID) az uj esemenylista
utan.
