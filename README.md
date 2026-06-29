# Cheech & Chong Pinball - Raspberry Pi GUI/video vezerlo

## Fájlstruktúra

```
cnc_pinball_pi/
├── requirements.txt
├── cnc-pinball.service      <- systemd unit, a Pi-n /etc/systemd/system/-be kerül
└── src/
    ├── protocol.py          <- soros parancsok -> GameEvent parser
    ├── serial_reader.py     <- soros port olvasása külön szálon
    ├── mpv_controller.py    <- mpv IPC vezérlés (videólejátszás)
    ├── state_machine.py     <- SCORE <-> VIDEO állapotgép
    ├── score_gui.py         <- pontszám GUI, pygame-mel
    └── main.py              <- belépési pont, mindent összeköt
```

## 1. Tesztelés a saját gépeden (Windows/Linux, VS Code)

```bash
cd cnc_pinball_pi
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

A `SERIAL_PORT` értékét a `src/main.py`-ban át kell ideiglenesen írnod a saját
gépeden elérhető soros portra, vagy egy szimulátor scriptre (lásd lent), mert
a Teensy valószínűleg nem lesz csatlakoztatva a fejlesztői géphez.

PC-n az `mpv` és a videófájlok hiánya miatt az `mpv_controller.py` hibát fog
dobni induláskor, ha nincs telepítve `mpv`. Telepítsd:
- Windows: https://mpv.io/installation/ majd add hozzá a PATH-hoz
- Linux: `sudo apt install mpv`

A GUI ablakban fog megnyílni (mert `SDL_VIDEODRIVER=kmsdrm` PC-n nem érvényes,
SDL visszaesik a normál ablakos módra).

### Soros port szimulálása teszteléshez

Mivel a Teensy valószínűleg nincs ott a fejlesztői gépeden, érdemes egy
virtuális soros port-páros segítségével (pl. `socat` Linux/Mac alatt, vagy
`com0com` Windows alatt) szimulált parancsokat küldeni, hogy lásd működik-e
a GUI/state machine anélkül, hogy a teljes hardver ott lenne.

Linux/Mac példa:
```bash
socat -d -d pty,raw,echo=0 pty,raw,echo=0
# Ez kiír két /dev/pts/N elérési utat - egyiket állítsd be SERIAL_PORT-nak,
# a másikra pedig küldhetsz teszt-parancsokat: echo "SCORE,1,5000" > /dev/pts/M
```

## 2. Telepítés a Raspberry Pi 3B+-on

```bash
# Alap függőségek a Pi-n (Raspberry Pi OS Lite)
sudo apt update
sudo apt install -y python3-pip python3-venv mpv

# Projekt másolása a Pi-re (pl. scp-vel vagy git clone-nal)
scp -r cnc_pinball_pi pi@<pi-ip-cime>:/home/pi/

ssh pi@<pi-ip-cime>
cd /home/pi/cnc_pinball_pi
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Felhasználói jogosultságok

A `pi` felhasználónak kell, hogy legyen DRM/KMS írási joga és soros port elérése:

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

A videókat H.264, MP4, yuv420p formátumban kell a `/home/pi/videos/` mappába
tenni (ez a `MpvController.VIDEO_DIR` alapértelmezett értéke, módosítható).

After Effects exportot ffmpeg-gel konvertálhatod:
```bash
ffmpeg -i input.mov -c:v libx264 -pix_fmt yuv420p -vf scale=800:600 output.mp4
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

## Ismert korlátozások / további finomítás

- A `score_gui.py` betűtípusai jelenleg `Arial` SysFont-tal mennek - ha
  egyedi fontot szeretnél (pl. a flipper témájához illőt), azt .ttf fájlból
  kell betölteni `pygame.font.Font(path, size)`-szal.
- A pontszám-elrendezés (2x2 grid) egyszerű kiindulópont - a tényleges
  layout, színek, animációk később finomíthatók.
- A `--drm-connector` és `SERIAL_PORT` értékeket MINDENKÉPP ellenőrizni
  kell a konkrét Pi-n, ezek hardverfüggőek.
