#!/bin/bash
# Compila ffmpeg.exe, ffprobe.exe e ffplay.exe per Eclipsis (Windows 64 bit, statici, LGPL 3):
# FFmpeg 8.1 con le opzioni di configurazione.txt e il decoder AAC di Windows
# (sorgenti/), dentro l'immagine Docker di FFmpeg-Builds che contiene il compilatore e le
# librerie gia' compilate (libass, zimg, dav1d, SDL2, zlib e le loro dipendenze).
# L'immagine e il commit di FFmpeg-Builds da cui e' costruita sono fissati in immagine.txt.
# Uscita in ./uscita: gli eseguibili, il sorgente corrispondente di FFmpeg e delle librerie
# (LGPL), le licenze di ogni componente e le impronte.
# Serve Linux con Docker, Python 3 e git (lo fa da solo il workflow .github/workflows/compila.yml).
set -euo pipefail
cd "$(dirname "$0")"

leggi() { sed -n "s/^$1=//p" immagine.txt | tail -n 1; }
IMMAGINE="${IMMAGINE:-$(leggi IMMAGINE)}"
FFMPEG_BUILDS_COMMIT="${FFMPEG_BUILDS_COMMIT:-$(leggi FFMPEG_BUILDS_COMMIT)}"
RAMO="${RAMO:-release/8.1}"
RELEASE="${RELEASE:-}"
export RAMO
# per impronta, mai per etichetta: "latest" cambierebbe le librerie senza dirlo
if [[ "$IMMAGINE" != *@sha256:* ]]; then
    echo "ERRORE: IMMAGINE va fissata per impronta (...@sha256:...), non per etichetta: $IMMAGINE" >&2
    exit 1
fi
if [[ ! "$FFMPEG_BUILDS_COMMIT" =~ ^[0-9a-f]{40}$ ]]; then
    echo "ERRORE: FFMPEG_BUILDS_COMMIT deve essere lo SHA completo di un commit: '$FFMPEG_BUILDS_COMMIT'" >&2
    exit 1
fi

rm -rf lavoro uscita
mkdir -p lavoro uscita
cp -r sorgenti configurazione.txt lavoro/

docker pull "$IMMAGINE"
echo "$IMMAGINE" > uscita/immagine_docker.txt

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

# i file del sorgente davvero compilati, dalle dipendenze .d che make lascia accanto a ogni
# oggetto: raccogli_librerie.py ne copia gli avvisi con licenze diverse dalla LGPL
# (AVVISI_FFMPEG.txt)
find . -name '*.d' -not -path './tests/*' -not -path './doc/*' -not -path './ffbuild/*' -print0 \
    | xargs -0 cat | tr -s ' ' '\n' | sed -e 's|^/lavoro/ffmpeg/||' -e 's|^\./||' \
    | grep -E '\.(c|h|cpp|asm|S|inc|css|html)$' | grep -v -E '^(/|tests/|doc/|tools/|ffbuild/)' \
    | sort -u > /lavoro/file_compilati_ffmpeg.txt
test -s /lavoro/file_compilati_ffmpeg.txt

# versioni delle librerie e del compilatore dentro l'immagine: raccogli_librerie.py le
# confronta con il sorgente che scarica (se non tornano, immagine e commit non sono coppia)
mingw_v() { echo '#include <_mingw.h>' | $CC -dM -E - | sed -n "s/^#define __MINGW64_VERSION_$1 //p"; }
{
    for p in zlib fribidi libxml-2.0 fontconfig harfbuzz libunibreak libass zimg dav1d sdl2 samplerate; do
        echo "$p=$(pkg-config --modversion "$p")"
    done
    echo "freetype=$(sed -n 's/^#define FREETYPE_\(MAJOR\|MINOR\|PATCH\) *\([0-9]*\).*/\2/p' \
        "$FFBUILD_PREFIX"/include/freetype2/freetype/freetype.h | paste -sd.)"
    v="$(sed -n 's/^#define _LIBICONV_VERSION 0x\([0-9A-Fa-f]*\).*/\1/p' "$FFBUILD_PREFIX"/include/iconv.h)"
    echo "libiconv=$((16#${v:0:2})).$((16#${v:2:2}))"
    echo "mingw=$(mingw_v MAJOR).$(mingw_v MINOR).$(mingw_v BUGFIX)"
    echo "gcc=$($CC -dumpfullversion)"
} > /lavoro/versioni_immagine.txt
cat /lavoro/versioni_immagine.txt
FINE

docker run --rm -e RAMO -u "$(id -u):$(id -g)" -v "$PWD/lavoro":/lavoro "$IMMAGINE" bash /lavoro/dentro.sh

cp lavoro/prefix/bin/ffmpeg.exe lavoro/prefix/bin/ffprobe.exe lavoro/prefix/bin/ffplay.exe uscita/
cp lavoro/commit_ffmpeg.txt lavoro/ffmpeg-sorgente.tar.gz lavoro/codificatori.txt \
   lavoro/decodificatori.txt lavoro/versioni_immagine.txt lavoro/file_compilati_ffmpeg.txt \
   configurazione.txt uscita/
tar -xzf lavoro/ffmpeg-sorgente.tar.gz -C lavoro ffmpeg-eclipsis/COPYING.LGPLv3 \
    ffmpeg-eclipsis/COPYING.GPLv3 ffmpeg-eclipsis/MODIFICHE_ECLIPSIS.txt
# LGPL 3: il testo della LGPL, quello della GPL a cui rimanda e l'avviso delle modifiche
cp lavoro/ffmpeg-eclipsis/COPYING.LGPLv3 uscita/LICENSE.txt
cp lavoro/ffmpeg-eclipsis/COPYING.GPLv3 uscita/COPYING.GPLv3.txt
cp lavoro/ffmpeg-eclipsis/MODIFICHE_ECLIPSIS.txt uscita/

# Licenze di ogni libreria collegata e loro sorgente corrispondente (FriBidi e libiconv sono
# LGPL e collegate in modo statico), al commit fissato da FFmpeg-Builds; controlla anche che
# immagine, commit ed eseguibili corrispondano.
python3 raccogli_librerie.py --ffmpeg-builds "$FFMPEG_BUILDS_COMMIT" --lavoro lavoro/librerie \
    --licenze uscita/licenze --archivio uscita/librerie-sorgente.tar \
    --eseguibili uscita/ffmpeg.exe uscita/ffprobe.exe uscita/ffplay.exe \
    --commit-ffmpeg uscita/commit_ffmpeg.txt --modifiche uscita/MODIFICHE_ECLIPSIS.txt \
    --ffmpeg-compilati uscita/file_compilati_ffmpeg.txt --sorgente-ffmpeg lavoro/ffmpeg \
    --origine-elenco "dipendenze .d della compilazione: file_compilati_ffmpeg.txt" \
    --immagine "$IMMAGINE" --versioni-immagine uscita/versioni_immagine.txt --release "$RELEASE"

(cd uscita && sha256sum ffmpeg.exe ffprobe.exe ffplay.exe ffmpeg-sorgente.tar.gz \
    librerie-sorgente.tar > SHA256SUMS.txt)
ls -la uscita uscita/licenze
