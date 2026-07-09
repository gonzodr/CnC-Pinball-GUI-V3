# Cheech & Chong Pinball — Raspberry Pi GUI/videó vezérlő

Egyedi épített flippergép (Cheech & Chong témával) pontszám-kijelzőjének és
videólejátszásának szoftvere. Egy Teensy mikrovezérlő küldi a játék
eseményeit (pontszám, gombnyomások, videó-triggerek) soros porton, ez a
Python-os szoftver ebből épít fel egy teljes attract-mode + pontszám-kijelző
GUI-t (pygame), és vezérli a témavideók lejátszását (mpv).

Fejlesztés PC-n (Windows/Linux) történik valódi hardver nélkül — egy
billentyűzet-alapú mock input ugyanolyan eseményeket generál, mint amiket a
Teensy küldene, így a teljes GUI/állapotgép lánc tesztelhető. A Teensy
csatlakoztatása után a kód változtatás nélkül tovább működik: a billentyűzet
és a soros port egyszerre "élhet" egymás mellett.

## Fájlstruktúra

```
CnC Pinball GUI V3 Python/
├── requirements.txt
├── cnc-pinball.service          <- systemd unit (Pi-n /etc/systemd/system/-be)
└── src/
    ├── main.py                  <- belépési pont, a fő event loop
    ├── state_machine.py         <- a teljes állapotgép + attract-loop
    ├── protocol.py               <- soros parancsok -> GameEvent parser
    ├── serial_reader.py          <- soros port olvasása külön szálon
    ├── mock_input.py             <- billentyűzet -> GameEvent (fejlesztői mód)
    ├── mpv_controller.py         <- mpv IPC vezérlés (videólejátszás)
    ├── score_gui.py              <- a teljes pygame GUI (minden képernyő)
    ├── score_manager.py          <- hiscores.json kezelése
    ├── thanks_names_manager.py   <- thanks_names.json kezelése
    ├── name_entry.py             <- hiscore névbeíró logika
    ├── service_menu.py           <- titkos szerviz menü (Ctrl+M) logika
    ├── hiscores.json             <- mentett high score-ok (top 10)
    ├── thanks_names.json         <- Special Thanks névlista
    └── assets/                   <- képek, fontok, videók
```

## Architektúra

### Állapotgép (`state_machine.py`)

A `StateMachine` egyetlen `AppState` enumot vezet, amit a `main.py` event
loopja minden frame-ben lekérdez, és ez alapján dönt, mit rajzoljon ki a
`score_gui.py`. A fő állapotok:

| Állapot | Mit csinál |
|---|---|
| `SCORE` | Az aktív játék pontszám-kijelzője (kártyák, fő pontszám) |
| `VIDEO` | Egy témavideó fut (mpv kapja a kijelzőt) |
| `SUMMARY` | Egy labda/játékos bónusz-összegzője (PLAYER/SCORE/BONUS/TOTAL) |
| `FINAL_SCORES` | Játék végi végeredmény, ha 2+ játékos volt (győztes kiemelve) |
| `NAME_ENTRY` | Hiscore névbeírás (3 karakteres monogram) |
| `HIGHSCORE` | A teljes top-10 tábla |
| `LOGO` | Attract-mode logó, pszichedelikus animált háttérrel |
| `PRESS_START` | "Press Start to Play!" pulzáló felirat |
| `SPECIAL_THANKS` | Görgetett köszönet-lista |
| `BEAT_SCORE` | "Beat This Score!" — a #1 rekord kihívása |
| `SERVICE_MENU` | Titkos szerviz menü (lásd lent) |

### Attract-mode loop

Amikor senki nem játszik, a gép automatikusan körbe-körbe megy:

```
LOGO → PRESS_START → SPECIAL_THANKS → PRESS_START → HIGHSCORE → PRESS_START → BEAT_SCORE → (elölről)
```

Minden lépés 8 másodpercig tart. A ciklus a program indulásakor a LOGO-val
kezdődik; ha egy játék véget ér (GAMEOVER), a ciklus utána PRESS_START-tal
folytatódik (nem a LOGO-val — valódi flippereken is így szokás). A **Start**
gomb a ciklus bármely pontján kilépteti a játékost a `SCORE` képernyőre.

### Játékmenet

```
SCORE --(NEXT/GAMEOVER)--> SUMMARY --(8s után)--> [FINAL_SCORES, ha 2+ játékos volt]
                                                  --> [NAME_ENTRY, ha új rekord]
                                                  --> attract-loop (ha se nem rekord, se nem GAMEOVER: vissza SCORE-ba)

NAME_ENTRY --(kész)--> HIGHSCORE (5s) --> attract-loop
```

## Vezérlés (billentyűzet — mock input, fejlesztői teszteléshez)

A valódi gépen ezek fizikai gombok (piros Start, zöld Shoot/Player, két
sárga flipper); PC-n a billentyűzet szimulálja őket:

| Gomb | Funkció |
|---|---|
| `S` | Start (piros gomb) — attract képernyőkről kilép SCORE-ba |
| `P` | Player/Shoot (zöld gomb) — játékosszám váltás / NAME_ENTRY-ben betű megerősítés |
| `←` / `→` | Flipper bal/jobb — NAME_ENTRY-ben betűváltás |
| `W` | Teszt pontszerzés (+1500, néha szorzó-növelés) |
| `R` | Véletlen témavideó lejátszása |
| `B` | Labda leesik (ball drain) — NEXT vagy GAMEOVER, a játékos/labda számától függően |
| `I` | Elindítja a teljes attract-loopot (`ATTRACT` esemény) |
| `T` / `L` / `K` | Ideiglenes fejlesztői gombok: közvetlenül a Special Thanks / Logo / Beat This Score képernyőre ugrik (loopon kívül, gyors vizuális ellenőrzéshez) |
| `Esc` | Bárhonnan (amíg nem fut már az attract-loop) visszadob a loop elejére |
| `Ctrl+M` | Titkos szerviz menü megnyitása (csak nyugalmi/attract állapotból) |
| `Q` | Kilépés a programból a parancssorba (szerviz menün kívül) |

## Titkos szerviz menü (`Ctrl+M`)

Valódi (Pi-hez csatlakoztatott) billentyűzettel kezelhető, rejtett
karbantartó felület. Amíg aktív, a nyers billentyű-események közvetlenül a
menühöz mennek (nem a fenti mock input táblázathoz), úgyhogy szabadon lehet
gépelni (pl. nevet beírni) ütközés nélkül.

- **Hiscore szerkesztés/törlés** — egyesével törölhető bejegyzés, listából
- **Special Thanks nevek** — `A` új nevet ad hozzá (begépelve, automatikusan
  ABC sorrendbe rendezve mentéskor), `Delete` törli a kijelöltet
- **Input/gomb teszt** — élőben mutatja az utóbbi (mock/soros) eseményeket
  és mennyi ideje történtek
- **Serial Monitor (raw)** — a soros porton ténylegesen beérkező NYERS
  sorokat mutatja, akkor is, ha nem sikerült értelmezni — hardveres
  hibakereséshez, mielőtt a Teensy eseményei tényleg vezérlik a játékot
- **Összes hiscore törlése** — Y/N megerősítéssel nullázza a táblát
- **Verzió info** — Python/pygame verzió + git commit hash

Navigáció: `↑`/`↓` mozgás, `Enter` kiválaszt, `Esc` vissza/kilépés (a
főmenüből kilépve, vagy a Hiscore szerkesztésből közvetlenül is, a program
visszatér az attract-loopba).

## 1. Tesztelés a saját gépeden (Windows/Linux, VS Code)

```bash
cd "CnC Pinball GUI V3 Python"
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

PC-n az `mpv` és a videófájlok hiánya miatt az `mpv_controller.py`
automatikusan **offline módba** kapcsol (ha nincs telepítve `mpv`, vagy
nincs AF_UNIX socket — pl. Windowson), és fake időzítéssel szimulálja a
videó lejátszását/végét, hogy a GUI/state machine lánc hardver nélkül is
tesztelhető legyen.

Ha szeretnél valódi `mpv`-t is tesztelni PC-n:
- Windows: https://mpv.io/installation/ majd add hozzá a PATH-hoz
- Linux: `sudo apt install mpv`

A GUI ablakban fog megnyílni (mert `SDL_VIDEODRIVER=kmsdrm` PC-n nem
érvényes, SDL visszaesik a normál ablakos módra — ezt a `score_gui.py`
automatikusan detektálja).

## 2. Telepítés a Raspberry Pi 3B+-on

```bash
# Alap függőségek a Pi-n (Raspberry Pi OS Lite)
sudo apt update
sudo apt install -y python3-pip python3-venv mpv

# Projekt másolása a Pi-re (pl. scp-vel vagy git clone-nal)
scp -r "CnC Pinball GUI V3 Python" pi@<pi-ip-cime>:/home/pi/

ssh pi@<pi-ip-cime>
cd /home/pi/CnC\ Pinball\ GUI\ V3\ Python
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Felhasználói jogosultságok

```bash
sudo usermod -aG video,render,dialout pi
# Jelentkezz ki/be (vagy reboot), hogy érvénybe lépjen
```

### DRM connector beazonosítása

```bash
cat /sys/class/drm/*/status
# Keresd meg, melyik kimenet van "connected" állapotban (pl. card1-HDMI-A-1)
```

Ezt írd be a `src/mpv_controller.py`-ban a `--drm-connector=` paraméterbe.

### Soros port beazonosítása

```bash
ls /dev/ttyACM* /dev/ttyUSB* 2>/dev/null
```

Ezt írd be a `src/main.py`-ban a `SERIAL_PORT` konstansba.

### Videók előkészítése

A videókat H.264, MP4, yuv420p formátumban kell a `src/assets/Videos/`
mappába tenni (ez a `MpvController.VIDEO_DIR` alapértelmezett értéke).

After Effects exportot ffmpeg-gel konvertálhatod:
```bash
ffmpeg -i input.mov -c:v libx264 -pix_fmt yuv420p -vf scale=640:480 output.mp4
```

## 3. Automatikus indítás boot-kor (systemd)

```bash
sudo cp cnc-pinball.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable cnc-pinball.service
sudo systemctl start cnc-pinball.service
```

Logok követése:
```bash
journalctl -u cnc-pinball -f
```

Leállítás/újraindítás teszteléshez:
```bash
sudo systemctl stop cnc-pinball.service
sudo systemctl restart cnc-pinball.service
```

## Platform-specifikus dolgok, amikre figyelni kell

- **32 bites ARM-on (armv7l/armv6l) a `pygame.transform.smoothscale` és a
  box blur Bus Error-t okoz.** A `score_gui.py` automatikusan detektálja a
  platformot (`_smoothscale_supported()` / `_blur_supported()`), és plain
  `scale`-re / blur nélkülire vált ezeken a gépeken. Új scale/blur hívást
  mindig ezeken a helper függvényeken keresztül kell bekötni, közvetlen
  `pygame.transform.smoothscale`/`box_blur` hívás nélkül.
- **mpv videó beállítások, amiket NEM szabad visszaállítani:**
  `--hwdec=v4l2m2m` (NEM `-copy`), `--drm-mode=640x480@60`, `--ao=alsa`,
  `gpu_mem=128`. A zero-copy `--vo=gpu` korábban teljes rendszerfagyást
  okozott a Pi-n.
- A `hiscores.json` és `thanks_names.json` mindig a `src/` mappa mellé
  íródik (abszolút útvonal a fájl saját helyéhez képest), függetlenül
  attól, honnan indítod a `main.py`-t.

## Ismert nyitott pontok

- A valódi Teensy hardver soros bekötése még nincs teljesen végigtesztelve
  — a `main.py`-ban a `serial_reader.poll_events()` -> `state.handle_event()`
  útvonal jelenleg ki van kommentezve (a `SerialReader` háttérszála viszont
  fut, és a nyers adatot a szerviz menü Serial Monitor képernyőjén már most
  is lehet nézni).
- A `mock_input.py` billentyű-leképezése ideiglenes/fejlesztői célú — valós
  gombok bekötésekor az esemény-fajták (`GameEvent.kind`) már stimmelnek,
  a `state_machine.py`/`score_gui.py` nem igényel változtatást.
