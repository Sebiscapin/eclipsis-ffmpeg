#!/bin/bash
# Compila ffmpeg.exe, ffprobe.exe e ffplay.exe per Eclipsis (Windows 64 bit, statici, LGPL 3):
# FFmpeg 8.1 con le opzioni di configurazione.txt e il decoder AAC di Windows
# (sorgenti/), dentro l'immagine Docker di BtbN/FFmpeg-Builds che contiene il compilatore
# e le librerie gia' compilate (libass, zimg, dav1d, SDL2, zlib).
# Uscita in ./uscita: gli eseguibili, il sorgente corrispondente (LGPL) di FFmpeg e delle
# librerie esterne, gli avvisi di licenza e le impronte.
# Serve Linux con Docker, git e python3 (lo fa da solo il workflow .github/workflows/compila.yml).
set -euo pipefail
cd "$(dirname "$0")"

IMMAGINE="${IMMAGINE:-ghcr.io/btbn/ffmpeg-builds/win64-lgpl-8.1:latest}"
RAMO="${RAMO:-release/8.1}"
export RAMO

rm -rf lavoro uscita
mkdir -p lavoro uscita
cp -r sorgenti configurazione.txt lavoro/

docker pull "$IMMAGINE"
docker image inspect --format '{{index .RepoDigests 0}}' "$IMMAGINE" > uscita/immagine_docker.txt

cat > lavoro/dentro.sh <<'FINE'
set -euxo pipefail
cd /lavoro
git clone --filter=blob:none --branch="$RAMO" https://github.com/FFmpeg/FFmpeg.git ffmpeg
cd ffmpeg
git rev-parse HEAD > /lavoro/commit_ffmpeg.txt
bash /lavoro/sorgenti/aggiungi_decoder_aac_mf.sh .

# Il sorgente corrispondente (LGPL 3 / GPL 3 sez. 6): FFmpeg com'e' stato compilato, con
# il decoder aggiunto, prima di compilare.
git add -A
git -c user.name="Eclipsis" -c user.email="build@eclipsis.app" commit -q \
    -m "avcodec: decoder AAC tramite Media Foundation (aac_mf)"
git archive --format=tar.gz --prefix=ffmpeg-eclipsis/ -o /lavoro/ffmpeg-sorgente.tar.gz HEAD

OPZIONI="$(grep -v '^#' /lavoro/configurazione.txt | grep -v '^[[:space:]]*$' | tr '\n' ' ')"
./configure --prefix=/lavoro/prefix --pkg-config-flags="--static" $FFBUILD_TARGET_FLAGS $OPZIONI \
    --extra-cflags="$FF_CFLAGS" --extra-cxxflags="$FF_CXXFLAGS" --extra-libs="$FF_LIBS" \
    --extra-ldflags="$FF_LDFLAGS" --extra-ldexeflags="$FF_LDEXEFLAGS" \
    --cc="$CC" --cxx="$CXX" --ar="$AR" --ranlib="$RANLIB" --nm="$NM" \
    --extra-version="eclipsis" || { cat ffbuild/config.log; exit 1; }
make -j"$(nproc)" V=1
make install

# controlli: il decoder di Windows c'e', quello di FFmpeg no, codificatori solo quelli previsti
grep -q '^#define CONFIG_AAC_MF_DECODER 1' config_components.h
grep -q '^#define CONFIG_AAC_DECODER 0' config_components.h
grep -q '^#define CONFIG_AAC_ENCODER 0' config_components.h
grep -q '^#define CONFIG_LIBOPENH264_ENCODER 0' config_components.h
grep '_ENCODER 1$' config_components.h > /lavoro/codificatori.txt
grep '_DECODER 1$' config_components.h > /lavoro/decodificatori.txt

# Le librerie esterne messe negli eseguibili: quelle che configure passa al linker
# (EXTRALIBS) e che stanno in $FFBUILD_PREFIX, cioe' compilate da BtbN; le altre sono di
# Windows o del compilatore. librerie.sh ne raccoglie il sorgente e le licenze.
grep -h '^EXTRALIBS' ffbuild/config.mak | tr ' ' '\n' |
    sed -n -e 's/^-l//p' -e 's#^.*/lib\([^/]*\)\.a$#\1#p' | sort -u |
    while read -r lib; do
        if [[ -f "$FFBUILD_PREFIX/lib/lib$lib.a" ]]; then
            echo "$lib"
        fi
    done > /lavoro/librerie_collegate.txt
cat /lavoro/librerie_collegate.txt
# quelle chieste da configurazione.txt devono esserci (o l'elenco non e' stato letto bene)
for lib in ass dav1d zimg SDL2 z; do
    grep -qx "$lib" /lavoro/librerie_collegate.txt
done
FINE

docker run --rm -e RAMO -u "$(id -u):$(id -g)" -v "$PWD/lavoro":/lavoro "$IMMAGINE" bash /lavoro/dentro.sh

# sorgente e avvisi di licenza delle librerie esterne (uscita/librerie-sorgente.tar.gz,
# uscita/LICENZE-LIBRERIE.txt, uscita/librerie_usate.txt)
IMMAGINE="$IMMAGINE" bash librerie.sh

cp lavoro/prefix/bin/ffmpeg.exe lavoro/prefix/bin/ffprobe.exe lavoro/prefix/bin/ffplay.exe uscita/
cp lavoro/commit_ffmpeg.txt lavoro/ffmpeg-sorgente.tar.gz lavoro/codificatori.txt \
   lavoro/decodificatori.txt configurazione.txt uscita/
tar -xzf lavoro/ffmpeg-sorgente.tar.gz -C lavoro ffmpeg-eclipsis/COPYING.LGPLv3 \
    ffmpeg-eclipsis/COPYING.GPLv3 ffmpeg-eclipsis/MODIFICHE_ECLIPSIS.txt
# LGPL 3: il testo della LGPL, quello della GPL a cui rimanda e l'avviso delle modifiche
cp lavoro/ffmpeg-eclipsis/COPYING.LGPLv3 uscita/LICENSE.txt
cp lavoro/ffmpeg-eclipsis/COPYING.GPLv3 uscita/COPYING.GPLv3.txt
cp lavoro/ffmpeg-eclipsis/MODIFICHE_ECLIPSIS.txt uscita/
(cd uscita && sha256sum ffmpeg.exe ffprobe.exe ffplay.exe ffmpeg-sorgente.tar.gz \
    librerie-sorgente.tar.gz > SHA256SUMS.txt)
ls -la uscita
