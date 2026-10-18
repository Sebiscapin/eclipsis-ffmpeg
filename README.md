# FFmpeg per Eclipsis

Questo repository compila i tre eseguibili di [FFmpeg](https://ffmpeg.org) (`ffmpeg.exe`,
`ffprobe.exe`, `ffplay.exe`) distribuiti con Eclipsis. Contiene solo la ricetta di
compilazione di FFmpeg.

FFmpeg è distribuito con la licenza **GNU LGPL**. Questa build è configurata con
`--enable-version3`, quindi gli eseguibili sono distribuiti con la **LGPL versione 3 o
successiva** ([COPYING.LGPLv3](COPYING.LGPLv3), che rimanda alla
[GPL versione 3](COPYING.GPLv3)). Non usa parti GPL né "nonfree". Gli script e il file
[sorgenti/mfdec.c](sorgenti/mfdec.c) di questo repository hanno la stessa licenza.

## Differenze rispetto a FFmpeg

- **Versione**: FFmpeg 8.1 (ramo `release/8.1`; il commit esatto è in `commit_ffmpeg.txt`
  di ogni release).
- **Decodifica AAC con Windows**: il decoder AAC di FFmpeg è escluso. Al suo posto c'è
  `aac_mf` ([sorgenti/mfdec.c](sorgenti/mfdec.c)), che passa l'audio al decoder AAC di
  Windows (Media Foundation, `msauddecmft.dll`). Lo script
  [sorgenti/aggiungi_decoder_aac_mf.sh](sorgenti/aggiungi_decoder_aac_mf.sh) lo aggiunge al
  sorgente di FFmpeg prima della compilazione.
- **Componenti**: solo quelli elencati in [configurazione.txt](configurazione.txt), dove
  sono tutte le opzioni di compilazione. H.264 e AAC si codificano con gli encoder di
  Windows (`h264_mf`, `aac_mf`): nessun codificatore H.264 o AAC è compilato negli
  eseguibili.
- **Avviso delle modifiche**: il sorgente di ogni release contiene `MODIFICHE_ECLIPSIS.txt`
  (cosa è cambiato e quando: 12 e 13 ottobre 2026) e ogni riga aggiunta ai file di FFmpeg è
  preceduta da un commento "Eclipsis".

## Come si compila

Il workflow [.github/workflows/compila.yml](.github/workflows/compila.yml) esegue
[compila.sh](compila.sh) su Linux: scarica FFmpeg 8.1, aggiunge il decoder e compila dentro
l'immagine Docker di [BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds)
(`win64-lgpl-8.1`), che contiene il compilatore e le librerie esterne già compilate.
L'impronta esatta dell'immagine usata è in `immagine_docker.txt` di ogni release.

Poi, su Windows, confronta l'audio decodificato da questa build con quello del FFmpeg
ufficiale di BtbN ([prove/confronta_aac.py](prove/confronta_aac.py)) e prova gli encoder di
Windows.

Ogni modifica caricata su `main` si compila e si prova; una release si crea solo con un
tag `v...` (per esempio `v8.1-2`).

## Librerie esterne

Gli eseguibili contengono, collegate staticamente, alcune librerie esterne prese
dall'immagine di BtbN: libass (con FreeType, HarfBuzz, FriBidi, fontconfig, libxml2,
GNU libiconv, libunibreak), dav1d, zimg, SDL2 (con libsamplerate) e zlib, oltre al runtime
di mingw-w64. L'elenco esatto lo ricava ogni compilazione dalle librerie passate al linker
([librerie.txt](librerie.txt) ne indica licenza e provenienza; la compilazione si ferma se
ne compare una nuova). [librerie.sh](librerie.sh) trova il commit di BtbN/FFmpeg-Builds da
cui è nata l'immagine e, da lì, il repository e il commit di ogni libreria.

## Sorgente corrispondente e licenze

Ogni release contiene, accanto agli eseguibili:

- `ffmpeg-sorgente.tar.gz`: il sorgente completo di FFmpeg esattamente come è stato
  compilato (con il decoder aggiunto);
- `librerie-sorgente.tar.gz`: il sorgente delle librerie esterne ai commit usati e gli
  script di BtbN/FFmpeg-Builds con cui sono compilate;
- nello zip degli eseguibili: la LGPL 3 (`LICENSE.txt`), la GPL 3 a cui rimanda
  (`COPYING.GPLv3.txt`), gli avvisi di licenza delle librerie (`LICENZE-LIBRERIE.txt`) e il
  loro elenco con licenza, repository e commit (`librerie_usate.txt`).

Chi distribuisce gli eseguibili deve distribuire insieme a loro anche `LICENSE.txt`,
`COPYING.GPLv3.txt` e `LICENZE-LIBRERIE.txt`, e indicare dove si scarica il sorgente.

---

# FFmpeg for Eclipsis (English)

This repository builds the FFmpeg executables distributed with Eclipsis. It only contains
the FFmpeg build recipe. The build is configured with `--enable-version3`, so the
executables are distributed under the GNU LGPL version 3 or later (COPYING.LGPLv3, together
with COPYING.GPLv3); it contains no GPL-only or nonfree parts. Every modified FFmpeg file
carries an "Eclipsis" notice and each release includes MODIFICHE_ECLIPSIS.txt describing
the changes (12 and 13 October 2026). AAC is decoded by the AAC decoder that ships with
Windows (Media Foundation) through the `aac_mf` decoder in `sorgenti/mfdec.c`; FFmpeg's own
AAC decoder is disabled and only the components listed in `configurazione.txt` are built.
Third-party libraries are statically linked from the BtbN/FFmpeg-Builds image. Every
release includes the complete corresponding source of FFmpeg (`ffmpeg-sorgente.tar.gz`),
the source of the third-party libraries with the scripts that build them
(`librerie-sorgente.tar.gz`), and their license notices (`LICENZE-LIBRERIE.txt`).
