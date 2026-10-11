#!/bin/bash
# Aggiunge a un sorgente di FFmpeg 8.1 il decoder "aac_mf" (sorgenti/mfdec.c): l'AAC
# decodificato dal decoder di Windows invece che da quello di FFmpeg.
# Uso: aggiungi_decoder_aac_mf.sh CARTELLA_DI_FFMPEG
# Si ferma con un errore se non trova le righe a cui agganciarsi (versione di FFmpeg
# diversa): meglio un errore che una build senza decoder.
set -euo pipefail

QUI="$(cd "$(dirname "$0")" && pwd)"
FF="$(cd "${1:-.}" && pwd)"

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

dopo "$FF/configure" \
    'aac_mf_encoder_deps="mediafoundation"' \
    'aac_mf_decoder_deps="mediafoundation"'
dopo "$FF/configure" \
    'aac_mf_decoder_deps="mediafoundation"' \
    'aac_mf_decoder_select="mpeg4audio"'
dopo "$FF/libavcodec/Makefile" \
    'OBJS-$(CONFIG_AAC_MF_ENCODER)          += mfenc.o mf_utils.o' \
    'OBJS-$(CONFIG_AAC_MF_DECODER)          += mfdec.o mf_utils.o'
dopo "$FF/libavcodec/allcodecs.c" \
    'extern const FFCodec ff_aac_mf_encoder;' \
    'extern const FFCodec ff_aac_mf_decoder;'

echo "decoder aac_mf aggiunto a $FF"
