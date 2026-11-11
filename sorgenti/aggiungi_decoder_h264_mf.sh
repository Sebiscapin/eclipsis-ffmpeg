#!/bin/bash
# Aggiunge a un sorgente di FFmpeg 8.1 il decoder "h264_mf" (sorgenti/mfvdec.c): l'H.264
# decodificato dal decoder di Windows invece che da quello di FFmpeg (che resta spento in
# configurazione.txt; il parser h264 e il filtro h264_mp4toannexb restano).
# Uso: aggiungi_decoder_h264_mf.sh CARTELLA_DI_FFMPEG
# Va lanciato DOPO aggiungi_decoder_aac_mf.sh: si aggancia alle righe del decoder aac_mf
# (righe nostre, quindi esatte) e aggiorna il suo MODIFICHE_ECLIPSIS.txt.
# Si ferma con un errore se non trova le righe a cui agganciarsi (versione di FFmpeg
# diversa o aac_mf mancante): meglio un errore che una build senza decoder.
set -euo pipefail

QUI="$(cd "$(dirname "$0")" && pwd)"
FF="$(cd "${1:-.}" && pwd)"

DATA_MODIFICHE="11 novembre 2026"
COMMENTO="Eclipsis, $DATA_MODIFICHE: decoder H.264 tramite Media Foundation (h264_mf)"

cp "$QUI/mfvdec.c" "$FF/libavcodec/mfvdec.c"

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
    'aac_mf_decoder_select="mpeg4audio"' \
    "# $COMMENTO"
dopo "$FF/configure" \
    "# $COMMENTO" \
    'h264_mf_decoder_deps="mediafoundation"'
dopo "$FF/configure" \
    'h264_mf_decoder_deps="mediafoundation"' \
    'h264_mf_decoder_select="h264_mp4toannexb_bsf"'
dopo "$FF/libavcodec/Makefile" \
    'OBJS-$(CONFIG_AAC_MF_DECODER)          += mfdec.o mf_utils.o' \
    "# $COMMENTO"
dopo "$FF/libavcodec/Makefile" \
    "# $COMMENTO" \
    'OBJS-$(CONFIG_H264_MF_DECODER)         += mfvdec.o mf_utils.o'
dopo "$FF/libavcodec/allcodecs.c" \
    'extern const FFCodec ff_aac_mf_decoder;' \
    "/* $COMMENTO */"
dopo "$FF/libavcodec/allcodecs.c" \
    "/* $COMMENTO */" \
    'extern const FFCodec ff_h264_mf_decoder;'

# L'avviso generale in cima al sorgente (scritto da aggiungi_decoder_aac_mf.sh): la data e
# la voce del file nuovo, prima della licenza.
AVVISO="$FF/MODIFICHE_ECLIPSIS.txt"
dopo "$AVVISO" \
    'This source is FFmpeg, modified for Eclipsis on 12 October 2026.' \
    "Decoder H.264 (h264_mf) aggiunto l'$DATA_MODIFICHE. / H.264 decoder (h264_mf) added on 11 November 2026."
if ! grep -q '^Licenza / License:' "$AVVISO"; then
    echo "ERRORE: in $AVVISO manca la riga della licenza" >&2
    exit 1
fi
if ! grep -qF 'libavcodec/mfvdec.c' "$AVVISO"; then
    awk '/^Licenza \/ License:/ {
        print "- libavcodec/mfvdec.c (file nuovo / new file, 11 novembre 2026): decoder \"h264_mf\", che"
        print "  decodifica l'\''H.264 con il decoder di Windows (Media Foundation, CLSID_CMSH264DecoderMFT);"
        print "  il decoder H.264 di FFmpeg e'\'' spento. / H.264 decoding through the Windows H.264 decoder"
        print "  MFT; the FFmpeg H.264 decoder is disabled."
        print "- configure, libavcodec/Makefile, libavcodec/allcodecs.c: righe che registrano h264_mf,"
        print "  ognuna preceduta da un commento \"Eclipsis, 11 novembre 2026\". / Lines that register"
        print "  h264_mf, each preceded by an \"Eclipsis\" comment."
        print ""
    } { print }' "$AVVISO" > "$AVVISO.eclipsis"
    cat "$AVVISO.eclipsis" > "$AVVISO"
    rm -f "$AVVISO.eclipsis"
fi

echo "decoder h264_mf aggiunto a $FF"
