#!/bin/bash
# Sorgente e avvisi di licenza delle librerie esterne che il linker ha messo dentro gli
# eseguibili (lavoro/librerie_collegate.txt, scritto da compila.sh; tabella in librerie.txt).
# Le librerie arrivano gia' compilate nell'immagine Docker di BtbN/FFmpeg-Builds, che non
# dice da quale commit di quel repository e' nata: lo dice il "run" di BtbN che l'ha creata
# (l'ora di creazione dell'immagine cade dentro quel run). A quel commit, lo script di ogni
# libreria (scripts.d) fissa il suo repository e il suo commit.
# Uscita in ./uscita:
#   librerie-sorgente.tar.gz  sorgente delle librerie e script di BtbN che le compilano
#   LICENZE-LIBRERIE.txt      avvisi di licenza, da distribuire insieme agli eseguibili
#   librerie_usate.txt        per ogni libreria: licenza, repository e commit
# Variabili: IMMAGINE (come in compila.sh); COMMIT_BTBN salta la ricerca del run; GH_TOKEN
# (facoltativa) alza il limite di richieste alle API di GitHub.
set -euo pipefail
cd "$(dirname "$0")"

IMMAGINE="${IMMAGINE:-ghcr.io/btbn/ffmpeg-builds/win64-lgpl-8.1:latest}"
BTBN="https://github.com/BtbN/FFmpeg-Builds"
LAV=lavoro/librerie
SORG="$LAV/librerie-sorgente"

errore() {
    echo "ERRORE: $*" >&2
    exit 1
}

# un comando di rete, fino a 3 tentativi (i server git ogni tanto non rispondono)
riprova() {
    local n
    for n in 1 2 3; do
        "$@" && return 0
        echo "tentativo $n non riuscito: $*" >&2
        sleep $((n * 10))
    done
    return 1
}

# clona senza estrarre i file (li scarica git quando servono): cartella url [url di riserva]
clona() {
    local dir="$1" url
    shift
    for url in "$@"; do
        [[ -n "$url" ]] || continue
        rm -rf "$dir"
        riprova git clone -q --filter=blob:none --no-checkout "$url" "$dir" && return 0
    done
    return 1
}

[[ -s lavoro/librerie_collegate.txt ]] || errore "manca lavoro/librerie_collegate.txt (lo scrive compila.sh)"
rm -rf "$LAV"
mkdir -p "$SORG" uscita
riprova git clone -q "$BTBN.git" "$LAV/btbn"

# percorso dello script di una libreria in scripts.d a un commit di BtbN (se ce ne sono due
# con lo stesso nome, come freetype, l'ultimo: e' quello che installa la libreria finale)
script_di() {
    git -C "$LAV/btbn" ls-tree -r --name-only "$1" scripts.d |
        grep -E "(^|/)[0-9]+-$2\.sh$" | sort | tail -n 1 || true
}

# valore di una variabile SCRIPT_... in uno script di BtbN: commit percorso variabile
var_di() {
    git -C "$LAV/btbn" show "$1:$2" | sed -n "/^$3=/{s/^$3=\"\(.*\)\"\$/\1/p;q}"
}

# --- 1. le librerie da raccogliere: quelle della tabella collegate davvero, piu' quelle "-"
declare -A COLLEGATA
while read -r lib; do
    COLLEGATA[$lib]=1
done < lavoro/librerie_collegate.txt

NOMI=() LICENZA=() TIPO=() EXTRA=()
declare -A IN_TABELLA
while read -r nomi script licenza tipo extra; do
    [[ "$tipo" == sorgente || "$tipo" == avviso ]] || errore "librerie.txt, $script: tipo \"$tipo\""
    usata=0
    if [[ "$nomi" == - ]]; then
        usata=1
    else
        IFS=, read -ra elenco <<< "$nomi"
        for lib in "${elenco[@]}"; do
            IN_TABELLA[$lib]=1
            [[ -n "${COLLEGATA[$lib]:-}" ]] && usata=1
        done
    fi
    if (( usata )); then
        NOMI+=("$script") LICENZA+=("$licenza") TIPO+=("$tipo") EXTRA+=("$extra")
    else
        echo "librerie.txt: $script non e' collegata in questa build" >&2
    fi
done < <(grep -v -e '^#' -e '^[[:space:]]*$' librerie.txt)

mancanti=()
for lib in "${!COLLEGATA[@]}"; do
    [[ -n "${IN_TABELLA[$lib]:-}" ]] || mancanti+=("$lib")
done
(( ${#mancanti[@]} == 0 )) ||
    errore "librerie collegate che mancano in librerie.txt (aggiungerle con la loro licenza): ${mancanti[*]}"

# --- 2. il commit di BtbN da cui e' nata l'immagine
# Le righe SCRIPT_... degli script scelti: se piu' run di BtbN sono possibili, basta che
# fissino le stesse librerie agli stessi commit.
firma() {
    local nome percorso
    for nome in "${NOMI[@]}"; do
        percorso="$(script_di "$1" "$nome")"
        [[ -n "$percorso" ]] || errore "BtbN $1: nessuno script \"$nome\" in scripts.d"
        echo "$nome"
        git -C "$LAV/btbn" show "$1:$percorso" | grep -E '^SCRIPT_(REPO|COMMIT|MIRROR)[0-9]*=' ||
            errore "BtbN $1, $percorso: nessuna riga SCRIPT_..."
    done
}

RUN_BTBN=""
if [[ -n "${COMMIT_BTBN:-}" ]]; then
    CANDIDATI=("$COMMIT_BTBN")
else
    CREATA="$(docker image inspect --format '{{.Created}}' "$IMMAGINE")"
    echo "immagine creata il $CREATA"
    # su stdout "commit url-del-run" per ogni run di BtbN dentro cui cade l'ora di creazione
    mapfile -t RUN < <(python3 - "$CREATA" <<'PY'
import datetime, json, os, sys, urllib.error, urllib.parse, urllib.request

def ora(testo):  # "2026-10-18T12:57:11.123456789Z" -> datetime in UTC (al secondo)
    return datetime.datetime.strptime(testo[:19], "%Y-%m-%dT%H:%M:%S").replace(
        tzinfo=datetime.timezone.utc)

creata = ora(sys.argv[1])
domanda = urllib.parse.urlencode({
    "per_page": 100, "created": "<=" + creata.strftime("%Y-%m-%dT%H:%M:%SZ")})
url = ("https://api.github.com/repos/BtbN/FFmpeg-Builds/actions/workflows/build.yml/runs?"
       + domanda)

def leggi(token):
    richiesta = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})
    if token:
        richiesta.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(richiesta, timeout=60) as risposta:
        return json.load(risposta)["workflow_runs"]

token = os.environ.get("GH_TOKEN", "")
try:
    runs = leggi(token)
except urllib.error.HTTPError:
    if not token:
        raise
    runs = leggi("")  # il token di un altro repository puo' non bastare: senza token
for run in runs:
    inizio = ora(run["run_started_at"])
    finito = run["status"] == "completed"
    if inizio <= creata and (not finito or creata <= ora(run["updated_at"])):
        print(run["head_sha"], run["html_url"])
PY
)
    (( ${#RUN[@]} > 0 )) ||
        errore "nessun run di BtbN/FFmpeg-Builds (build.yml) comprende l'ora di creazione dell'immagine ($CREATA), o la richiesta alle API di GitHub non e' riuscita (vedi sopra)"
    CANDIDATI=()
    for riga in "${RUN[@]}"; do
        echo "run di BtbN possibile: $riga"
        CANDIDATI+=("${riga%% *}")
        RUN_BTBN+="${RUN_BTBN:+ }${riga#* }"
    done
fi
COMMIT="${CANDIDATI[0]}"
FIRMA="$(firma "$COMMIT")"
for altro in "${CANDIDATI[@]:1}"; do
    [[ "$(firma "$altro")" == "$FIRMA" ]] ||
        errore "i run possibili di BtbN ($COMMIT e $altro) fissano librerie diverse"
done
echo "commit di BtbN/FFmpeg-Builds: $COMMIT"

# --- 3. sorgente e avvisi
# Lo stesso commit di BtbN completo (script e patch con cui sono compilate le librerie).
git -C "$LAV/btbn" archive --format=tar --prefix=FFmpeg-Builds/ "$COMMIT" | tar -x -C "$SORG"

ANNO_FREETYPE=""
{
    echo "Immagine Docker: $(cat uscita/immagine_docker.txt 2>/dev/null || echo "$IMMAGINE")"
    echo "BtbN/FFmpeg-Builds: $BTBN/tree/$COMMIT${RUN_BTBN:+ (run: $RUN_BTBN)}"
    echo
    printf '%-20s %-22s %-9s %s\n' libreria licenza tipo "repository e commit"
} > "$LAV/librerie_usate.txt"
: > "$LAV/avvisi.txt"

for i in "${!NOMI[@]}"; do
    nome="${NOMI[$i]}"
    percorso="$(script_di "$COMMIT" "$nome")"
    repo="$(var_di "$COMMIT" "$percorso" SCRIPT_REPO)"
    commit="$(var_di "$COMMIT" "$percorso" SCRIPT_COMMIT)"
    [[ -n "$repo" && -n "$commit" ]] || errore "$percorso: SCRIPT_REPO o SCRIPT_COMMIT mancante"
    dir="$LAV/git/$nome"
    clona "$dir" "$repo" "$(var_di "$COMMIT" "$percorso" SCRIPT_MIRROR)" ||
        errore "$nome: non riesco a clonare $repo"
    git -C "$dir" cat-file -e "$commit^{commit}" 2>/dev/null ||
        riprova git -C "$dir" fetch -q origin "$commit" ||
        errore "$nome: il commit $commit non c'e' in $repo"
    riga="$repo $commit"

    # i file di licenza: quelli della radice, quelli indicati in librerie.txt e, per il
    # sorgente intero, quelli della radice dei sottomoduli
    mapfile -t file_licenza < <(git -C "$dir" ls-tree "$commit" | awk '$2 == "blob" { print $4 }' |
        grep -iE '^(copying|licen[cs]e|copyright|notice)([._-].*)?$' || true)
    read -ra extra <<< "${EXTRA[$i]}"
    file_licenza+=("${extra[@]}")
    (( ${#file_licenza[@]} > 0 )) || errore "$nome: nessun file di licenza trovato"

    if [[ "${TIPO[$i]}" == sorgente ]]; then
        git -C "$dir" checkout -q "$commit"
        riprova git -C "$dir" submodule update -q --init --recursive
        # libiconv: BtbN aggiunge gnulib (il secondo repository dello script) dentro il sorgente
        repo2="$(var_di "$COMMIT" "$percorso" SCRIPT_REPO2)"
        if [[ -n "$repo2" ]]; then
            commit2="$(var_di "$COMMIT" "$percorso" SCRIPT_COMMIT2)"
            [[ "$nome" == libiconv ]] || errore "$nome: secondo repository non previsto ($repo2)"
            clona "$dir/gnulib" "$repo2" "$(var_di "$COMMIT" "$percorso" SCRIPT_MIRROR2)" ||
                errore "$nome: non riesco a clonare $repo2"
            git -C "$dir/gnulib" checkout -q "$commit2"
            riga+=" + $repo2 $commit2"
        fi
        while read -r sotto; do
            for f in "$dir/$sotto"/*; do
                [[ -f "$f" ]] || continue
                b="$(basename "$f")"
                if grep -qiE '^(copying|licen[cs]e|copyright|notice)([._-].*)?$' <<< "$b"; then
                    file_licenza+=("$sotto/$b")
                fi
            done
        # shellcheck disable=SC2016 # $displaypath lo espande git, per ogni sottomodulo
        done < <(git -C "$dir" submodule foreach --quiet --recursive 'echo "$displaypath"')
        mkdir -p "$SORG/$nome"
        # i file come nel repository, senza la cartella .git (anche quella dei sottomoduli)
        tar -C "$dir" --exclude=.git -cf - . | tar -x -C "$SORG/$nome"
    else
        git -C "$dir" checkout -q "$commit" -- "${file_licenza[@]}"
    fi

    if [[ "$nome" == freetype ]]; then
        ANNO_FREETYPE="$(git -C "$dir" show "$commit:include/freetype/freetype.h" |
            sed -n 's/.*Copyright (C) \([0-9][0-9-]*\) by.*/\1/p' | head -n 1)"
        [[ -n "$ANNO_FREETYPE" ]] || errore "freetype: anni del copyright non trovati"
    fi

    printf '%-20s %-22s %-9s %s\n' "$nome" "${LICENZA[$i]}" "${TIPO[$i]}" "$riga" >> "$LAV/librerie_usate.txt"
    {
        printf '%s\n' "================================================================================"
        echo "$nome ($(tr ',' ' ' <<< "${LICENZA[$i]}"))"
        echo "$riga"
        for f in "${file_licenza[@]}"; do
            printf -- '\n----- %s -----\n\n' "$f"
            cat "$dir/$f"
        done
        echo
    } >> "$LAV/avvisi.txt"
done

{
    cat <<'TESTO'
Avvisi di licenza delle librerie esterne contenute negli eseguibili di FFmpeg di questa
build (ffmpeg.exe, ffprobe.exe, ffplay.exe), collegate staticamente.
License notices of the third-party libraries statically linked into these FFmpeg
executables (ffmpeg.exe, ffprobe.exe, ffplay.exe).

FFmpeg: GNU Lesser General Public License versione 3 o successiva / version 3 or later
(LICENSE.txt, COPYING.GPLv3.txt).
Il sorgente di FFmpeg e' in ffmpeg-sorgente.tar.gz, quello delle librerie (con gli script
che le compilano) in librerie-sorgente.tar.gz: sono nella stessa release degli eseguibili.
The source code of FFmpeg is in ffmpeg-sorgente.tar.gz and that of the libraries (with the
scripts that build them) in librerie-sorgente.tar.gz, in the same release as the executables.
TESTO
    if [[ -n "$ANNO_FREETYPE" ]]; then
        echo
        echo "Portions of this software are copyright (C) $ANNO_FREETYPE The FreeType Project"
        echo "(www.freetype.org). All rights reserved."
    fi
    echo
    cat "$LAV/librerie_usate.txt"
    echo
    cat "$LAV/avvisi.txt"
} > uscita/LICENZE-LIBRERIE.txt

cp "$LAV/librerie_usate.txt" uscita/librerie_usate.txt
cp "$LAV/librerie_usate.txt" uscita/LICENZE-LIBRERIE.txt "$SORG/"
tar -C "$LAV" --sort=name --owner=0 --group=0 --numeric-owner \
    -czf uscita/librerie-sorgente.tar.gz librerie-sorgente
ls -la uscita/librerie-sorgente.tar.gz uscita/LICENZE-LIBRERIE.txt
cat uscita/librerie_usate.txt
