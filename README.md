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
l'immagine Docker di [FFmpeg-Builds](https://github.com/FFmpeg-Builds/FFmpeg-Builds)
(`win64-lgpl-8.1`), che contiene il compilatore e le librerie: libass (con FreeType,
HarfBuzz, FriBidi, Fontconfig, libxml2, libunibreak, libiconv), zimg, dav1d, SDL2 (con
libsamplerate), zlib. L'immagine è fissata per impronta in [immagine.txt](immagine.txt),
insieme al commit di FFmpeg-Builds da cui è costruita.

Poi [raccogli_librerie.py](raccogli_librerie.py) legge in quel commit di FFmpeg-Builds
repository e commit di ogni libreria collegata, ne scarica il sorgente, controlla che le
versioni coincidano con quelle dell'immagine e con quelle scritte dentro gli eseguibili, e
prepara le licenze e l'archivio del sorgente delle librerie.

Infine, su Windows, confronta l'audio decodificato da questa build con quello del FFmpeg
ufficiale di FFmpeg-Builds ([prove/confronta_aac.py](prove/confronta_aac.py)) e prova gli
encoder di Windows.

Ogni modifica caricata su `main` si compila e si prova; una release si crea solo con un
tag `v...` (per esempio `v8.1-1`).

## Sorgente corrispondente

Ogni release contiene, accanto agli eseguibili:

- `ffmpeg-sorgente.tar.gz`: il sorgente completo di FFmpeg esattamente come è stato
  compilato (con il decoder aggiunto);
- `librerie-sorgente.tar`: il sorgente di ogni libreria collegata negli eseguibili (fra cui
  FriBidi e libiconv, LGPL), al commit esatto, con gli script di FFmpeg-Builds che le hanno
  compilate e questo repository al commit della release (vedi il suo `LEGGIMI.txt`).

Dentro `ffmpeg-eclipsis-win64.zip`, la cartella `licenze/` ha la licenza e gli avvisi di
ogni componente e `librerie.json` (nome, versione, commit, origine, licenza SPDX). Per la
release v8.1-1, compilata prima, le stesse cose sono nell'allegato
`ffmpeg-eclipsis-licenze.zip` e nella cartella [librerie/](librerie/) di questo repository.

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
release includes the complete corresponding source: `ffmpeg-sorgente.tar.gz` (FFmpeg as
built) and `librerie-sorgente.tar` (every statically linked library, FriBidi and libiconv
included, at the exact commit, with the FFmpeg-Builds scripts that built them). The
`licenze/` folder of the zip holds the license and notices of every component.
