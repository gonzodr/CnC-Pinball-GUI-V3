# Cheech & Chong Pinball — Raspberry Pi GUI/videó vezérlő

Egyedi épített flippergép (Cheech & Chong témával) pontszám-kijelzőjének és
videólejátszásának szoftvere. Egy Teensy mikrovezérlő küldi a játék
eseményeit (pontszám, gombnyomások, videó-triggerek) soros porton, ez a
Python-os szoftver ebből épít fel egy teljes attract-mode + pontszám-kijelző
GUI-t (pygame), vezérli a témavideók lejátszását (mpv), valamint futtatja a
VUK/UFO által indított **Munchies Abduction** minijátékot.

Fejlesztés PC-n (Windows/Linux) történik valódi hardver nélkül — egy
billentyűzet-alapú mock input ugyanolyan eseményeket generál, mint amiket a
Teensy küldene, így a teljes GUI/állapotgép lánc tesztelhető. A Teensy
csatlakoztatása után a kód változtatás nélkül tovább működik: a billentyűzet
és a soros port egyszerre "élhet" egymás mellett.

## Fájlstruktúra

```
CnC Pinball GUI V3 Python/
├── requirements.txt
├── cnc-pinball.service          <- systemd unit, a fő GUI-hoz (Pi-n /etc/systemd/system/-be)
├── splashscreen.service         <- systemd unit, a boot-splash képhez (Pi-n /etc/systemd/system/-be)
└── src/
    ├── main.py                  <- belépési pont, a fő event loop
    ├── state_machine.py         <- a teljes állapotgép + attract-loop
    ├── protocol.py               <- soros parancsok -> GameEvent parser
    ├── serial_reader.py          <- soros port olvasása külön szálon
    ├── arduino_port.py           <- Arduino port detektálása/mentése (arduino-cli)
    ├── firmware_update.py        <- önálló program: git pull + fordítás + feltöltés a Megára
    ├── mock_input.py             <- billentyűzet -> GameEvent (fejlesztői mód)
    ├── mpv_controller.py         <- mpv IPC vezérlés (videólejátszás)
    ├── png_video_player.py       <- Pygame PNG-sequence videómotor
    ├── video_asset_paths.py      <- külső sequence assetkönyvtár feloldása
    ├── score_gui.py              <- a teljes pygame GUI (minden képernyő)
    ├── munchies_abduction.py     <- önálló Munchies Abduction minijáték-modul
    ├── score_manager.py          <- hiscores.json kezelése
    ├── thanks_names_manager.py   <- thanks_names.json kezelése
    ├── particle_settings.py      <- particle_settings.json kezelése (szerkeszthető effekt-szorzók)
    ├── minigame_settings.py      <- minigame difficulty mentése (-3..+3)
    ├── name_entry.py             <- hiscore névbeíró logika
    ├── service_menu.py           <- titkos szerviz menü (Ctrl+M) logika
    ├── hiscores.json             <- mentett high score-ok (top 10)
    ├── thanks_names.json         <- Special Thanks névlista
    ├── particle_settings.json    <- particle-effekt szorzók (a szerkesztőből mentve)
    ├── minigame_settings.json    <- minigame difficulty (futás közben keletkezik)
    ├── serial_port.json          <- legutóbb detektált Arduino port (a szerkesztőből mentve)
    └── assets/
        ├── bootimage.png         <- boot-splash kép (a splashscreen.service ezt jeleníti meg)
        ├── Minigame/             <- pálya, sprite-ok, UI, zene, FX és karakterhangok
        └── ...                   <- egyéb képek, fontok, videók
```

A `hiscores.json`, `thanks_names.json`, `particle_settings.json`, `minigame_settings.json`
és `serial_port.json` mind
futásidőben keletkező/frissülő adatfájlok — egy friss telepítésnél nem kell velük foglalkozni,
a program magától létrehozza őket alapértékekkel, ha még nem léteznek.

## Architektúra

### Jackpot PNG-videók

A firmware `Jackpot_<pont>` sort küld (például `Jackpot_30000`), a GUI pedig
a `JACKPOT_<pont>` sequence-mappát játssza le. Elkészült összegek:
10000, 15000, 20000, 25000, 30000, 50000 és 100000. Mindegyik 125 képkocka,
30 fps, egyszeri lejátszás (kb. 4,17 másodperc), utána visszatér a pontkijelző.

A normál jackpotok változatlanok: a kishíd multiball-jackpotjai 10000/15000/20000/25000/30000,
a nagyhídé 10000/15000/20000/20000/20000, a loopé 30000. Az új triggerek
a Hurry Up szorzóját is figyelembe veszik. Jackpotnál a szorzás után kijövő
40000 pont helyett 50000, 60000 helyett 100000 jár, és a videó is ezt mutatja.
Így Hurry Up alatt az 10000/15000/20000/25000/30000 alap-jackpotból rendre
20000/30000/50000/50000/100000 lesz. A bónusz változatlan, a többi pontforrás
továbbra is egyszerűen 2x pontot ad. Hiányzó klip helyett nem jelenítünk meg
másik pontösszeget.

Ehhez a firmware-t és a GUI-t is frissíteni kell: a régi `Jackpot2..6` módazonosító
nem különbözteti meg a két híd eltérő jutalmát. A PNG-k Gitből ki vannak zárva;
a hét `JACKPOT_*` mappát külön kell a célgépen az aktív videókönyvtárba tenni
(`CNC_PNG_VIDEO_ROOT`, vagy a `video_asset_paths.py` szerinti hely).

### Állapotgép (`state_machine.py`)

A `StateMachine` egyetlen `AppState` enumot vezet, amit a `main.py` event
loopja minden frame-ben lekérdez, és ez alapján dönt, mit rajzoljon ki a
`score_gui.py`. A fő állapotok:

| Állapot | Mit csinál |
|---|---|
| `SCORE` | Az aktív játék pontszám-kijelzője (kártyák, fő pontszám) |
| `VIDEO` | Egy témavideó fut (mpv kapja a kijelzőt) |
| `PNG_VIDEO` | Egy 30 FPS-es PNG sequence fut közvetlenül a Pygame kijelzőn |
| `SUMMARY` | Egy labda/játékos bónusz-összegzője (PLAYER/SCORE/BONUS/TOTAL) |
| `FINAL_SCORES` | Játék végi végeredmény, ha 2+ játékos volt (győztes kiemelve) |
| `NAME_ENTRY` | Hiscore névbeírás (3 karakteres monogram) |
| `HIGHSCORE` | A teljes top-10 tábla |
| `LOGO` | Attract-mode logó, pszichedelikus animált háttérrel |
| `PRESS_START` | "Press Start to Play!" pulzáló felirat |
| `SPECIAL_THANKS` | Görgetett köszönet-lista |
| `BEAT_SCORE` | "Beat This Score!" — a #1 rekord kihívása |
| `SERVICE_MENU` | Titkos szerviz menü (lásd lent) |
| `MINIGAME` | Munchies Abduction: VUK/UFO által indított, valós időben vezérelt minijáték |

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

SCORE --(MG_START,<session>)--> MINIGAME --(MG_DONE/ACK)--> SCORE
```

### UFO Feature Wheel

A ket jointos UFO-sorsolas az Arduinon tortenik. A firmware
`WheelStart,<session>,<EXTRABALL|HURRYUP|MUNCHIES>` sort kuld, a GUI pedig a
hozza tartozo 640x480-as, 180 frame-es PNG sequence-et jatssza le. Extra Ball
es Hurry Up eseten ezt meg egy 150 frame-es, 5 masodperces eredmenyvideo
koveti; Munchiesnal nincs koztes klip. Extra Ball/Hurry Up agban a GUI az
eredmenyvideo inditasakor kuldi a `WHEEL_DONE,<session>` valaszt, igy a
firmware mar az animacio alatt aktivizalhatja a jutalmat es kidobhatja a
golyot. Munchiesnal ugyanez a valasz a kerek vegen inditja a minijatekot.
A firmware 500 ms-onkent megismetli a START-ot, a GUI ugyanazt a sessiont
nem inditja ujra; 12 masodperces firmware-timeout megakadalyozza, hogy
GUI-hiba eseten a golyo a VUK-ban maradjon.

A Feature Wheel agban a regi `Ufo5`/`Ufo6` klipek szandekosan nem futnak le.

## Munchies Abduction minijáték

A VUK/UFO találat egy 640×480-as, felülnézetes Cheech & Chong minijátékot
indít. A játékos a két flippergombbal mozgatja az UFO-t, a
Shoot/Player/Plunger gombbal pedig bekapcsolja a vonósugarat. A jó tárgyakat
(kajákat) be kell szippantani, a junk tárgyakat érdemes elkerülni.

Indítás:

- firmware/soros protokoll: `MG_START,<session>` sor (`MUNCHIES` és
  `VUK_GAME` legacy triggerként továbbra is elfogadott);
- PC-s fejlesztői mód: `U` billentyű a `SCORE` képernyőn;
- a minijáték aktív flipperjáték közben indul; egy még futó videót vagy
  PNG-sequence-t a VUK trigger azonnal megszakít.

A teljes képernyős folyamat:

```text
INTRO → 3 / 2 / 1 → JÁTÉK → TIME'S UP → SUMSCREEN → SCORE
```

Az intro és a visszaszámlálás alatt a háttér-streamer előre feltölti a
pályacache-t. Az idő lejártakor a játéktér kimerevedik, lefut a kétlépcsős
Time's Up hang/animáció, majd az eredményképernyő és a két karakter lezáró
párbeszéde. A végső bónusz bekerül az aktuális játékos flipperpontszámába,
és hardveren a GUI `MG_DONE,<session>,<pont>` sort küld vissza. Ezt ACK-ig
250 ms-onként újraküldi, így egy elveszett sor nem nyeli el a pontot és nem
hagyja a golyót a VUK-ban.

### Arduino–Pi minijáték-protokoll

A protokoll 115200 baudon, újsorral lezárt ASCII üzenetekkel fut. A firmware
minden VUK-belépéshez új 16 bites session ID-t generál; más sessionből maradt
inputot vagy eredményt mindkét oldal eldob.

```text
Arduino                         Pi / GUI
   |--- MG_START,sid ----------->|
   |<-- MG_READY,sid ------------|
   |--- MG_INPUT,sid,seq,mask --->|  változáskor azonnal + 50 ms heartbeat
   |<-- MG_ALIVE,sid ------------|  500 ms heartbeat
   |<-- MG_PICKUP,sid,GOOD/BAD ---|
   |<-- MG_COLLECTION,sid --------|
   |<-- MG_DONE,sid,bonus --------|
   |--- MG_ACK,sid -------------->|
```

Az input bitmask: bit 0 = bal flipper, bit 1 = jobb flipper, bit 2 =
Shoot/Plunger. A snapshotok nemcsak gyorsak, hanem öngyógyítók is: ha egy
állapotváltozás elveszne, a következő 50 ms-os csomag helyreállítja a valós
gombállapotot. A firmware READY nélkül 3 másodperc, Pi-heartbeat nélkül 5
másodperc, abszolút pedig 230 másodperc után megszakítja a módot és kidobja a
golyót. A minijáték alatt a normál játékmenet és a flippertekercsek szünetelnek,
a megszakításos tekercsvédelem végig aktív.

A pálya ilyenkor közel sötét alapfényt kap. Jó pickupnál az összes fény zölden
villan, junknál kétszer pirosan, collection complete-nél egy kb. egy másodperces
folyamatos zöld hullám fut végig. Minden effekt `millis()`-al időzített,
`delay()` nélküli réteg.

### Játékszabályok és pontozás

- Alapidő: **35 másodperc**; az első kb. 15 másodperc könnyebb tutorial
  szakasz, utána fokozatosan nő a sebesség és a spawnnyomás.
- Egy kaja felszedése névlegesen **+2 másodpercet** és típusfüggő alappontot
  ad; a tényleges időjutalmat a lentebb leírt dinamikus csillapítás módosítja.
- Egy junk tárgy **-2 másodpercet** és legfeljebb **-1500 pontot** jelent, valamint
  megszakítja a kombót.
- A kombószorzó 3/5/8/12 egymást követő kajánál `x2/x3/x4/x5`; a lánc
  1,35 másodperc tétlenség után lejár.
- Minden kajatípus külön `0/10` collectiont vezet. A tizedik darab után a
  számláló újraindul, jár névlegesen **+5 másodperc** és **10 000 collection
  bonus**.
- Hosszú meneteknél az időjutalom fokozatosan gyengül, és a magas időtartalék
  is csillapítja; nincs kemény pont- vagy időlimit, de a játék egyre nehezebb.
- A szervizmenü F8-as csúszkáján a `NORMAL` pontosan ezeket az értékeket
  használja. A többi fokozat együtt hangolja a pályasebességet, az óra
  fogyását és jutalmait, a sávszélességet, a slalom/choice mintákat, a junk
  időbüntetését és a combo-ablakot; a junk tárgyak arányát nem növeli.

A SumScreen végső képlete:

```text
TOTAL BONUS = MUNCHIES SCORE + COMBO BONUS + COLLECTION BONUS - JUNK PENALTY
```

Az eredményképernyő ezen felül mutatja a felszedett kaják számát, a teljes
collection seteket, a legjobb streaket, a maximális kombót és a felszedett
junk mennyiségét.

### Minijáték assetstruktúra

```text
src/assets/Minigame/
├── Level/                       # STREET_00000.png ... STREET_00599.png
├── Intro/                       # bgr.png, title.png, foreGr.png
├── Music/                       # loopolt háttérzene
├── Sound/
│   ├── FX/                      # intro, countdown, beam, pickup, reaction, Time's Up
│   └── Voices/                  # VLxxx[_item][_speaker].wav karakterhangok
├── Sprites/
│   ├── Food/ és Junk/           # 100×100: *_sw (árnyék), *_gl (beam glow)
│   ├── Characters/              # Cheech/Chong portré- és száj/pislogás-frame-ek
│   └── UFO/                     # hajótest, futófény-sequence és stabil útárnyék
└── UI/                          # Frame, HUD, Collect, SumScreen és Timesup elemek
```

A 600 pályakép **kötelezően 640×480-as, 8 bites RGB PNG legyen,
alfacsatorna nélkül, a `Level_BG` háttérrel már összerenderelve**. A streamer
30 FPS-en, 60 képes LRU cache-sel fut; Raspberry Pi-n két PNG-dekódoló szál
dolgozik. A játék végén a logba írt `[munchies] background stream:` sor
megmutatja a cache-találati arányt, a fallbackek számát és a legnagyobb
frame-lemaradást.

Az átlátszó vagy háttér nélküli STREET frame-eket a kompatibilitási útvonal
cache-be helyezés előtt kénytelen összekomponálni a `Level_BG` képpel. Ez
újra teljes képernyős munkát rakna a főszálra az előtöltés során, ezért új
pályarendernél mindig ellenőrizni kell az RGB formátumot.

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
| `T` | Véletlen PNG-sequence videó indítása; lejátszás közben újabb `T` azonnal másik véletlen klipre vált |
| `Y` / `L` / `K` | Ideiglenes fejlesztői gombok: közvetlenül a Special Thanks / Logo / Beat This Score képernyőre ugrik (loopon kívül, gyors vizuális ellenőrzéshez) |
| `U` | Munchies Abduction indítása a `SCORE` képernyőről |
| `Esc` | Bárhonnan (amíg nem fut már az attract-loop) visszadob a loop elejére |
| `Ctrl+M` | Titkos szerviz menü megnyitása (csak nyugalmi/attract állapotból) |
| `Q` | Kilépés a programból a parancssorba (szerviz menün kívül) |

A minijáték alatt a nyers `KEYDOWN` és `KEYUP` események közvetlenül a
minijátékhoz kerülnek, így a gombok nyomva tarthatók:

| Minijáték-gomb | Funkció |
|---|---|
| `←` / `A` | UFO balra |
| `→` / `D` | UFO jobbra |
| `Space` / `P` | Vonósugár bekapcsolása |

## Titkos szerviz menü (`Ctrl+M`)

Valódi (Pi-hez csatlakoztatott) billentyűzettel kezelhető, rejtett
karbantartó felület. Amíg aktív, a nyers billentyű-események közvetlenül a
menühöz mennek (nem a fenti mock input táblázathoz), úgyhogy szabadon lehet
gépelni (pl. nevet beírni) ütközés nélkül.

- **Hiscore szerkesztés/törlés** — egyesével törölhető bejegyzés; a lista
  utolsó sora Y/N megerősítéssel a teljes táblát is nullázza
- **Special Thanks nevek** — `A` új nevet ad hozzá (begépelve, automatikusan
  ABC sorrendbe rendezve mentéskor), `Delete` törli a kijelöltet
- **Input/gomb teszt** — élőben mutatja az utóbbi (mock/soros) eseményeket
  és mennyi ideje történtek
- **Serial Monitor (raw)** — a soros porton ténylegesen beérkező NYERS
  sorokat mutatja, akkor is, ha nem sikerült értelmezni — hardveres
  hibakereséshez, mielőtt a Teensy eseményei tényleg vezérlik a játékot
- **Particle szerkesztő** — élő, csúszkás szerkesztő a szikra/tűzijáték
  effektekhez (darabszám, méret, sebesség, élettartam, gravitáció),
  a képernyő alján folyamatosan újrainduló élő előnézettel;
  `↑`/`↓` paraméter, `←`/`→` érték, `R` alapértelmezettre állít
- **Arduino keresése** — lefuttatja az `arduino-cli`-s port-detektálást,
  elmenti a talált portot (`serial_port.json`), a GUI legközelebbi
  újracsatlakozáskor ezt próbálja először
- **Firmware update** — átadja a kijelzőt/soros portot egy önálló
  programnak (`firmware_update.py`), ami port-detektálás után
  `git pull`-t, `arduino-cli compile`-t és `upload`-ot futtat a Mega
  firmware-jén, élő loggal; a végén automatikusan visszaadja a
  vezérlést a GUI-nak (lásd lent, "Firmware toolchain a Pi-n")
- **Minigame difficulty (F8)** — minijátékonként hétfokozatú csúszka:
  `ALMOST ENDLESS` / `VERY EASY` / `EASY` / `NORMAL` / `HARD` / `HARDER` /
  `VERY HARD`. `←`/`→` állítja, `R` visszaállítja Normalra; a választás
  újraindítás után is megmarad
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

## 2. Telepítés egy friss Raspberry Pi-re (teljes környezet, 0-tól)

Ez a szakasz végigviszi a **teljes** környezetet, amit egy vadonatúj (vagy
újratelepített) Raspberry Pi 3B+-on be kell állítani, hogy a gép pontosan
úgy induljon, ahogy az élesben futó cabinet: boot-splash kép, majd
automatikusan induló GUI, plusz a szerviz menüből elérhető firmware-frissítő
toolchain. Minden lépés a jelenlegi éles gépen ténylegesen bevált, letesztelt
konfigurációt tükrözi (nem elméleti/generikus útmutató).

Az alábbi parancsokban a `gonzodr` felhasználónevet és a
`/home/gonzodr/CnC-Pinball-GUI-V3` elérési utat használjuk — ha egy másik
felhasználóval/mappával dolgozol, ezeket értelemszerűen cseréld le
mindenhol (beleértve a `.service` fájlokat is).

### 2.1 Alap OS csomagok

```bash
sudo apt update
sudo apt install -y python3-pip python3-venv mpv git fbi
```

(`fbi` a boot-splash képhez kell, lásd 2.7; `git` a GUI és a firmware repo
klónozásához.)

### 2.2 A GUI repó klónozása és a venv

```bash
cd ~
git clone https://github.com/gonzodr/CnC-Pinball-GUI-V3.git
cd CnC-Pinball-GUI-V3
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2.3 Felhasználói jogosultságok

```bash
sudo usermod -aG video,render,dialout gonzodr
# Jelentkezz ki/be (vagy reboot), hogy érvénybe lépjen
```

Kell: DRM/KMS írási jog (`video`, `render`) és soros port elérés
(`dialout` — ez utóbbi az `arduino-cli upload`-hoz is szükséges).

### 2.4 DRM connector beazonosítása

```bash
cat /sys/class/drm/*/status
# Keresd meg, melyik kimenet van "connected" állapotban (pl. card1-HDMI-A-1)
```

Ha nem `HDMI-A-1`, írd át a `src/mpv_controller.py`-ban a
`--drm-connector=` paramétert.

### 2.5 Soros port / Arduino

Nem kell kézzel beállítani semmit — a `src/main.py`-ban a `SERIAL_PORT`
konstans (`/dev/ttyACM0`) csak egy alapértelmezett fallback. Miután az
Arduino csatlakoztatva van, a szerviz menü **"Arduino keresése"** pontja
(vagy egy "Firmware update" futtatása) automatikusan megtalálja és elmenti
a tényleges portot (`src/serial_port.json`) — onnantól a GUI ezt használja
minden újracsatlakozáskor.

### 2.6 Videók előkészítése

A videókat H.264, MP4, yuv420p formátumban kell a `src/assets/Videos/`
mappába tenni (ez a `MpvController.VIDEO_DIR` alapértelmezett értéke).

After Effects exportot ffmpeg-gel konvertálhatod:
```bash
ffmpeg -i input.mov -c:v libx264 -pix_fmt yuv420p -vf scale=640:480 output.mp4
```

A külső lejátszó nélküli ág alapértelmezetten a repón kívüli
`~/CnC-Pinball-Video-Assets/<klipnév>/` mappák számozott PNG-it játssza le
(Windows alatt `%LOCALAPPDATA%/CnC-Pinball-Video-Assets`). Az útvonal a
`CNC_PNG_VIDEO_ROOT` környezeti változóval felülírható. A régi
`src/assets/Videos/Test/` csak átmeneti, ignorált fallback; új sequence már
ne kerüljön normál Gitbe.

A frame-ek legyenek 640×480-as, átlátszóság nélküli RGB PNG-k, nullákkal
kitöltött sorszámmal (például `2500_00000.png`). A `T` csak `SCORE` vagy
már futó `PNG_VIDEO` állapotban aktív; a klip végén automatikusan visszatér
a `SCORE` képernyőre. A motor minden klip első frame-jét memóriában tartja,
az aktív klipből pedig legfeljebb 60 konvertált frame-et és egy kisméretű
dekódolási work queue-t tárol.

A teljes, várhatóan több gigabájtos sequence-készlet GitHub Release assetként
terjed. A csomagoló a kliphatárokat megtartva 2 GiB alatti ZIP-eket és
SHA-256 manifestet készít:

```bash
python tools/package_video_assets.py \
  --root /path/to/all/sequences \
  --output /path/to/release-packages \
  --asset-version v1 \
  --release-base-url https://github.com/gonzodr/CnC-Pinball-GUI-V3/releases/download/video-assets-v1/ \
  --manifest video_assets_manifest.json
```

A ZIP-ek és a manifest Release-re feltöltése után a Pi telepítője hash alapján
ellenőriz, staging könyvtárba bont, majd csak teljes siker után, atomi
könyvtárcserével aktiválja az új készletet:

```bash
cd ~/CnC-Pinball-GUI-V3
venv/bin/python3 tools/update_video_assets.py \
  --manifest https://github.com/gonzodr/CnC-Pinball-GUI-V3/releases/download/video-assets-v1/video_assets_manifest.json
sudo systemctl restart cnc-pinball.service
```

Megszakadt letöltés vagy hibás SHA-256 esetén a működő régi assetkönyvtár
érintetlen marad.

### 2.7 Boot-splash kép

A gép induláskor egy saját képet mutat, MÉG a GUI service elindulása előtt
— ezt egy `fbi`-t (framebuffer image viewer) hívó, kézzel telepített
systemd unit csinálja (nem Plymouth, nem a `/boot/firmware/`-es
bootloader-splash — ez utóbbi ezen a gépen nincs bekötve).

```bash
cp ~/CnC-Pinball-GUI-V3/src/assets/bootimage.png ~/bootimage.png
sudo cp ~/CnC-Pinball-GUI-V3/splashscreen.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable splashscreen.service
```

(A `splashscreen.service` a `/home/gonzodr/bootimage.png`-re hivatkozik
fixen — ha más útvonalat használsz, azt is át kell írni benne.)

### 2.8 Automatikus indítás boot-kor (a fő GUI)

```bash
sudo cp ~/CnC-Pinball-GUI-V3/cnc-pinball.service /etc/systemd/system/
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

### 2.9 Firmware toolchain (arduino-cli + a Mega firmware)

Ez teszi lehetővé, hogy a szerviz menü **"Firmware update"** pontja
működjön (git pull + fordítás + feltöltés a cabinetből, laptop nélkül).

**arduino-cli telepítése** — a hivatalos install script `-b DIR` flag-je
hibásan viselkedik (a verziószám helyére írja be a flag-et, ezért a letöltés
elszáll), a dokumentált `BINDIR` env-var változatot kell használni:

```bash
curl -fsSL https://raw.githubusercontent.com/arduino/arduino-cli/master/install.sh | BINDIR=$HOME/bin sh
echo 'export PATH="$HOME/bin:$PATH"' >> ~/.bashrc

~/bin/arduino-cli config init
~/bin/arduino-cli core update-index
~/bin/arduino-cli core install arduino:avr
```

**FastLED library** (a standard library indexben van):

```bash
~/bin/arduino-cli lib install FastLED
```

**WavTrigger serial library** (NINCS a standard indexben — kézzel kell
klónozni, és a header-ben át kell kapcsolni Serial1-re):

```bash
mkdir -p ~/Arduino/libraries
git clone https://github.com/robertsonics/WAV-Trigger-Arduino-Serial-Library.git ~/Arduino/libraries/wavTrigger

# __WT_USE_ALTSOFTSERIAL__ kikommentezve, __WT_USE_SERIAL1__ aktiválva:
sed -i 's/^#define __WT_USE_ALTSOFTSERIAL__/\/\/#define __WT_USE_ALTSOFTSERIAL__/' ~/Arduino/libraries/wavTrigger/wavTrigger.h
sed -i 's/^\/\/#define __WT_USE_SERIAL1__/#define __WT_USE_SERIAL1__/' ~/Arduino/libraries/wavTrigger/wavTrigger.h
```

**A firmware repó klónozása** (public repo, sima HTTPS clone elég, nem
kell git hitelesítés a Pi-n):

```bash
git clone https://github.com/gonzodr/CnC_firmware4.git ~/CnC_firmware4
```

A mappa nevének **pontosan** egyeznie kell a fő `.ino` fájl nevével
(`CnC_firmware4.ino`) — az `arduino-cli` ez alapján azonosítja a sketch-et,
és a symlink-es kerülőút nem működik (feloldja a symlinket, és az alatta
lévő valódi mappanevet nézi).

**Ellenőrzés** — fordítás hardver nélkül is lefuttatható:

```bash
~/bin/arduino-cli compile --fqbn arduino:avr:mega ~/CnC_firmware4
```

Ha ez hibátlanul lefut (kb. 18% flash / 29% RAM használat körül kell
legyen), a toolchain kész — a szerviz menü "Firmware update" pontja
mostantól működik.

### 2.10 Teljes reboot-teszt

```bash
sudo reboot
```

Várt viselkedés: boot közben megjelenik a `bootimage.png`, majd — soros
Arduino/Teensy nélkül is — elindul az attract-mode loop (Logo → Press
Start → Special Thanks → Highscore → Beat This Score → elölről). Ha ez
megvan, a telepítés kész.

## Platform-specifikus dolgok, amikre figyelni kell

- **32 bites ARM-on (armv7l/armv6l) a `pygame.transform.smoothscale` és a
  box blur Bus Error-t okoz.** A `score_gui.py` automatikusan detektálja a
  platformot (`_smoothscale_supported()` / `_blur_supported()`), és plain
  `scale`-re / blur nélkülire vált ezeken a gépeken. Új scale/blur hívást
  mindig ezeken a helper függvényeken keresztül kell bekötni, közvetlen
  `pygame.transform.smoothscale`/`box_blur` hívás nélkül.
- **A minijáték SumScreen/Time's Up rétegeit közvetlenül a display surface-re
  kell blittelni.** A Pi 3B+-on egy teljes képernyős köztes `SRCALPHA`
  kompozit, amelyre további alfa-surface-ek kerülnek, reprodukálható ARM Bus
  Errort és service-újraindulást okoz. A `ResultsOverlay.draw()` direkt-blit
  felépítését ezért nem szabad visszacserélni köztes alpha layerre.
- A minijáték bezárásakor kiírt `[munchies] background stream:` diagnosztika
  Pi-teszt után gyorsan megmutatja, hogy a PNG-dekóder/cache okozott-e
  pályaakadást. Ideális esetben a találati arány közel 100%, a max gap pedig
  0–2 frame.
- **mpv videó beállítások, amiket NEM szabad visszaállítani:**
  `--hwdec=v4l2m2m` (NEM `-copy`), `--drm-mode=640x480@60`, `--ao=alsa`,
  `gpu_mem=128`. A zero-copy `--vo=gpu` korábban teljes rendszerfagyást
  okozott a Pi-n.
- A `hiscores.json` és `thanks_names.json` mindig a `src/` mappa mellé
  íródik (abszolút útvonal a fájl saját helyéhez képest), függetlenül
  attól, honnan indítod a `main.py`-t.

## Ismert nyitott pontok

- A teljes VUK-session protokoll Mega + fizikai golyóval még bench tesztet
  igényel. Telepítéskor először a GUI-t frissítsd (az új GUI érti a legacy
  triggereket is), és csak utána a firmware-t. A soros útvonal aktív:
  `serial_reader.poll_events()` → `state.handle_event()` minden GUI frame-ben
  lefut, maga a blokkoló portolvasás külön szálon történik.
- A `mock_input.py` billentyű-leképezése ideiglenes/fejlesztői célú — valós
  gombok bekötésekor az esemény-fajták (`GameEvent.kind`) már stimmelnek,
  a `state_machine.py`/`score_gui.py` nem igényel változtatást.
