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


def annota(livello, titolo, testo):
    """Su GitHub una riga ::error:: (o ::notice::) diventa un'annotazione del lavoro."""
    if NEL_WORKFLOW:
        testo = testo.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::{livello} title={titolo}::{testo[:900]}", flush=True)


def scarto_migliore(a, b, massimo=4800, finestra=2048):
    """Di quanti valori va spostata b per somigliare di piu' ad a (a meta' file): dice se i
    due decoder tolgono un "priming" diverso. Restituisce (scarto, differenza massima)."""
    centro = len(a) // 2
    pezzo = a[centro:centro + finestra]
    migliore = (0, float("inf"))
    for scarto in range(-massimo, massimo + 1):
        inizio = centro + scarto
        if inizio < 0 or inizio + len(pezzo) > len(b):
            continue
        diff = 0.0
        for x, y in zip(pezzo, b[inizio:inizio + len(pezzo)]):
            d = abs(x - y)
            if d > diff:
                diff = d
                if diff >= migliore[1]:
                    break
        if diff < migliore[1]:
            migliore = (scarto, diff)
    return migliore


def decodifica(ffmpeg, percorso, decoder=None):
    comando = [ffmpeg, "-v", "error"]
    if decoder:
        comando += ["-c:a", decoder]
    comando += ["-i", percorso, "-map", "0:a:0", "-f", "f32le", "-acodec", "pcm_f32le", "-"]
    uscita = subprocess.run(comando, capture_output=True)
    if uscita.returncode != 0:
        raise RuntimeError(f"{ffmpeg} su {percorso}: {uscita.stderr.decode(errors='replace')[-800:]}")
    campioni = array.array("f")
    campioni.frombytes(uscita.stdout)
    return campioni


def main():
    ufficiale, eclipsis, file_prova = sys.argv[1], sys.argv[2], sys.argv[3:]
    errori = 0
    for percorso in file_prova:
        nome = os.path.basename(percorso)
        # un file che non si decodifica non ferma gli altri: si vedono tutti gli esiti
        try:
            a = decodifica(ufficiale, percorso)
            b = decodifica(eclipsis, percorso, "aac_mf")
        except RuntimeError as errore:
            errori += 1
            print(f"ERRORE   {nome}: {errore}", flush=True)
            annota("error", f"decodifica {nome}", str(errore))
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
            if n:
                scarto, diff = scarto_migliore(a, b)
                riga += f"; spostando di {scarto} valori la differenza scende a {diff:.2e}"
        print(riga, flush=True)
        annota("error" if esito != "ok" else "notice", f"confronto {nome}", riga)
    sys.exit(1 if errori else 0)


if __name__ == "__main__":
    main()
