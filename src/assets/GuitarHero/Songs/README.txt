Masold ebbe a mappaba a chartolando .ogg, .mp3, .wav vagy .flac zeneket.

A szervizmenu Guitar chart editor pontja innen listazza a szamokat. A mentett
chart ugyanide kerul, a zenevel azonos nevvel es .chart.json kiterjesztessel.

Pelda: wrench_solo.ogg -> wrench_solo.chart.json

Az automatikus gitar-felterkepezes a Demucs ket kimenetet is a zene melle
masolja, hogy a jatek a Raspberry Pi-n cache nelkul is hasznalhassa oket:

  wrench_solo.guitar.wav
  wrench_solo.no_guitar.wav

A jatek a no_guitar savot streameli, a guitar savot pedig kulon mixer-
csatornan jatsza. MISS eseten csak a gitar halkul el.
