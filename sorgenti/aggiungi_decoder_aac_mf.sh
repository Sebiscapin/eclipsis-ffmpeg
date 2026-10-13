#!/bin/bash
# Aggiunge a un sorgente di FFmpeg 8.1 il decoder "aac_mf" (sorgenti/mfdec.c): l'AAC
# decodificato dal decoder di Windows invece che da quello di FFmpeg.
# Uso: aggiungi_decoder_aac_mf.sh CARTELLA_DI_FFMPEG
# Si ferma con un errore se non trova le righe a cui agganciarsi (versione di FFmpeg
# diversa): meglio un errore che una build senza decoder.
set -euo pipefail

QUI="$(cd "$(dirname "$0")" && pwd)"
FF="$(cd "${1:-.}" && pwd)"

DATA_MODIFICHE="12 ottobre 2026"

cp "$QUI/mfdec.c" "$FF/libavcodec/mfdec.c"

# Aggiunge la riga NUOVA subito dopo la riga RIFERIMENTO (uguale, intera) di FILE.
# Riscrive il file al suo posto: configure resta eseguibile.
dopo() {
    local file="$1" riferimento="$2" nuova="$3"
    if ! grep -qxF -- "$riferimento" "$file"; then
        echo "ERRORE: in $file manca la riga: $riferimento" >&2
        exit 1
    fi
    if grep -qxF -- "$nuova" "$file"; then
        return 0
    fi
    awk -v r="$riferimento" -v n="$nuova" '{ print } $0 == r { print n }' "$file" > "$file.eclipsis"
    cat "$file.eclipsis" > "$file"
    rm -f "$file.eclipsis"
}

# Ogni riga aggiunta e' preceduta da un commento che dice chi l'ha aggiunta e quando
# (avviso di modifica nei file cambiati).
dopo "$FF/configure" \
    'aac_mf_encoder_deps="mediafoundation"' \
    "# Eclipsis, $DATA_MODIFICHE: decoder AAC tramite Media Foundation (aac_mf)"
dopo "$FF/configure" \
    "# Eclipsis, $DATA_MODIFICHE: decoder AAC tramite Media Foundation (aac_mf)" \
    'aac_mf_decoder_deps="mediafoundation"'
dopo "$FF/configure" \
    'aac_mf_decoder_deps="mediafoundation"' \
    'aac_mf_decoder_select="mpeg4audio"'
dopo "$FF/libavcodec/Makefile" \
    'OBJS-$(CONFIG_AAC_MF_ENCODER)          += mfenc.o mf_utils.o' \
    "# Eclipsis, $DATA_MODIFICHE: decoder AAC tramite Media Foundation (aac_mf)"
dopo "$FF/libavcodec/Makefile" \
    "# Eclipsis, $DATA_MODIFICHE: decoder AAC tramite Media Foundation (aac_mf)" \
    'OBJS-$(CONFIG_AAC_MF_DECODER)          += mfdec.o mf_utils.o'
dopo "$FF/libavcodec/allcodecs.c" \
    'extern const FFCodec ff_aac_mf_encoder;' \
    "/* Eclipsis, $DATA_MODIFICHE: decoder AAC tramite Media Foundation (aac_mf) */"
dopo "$FF/libavcodec/allcodecs.c" \
    "/* Eclipsis, $DATA_MODIFICHE: decoder AAC tramite Media Foundation (aac_mf) */" \
    'extern const FFCodec ff_aac_mf_decoder;'

# L'avviso generale, in cima al sorgente: cosa e' cambiato rispetto a FFmpeg e quando
cat > "$FF/MODIFICHE_ECLIPSIS.txt" <<AVVISO
Questo sorgente e' FFmpeg ($(git -C "$FF" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "release/8.1"))
modificato per Eclipsis il $DATA_MODIFICHE.
This source is FFmpeg, modified for Eclipsis on 12 October 2026.

Modifiche / Changes:
- libavcodec/mfdec.c (file nuovo / new file): decoder "aac_mf", che decodifica l'AAC con il
  decoder di Windows (Media Foundation, CLSID_MSAACDecMFT). / AAC decoding through the
  Windows AAC decoder MFT. Rivisto il 13 ottobre 2026: i flussi ADTS (.aac, MPEG-TS) si
  impostano dall'intestazione del primo pacchetto. / Revised on 13 October 2026: ADTS
  streams are set up from the header of their first packet.
- configure, libavcodec/Makefile, libavcodec/allcodecs.c: righe che registrano il decoder,
  ognuna preceduta da un commento "Eclipsis, $DATA_MODIFICHE". / Lines that register the
  decoder, each preceded by an "Eclipsis" comment.

Licenza / License: GNU Lesser General Public License versione 3 o successiva / version 3
or later (questa build e' configurata con --enable-version3): COPYING.LGPLv3, COPYING.GPLv3.
AVVISO

echo "decoder aac_mf aggiunto a $FF"
