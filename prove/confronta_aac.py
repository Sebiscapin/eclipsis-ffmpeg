"""Confronta l'audio decodificato da due FFmpeg: quello ufficiale (decoder AAC di FFmpeg) e
quello di Eclipsis (decoder AAC di Windows, aac_mf). Per ogni file: stesso numero di
campioni, stesso inizio (lo scarto dei campioni iniziali, il "priming") e differenza
massima. Solo libreria standard di Python.
Uso: confronta_aac.py FFMPEG_UFFICIALE FFMPEG_ECLIPSIS FILE [FILE ...]
Esce con 1 se un file non torna."""
import array
import subprocess
import sys

SOGLIA = 2e-3   # differenza massima ammessa (i due decoder arrotondano in modo diverso)
# Il primo blocco di un file senza "priming" dichiarato (niente edit list) esce un po'
# diverso dai due decoder (misurato: fino a 1,2e-2 sui primi 1024 campioni); con il
# priming dichiarato quel blocco viene scartato da entrambi.
INIZIO = 8 * 1024
SOGLIA_INIZIO = 5e-2


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
        a = decodifica(ufficiale, percorso)
        b = decodifica(eclipsis, percorso, "aac_mf")
        n = min(len(a), len(b))
        inizio = max((abs(x - y) for x, y in zip(a[:min(n, INIZIO)], b[:min(n, INIZIO)])), default=0.0)
        resto = max((abs(x - y) for x, y in zip(a[INIZIO:n], b[INIZIO:n])), default=0.0)
        esito = ("ok" if len(a) == len(b) and inizio <= SOGLIA_INIZIO and resto <= SOGLIA
                 else "DIVERSO")
        if esito != "ok":
            errori += 1
        print(f"{esito:8s} {percorso}: valori {len(a)} contro {len(b)}, differenza massima "
              f"{resto:.2e} (primo blocco {inizio:.2e})")
    sys.exit(1 if errori else 0)


if __name__ == "__main__":
    main()
