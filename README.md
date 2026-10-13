# FFmpeg per Eclipsis

Questo repository compila i tre eseguibili di [FFmpeg](https://ffmpeg.org) (`ffmpeg.exe`,
`ffprobe.exe`, `ffplay.exe`) che il programma Eclipsis usa per leggere e scrivere i video.
Contiene solo la ricetta di compilazione di FFmpeg: il codice di Eclipsis non è qui e non
è pubblico.

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
- **Codificatori**: solo quelli che il programma usa. H.264 e AAC sono quelli di Windows
  (`h264_mf`, `aac_mf`), quindi nessun codificatore H.264 o AAC è compilato negli eseguibili.
- **Opzioni di compilazione**: tutte in [configurazione.txt](configurazione.txt).
- **Avviso delle modifiche**: il sorgente di ogni release contiene `MODIFICHE_ECLIPSIS.txt`
  (cosa è cambiato e quando: 12 e 13 ottobre 2026) e ogni riga aggiunta ai file di FFmpeg è
  preceduta da un commento "Eclipsis".

## Come si compila

Il workflow [.github/workflows/compila.yml](.github/workflows/compila.yml) esegue
[compila.sh](compila.sh) su Linux: scarica FFmpeg 8.1, aggiunge il decoder e compila dentro
l'immagine Docker di [BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds)
(`win64-lgpl-8.1`), che contiene il compilatore e le librerie: libass (con FreeType,
HarfBuzz, FriBidi), zimg, dav1d, SDL2, zlib. L'impronta esatta dell'immagine usata è in
`immagine_docker.txt` di ogni release.

Poi, su Windows, confronta l'audio decodificato da questa build con quello del FFmpeg
ufficiale di BtbN ([prove/confronta_aac.py](prove/confronta_aac.py)) e prova gli encoder di
Windows.

Ogni modifica caricata su `main` si compila e si prova; una release si crea solo con un
tag `v...` (per esempio `v8.1-1`).

## Sorgente corrispondente

Ogni release contiene, accanto agli eseguibili, `ffmpeg-sorgente.tar.gz`: il sorgente
completo di FFmpeg esattamente come è stato compilato (con il decoder aggiunto). Le
librerie esterne sono compilate dagli script di BtbN/FFmpeg-Builds, che fissano le loro
versioni (cartella `scripts.d` di quel repository).

---

# FFmpeg for Eclipsis (English)

This repository builds the FFmpeg executables used by the Eclipsis application. It only
contains the FFmpeg build recipe; Eclipsis' own code is not here. The build is configured
with `--enable-version3`, so the executables are distributed under the GNU LGPL version 3
or later (COPYING.LGPLv3, together with COPYING.GPLv3); it contains no GPL-only or nonfree
parts. Every modified FFmpeg file carries an "Eclipsis" notice and each release includes
MODIFICHE_ECLIPSIS.txt describing the changes (12 and 13 October 2026). AAC is decoded by
the AAC decoder that ships with Windows (Media Foundation) through the `aac_mf` decoder in
`sorgenti/mfdec.c`; FFmpeg's own AAC decoder and all unused encoders are disabled. Every
release includes the complete corresponding source (`ffmpeg-sorgente.tar.gz`).
