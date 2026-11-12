"""Confronta il video decodificato da due FFmpeg: quello ufficiale (decoder H.264 di FFmpeg) e
quello di Eclipsis (decoder H.264 di Windows, h264_mf). Per ogni file:
- framemd5 di tutti i fotogrammi: stessi tempi, durate e impronte (devono essere identici);
- framemd5 dopo una ricerca (-ss), salvo i flussi grezzi .h264 (li' la ricerca e' per byte);
- su hd.mp4 (B-frame) anche framemd5 con -threads 1, che porta h264_mf alla bassa latenza;
- i campi di ffprobe: dimensioni, formato dei pixel e colore devono tornare, gli altri campi
  diversi diventano avvisi da guardare.
Solo libreria standard di Python. ffprobe si cerca accanto a ciascun ffmpeg.
Uso: confronta_h264.py FFMPEG_UFFICIALE FFMPEG_ECLIPSIS [--ss SECONDI] FILE [FILE ...]
Esce con 1 se un file non torna. Su GitHub ogni esito diventa anche un'annotazione, che si
legge senza accesso al repository (il registro no)."""
import json
import os
import subprocess
import sys

NEL_WORKFLOW = os.environ.get("GITHUB_ACTIONS") == "true"
MASSIMO_ANNOTAZIONE = 900
FOTOGRAMMI_DOPO_RICERCA = 12
# campi che devono essere uguali; gli altri, se diversi, sono solo avvisi
CAMPI_OBBLIGATORI = ("width", "height", "pix_fmt", "color_range", "color_space",
                     "color_transfer", "color_primaries")
CAMPI_AVVISO = ("codec_name", "profile", "level", "coded_width", "coded_height",
                "chroma_location", "sample_aspect_ratio", "r_frame_rate", "avg_frame_rate",
                "has_b_frames", "nb_read_frames")


def annota(livello, titolo, testo):
    if not NEL_WORKFLOW:
        return
    if len(testo) > MASSIMO_ANNOTAZIONE:
        testo = testo[:200] + " [...] " + testo[-(MASSIMO_ANNOTAZIONE - 207):]
    testo = testo.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::{livello} title={titolo}::{testo}", flush=True)


def esegui(comando):
    uscita = subprocess.run(comando, capture_output=True)
    if uscita.returncode != 0:
        raise RuntimeError(f"{os.path.basename(comando[0])}: "
                           f"{uscita.stderr.decode(errors='replace')[-1500:]}")
    return uscita.stdout.decode(errors="replace")


def framemd5(ffmpeg, percorso, decoder=None, ss=None, thread=None):
    """Righe (stream, dts, pts, durata, dimensione, impronta) del framemd5, senza le
    intestazioni. -noautoscale: un cambio di dimensioni resta tale e quale (-autoscale e'
    un'opzione booleana: "-autoscale 0" prenderebbe lo 0 come file d'uscita). thread: i
    thread del decoder (-threads prima di -i)."""
    comando = [ffmpeg, "-hide_banner", "-nostats", "-v", "error"]
    if ss is not None:
        comando += ["-ss", str(ss)]
    if thread is not None:
        comando += ["-threads", str(thread)]
    if decoder:
        comando += ["-c:v", decoder]
    comando += ["-i", percorso, "-map", "0:v:0", "-noautoscale"]
    if ss is not None:
        comando += ["-frames:v", str(FOTOGRAMMI_DOPO_RICERCA)]
    comando += ["-f", "framemd5", "-"]
    return [tuple(c.strip() for c in riga.split(","))
            for riga in esegui(comando).splitlines() if riga.strip() and not riga.startswith("#")]


def campi(ffmpeg, percorso):
    ffprobe = os.path.join(os.path.dirname(os.path.abspath(ffmpeg)), "ffprobe.exe")
    if not os.path.exists(ffprobe):
        ffprobe = os.path.join(os.path.dirname(os.path.abspath(ffmpeg)), "ffprobe")
    testo = esegui([ffprobe, "-v", "error", "-count_frames", "-select_streams", "v:0",
                    "-show_entries", "stream=" + ",".join(CAMPI_OBBLIGATORI + CAMPI_AVVISO),
                    "-of", "json", percorso])
    flussi = json.loads(testo).get("streams") or [{}]
    return flussi[0]


def confronta_righe(a, b):
    """Descrizione della prima differenza fra due framemd5, None se identici."""
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return f"fotogramma {i}: {', '.join(x)} contro {', '.join(y)}"
    if len(a) != len(b):
        return f"fotogrammi {len(a)} contro {len(b)}"
    return None


def main():
    argomenti = sys.argv[1:]
    ss = 1.3
    if "--ss" in argomenti:
        i = argomenti.index("--ss")
        ss = float(argomenti[i + 1])
        del argomenti[i:i + 2]
    if len(argomenti) < 3:
        print("ERRORE   servono i due ffmpeg e almeno un file", flush=True)
        annota("error", "confronto", "servono i due ffmpeg e almeno un file")
        sys.exit(1)
    ufficiale, eclipsis, file_prova = argomenti[0], argomenti[1], argomenti[2:]
    errori = 0
    for percorso in file_prova:
        nome = os.path.basename(percorso)
        prove = [("tutto", None, None)]
        if not nome.lower().endswith((".h264", ".264")):
            prove.append((f"da {ss} s", ss, None))
        if nome.lower() == "hd.mp4":
            prove.append(("tutto, -threads 1", None, 1))
        for cosa, inizio, thread in prove:
            try:
                a = framemd5(ufficiale, percorso, ss=inizio, thread=thread)
                b = framemd5(eclipsis, percorso, "h264_mf", ss=inizio, thread=thread)
            except (RuntimeError, OSError, ValueError) as errore:
                errori += 1
                print(f"ERRORE   {nome} ({cosa}): {errore}", flush=True)
                annota("error", f"decodifica {nome}", f"{cosa}: {errore}")
                continue
            differenza = confronta_righe(a, b) if a else "nessun fotogramma dal riferimento"
            esito = "ok" if differenza is None else "DIVERSO"
            riga = f"{esito:8s} {nome} ({cosa}): {len(a)} fotogrammi contro {len(b)}"
            if differenza:
                errori += 1
                riga += f"; {differenza}"
            print(riga, flush=True)
            annota("error" if differenza else "notice", f"framemd5 {nome}", riga)
        try:
            ca, cb = campi(ufficiale, percorso), campi(eclipsis, percorso)
        except (RuntimeError, OSError, ValueError) as errore:
            errori += 1
            print(f"ERRORE   {nome} (ffprobe): {errore}", flush=True)
            annota("error", f"ffprobe {nome}", str(errore))
            continue
        diversi = [k for k in CAMPI_OBBLIGATORI if ca.get(k) != cb.get(k)]
        avvisi = [k for k in CAMPI_AVVISO if ca.get(k) != cb.get(k)]
        descrivi = lambda chiavi: "; ".join(f"{k} {ca.get(k)} contro {cb.get(k)}" for k in chiavi)
        if diversi:
            errori += 1
            riga = f"DIVERSO  {nome} (ffprobe): {descrivi(diversi)}"
            print(riga, flush=True)
            annota("error", f"ffprobe {nome}", riga)
        else:
            print(f"ok       {nome} (ffprobe): {ca.get('width')}x{ca.get('height')} "
                  f"{ca.get('pix_fmt')} {ca.get('color_range')} {ca.get('color_space')}", flush=True)
        if avvisi:
            riga = f"avviso   {nome} (ffprobe, da guardare): {descrivi(avvisi)}"
            print(riga, flush=True)
            annota("warning", f"ffprobe {nome}", riga)
    sys.exit(1 if errori else 0)


if __name__ == "__main__":
    main()
