"""Confronta l'audio decodificato da due FFmpeg: quello ufficiale (decoder AAC di FFmpeg) e
quello di Eclipsis (decoder AAC di Windows, aac_mf). Per ogni file: stesso numero di
campioni, stesso inizio (lo scarto dei campioni iniziali, il "priming") e differenza
massima. Solo libreria standard di Python.
Uso: confronta_aac.py FFMPEG_UFFICIALE FFMPEG_ECLIPSIS FILE [FILE ...]
Esce con 1 se un file non torna. Su GitHub ogni esito diventa anche un'annotazione, che si
legge senza accesso al repository (il registro no)."""
import array
import os
import subprocess
import sys

NEL_WORKFLOW = os.environ.get("GITHUB_ACTIONS") == "true"

SOGLIA = 2e-3   # differenza massima ammessa (i due decoder arrotondano in modo diverso)
# Il primo blocco di un file senza "priming" dichiarato (niente edit list) esce un po'
# diverso dai due decoder (misurato: fino a 1,2e-2 sui primi 1024 campioni); con il
# priming dichiarato quel blocco viene scartato da entrambi.
INIZIO = 8 * 1024
SOGLIA_INIZIO = 5e-2
SILENZIO = 1e-3            # sotto questo valore un campione conta come silenzio
MASSIMO_ANNOTAZIONE = 900  # caratteri di un'annotazione


def annota(livello, titolo, testo):
    """Su GitHub una riga ::error:: (o ::notice::) diventa un'annotazione del lavoro. Un
    testo troppo lungo tiene l'inizio e la fine: gli errori di FFmpeg sono in fondo."""
    if not NEL_WORKFLOW:
        return
    if len(testo) > MASSIMO_ANNOTAZIONE:
        testo = testo[:200] + " [...] " + testo[-(MASSIMO_ANNOTAZIONE - 207):]
    testo = testo.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::{livello} title={titolo}::{testo}", flush=True)


def decodifica(ffmpeg, percorso, decoder=None, livello="error"):
    # senza intestazione e avanzamento: con -v verbose restano solo i messaggi utili
    comando = [ffmpeg, "-hide_banner", "-nostats", "-v", livello]
    if decoder:
        comando += ["-c:a", decoder]
    comando += ["-i", percorso, "-map", "0:a:0", "-f", "f32le", "-acodec", "pcm_f32le", "-"]
    uscita = subprocess.run(comando, capture_output=True)
    if uscita.returncode != 0:
        raise RuntimeError(f"{os.path.basename(ffmpeg)}: "
                           f"{uscita.stderr.decode(errors='replace')[-1500:]}")
    campioni = array.array("f")
    campioni.frombytes(uscita.stdout)
    return campioni


def dettagli_errore(ffmpeg, percorso, decoder):
    """Rifa la decodifica fallita con i messaggi del decoder (-v verbose): le ultime righe
    dicono perche' (per esempio il tipo d'ingresso rifiutato e il suo HRESULT)."""
    try:
        decodifica(ffmpeg, percorso, decoder, livello="verbose")
    except (RuntimeError, OSError, ValueError) as errore:
        righe = [r for r in str(errore).splitlines() if r.strip()]
        return "\n".join(righe[-12:])
    return "con -v verbose la decodifica riesce"


def attacco(campioni):
    """Indice del primo valore non silenzioso (-1 se e' tutto silenzio)."""
    for i, x in enumerate(campioni):
        if abs(x) > SILENZIO:
            return i
    return -1


def primo_diverso(a, b):
    """Indice del primo valore oltre la soglia della sua zona (-1 se nessuno)."""
    for i, (x, y) in enumerate(zip(a, b)):
        if abs(x - y) > (SOGLIA_INIZIO if i < INIZIO else SOGLIA):
            return i
    return -1


def main():
    ufficiale, eclipsis, file_prova = sys.argv[1], sys.argv[2], sys.argv[3:]
    if not file_prova:
        # senza file la prova passerebbe a vuoto
        print("ERRORE   nessun file da confrontare", flush=True)
        annota("error", "confronto", "nessun file da confrontare")
        sys.exit(1)
    errori = 0
    for percorso in file_prova:
        nome = os.path.basename(percorso)
        # un file che non si decodifica non ferma gli altri: si vedono tutti gli esiti
        try:
            a = decodifica(ufficiale, percorso)
        except (RuntimeError, OSError, ValueError) as errore:
            errori += 1
            print(f"ERRORE   {nome} (FFmpeg ufficiale): {errore}", flush=True)
            annota("error", f"riferimento {nome}", str(errore))
            continue
        try:
            b = decodifica(eclipsis, percorso, "aac_mf")
        except (RuntimeError, OSError, ValueError) as errore:
            errori += 1
            dettagli = dettagli_errore(eclipsis, percorso, "aac_mf")
            print(f"ERRORE   {nome} (aac_mf): {errore}\n--- con -v verbose ---\n{dettagli}",
                  flush=True)
            annota("error", f"decodifica {nome}", f"{errore}\n--- con -v verbose ---\n{dettagli}")
            continue
        n = min(len(a), len(b))
        inizio = max((abs(x - y) for x, y in zip(a[:min(n, INIZIO)], b[:min(n, INIZIO)])), default=0.0)
        resto = max((abs(x - y) for x, y in zip(a[INIZIO:n], b[INIZIO:n])), default=0.0)
        esito = ("ok" if len(a) == len(b) and inizio <= SOGLIA_INIZIO and resto <= SOGLIA
                 else "DIVERSO")
        riga = (f"{esito:8s} {nome}: valori {len(a)} contro {len(b)}, differenza massima "
                f"{resto:.2e} (primo blocco {inizio:.2e})")
        if esito != "ok":
            errori += 1
            # per la causa: un rapporto di 2 o 1/3 vuol dire canali diversi; un attacco
            # spostato di 1024 o 2112 campioni per canale, un priming tolto in modo diverso
            if len(a):
                riga += f"; rapporto delle lunghezze {len(b) / len(a):.4f}"
            riga += (f"; attacco del suono a {attacco(a)} contro {attacco(b)}; "
                     f"primo valore oltre la soglia a {primo_diverso(a, b)}")
        print(riga, flush=True)
        annota("error" if esito != "ok" else "notice", f"confronto {nome}", riga)
    sys.exit(1 if errori else 0)


if __name__ == "__main__":
    main()
