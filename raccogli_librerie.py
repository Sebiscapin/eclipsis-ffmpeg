#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Licenze e sorgente corrispondente delle librerie collegate dentro ffmpeg.exe, ffprobe.exe e
ffplay.exe di Eclipsis, compilati nell'immagine Docker di FFmpeg-Builds (vedi compila.sh).

Cosa fa, senza compilare ne' eseguire niente di quello che scarica:
  1. scarica FFmpeg-Builds al commit da cui e' costruita l'immagine e legge, negli script
     di ogni libreria collegata (scripts.d), il repository e il commit usati;
  2. scarica ogni libreria a quel commit (git fetch --depth=1, come git-mini-clone di
     FFmpeg-Builds) con le parti che la compilazione aggiunge (sottomoduli, gnulib);
  3. confronta le versioni con quelle lette nell'immagine (versioni_immagine.txt, scritto da
     compila.sh) e con le stringhe dentro gli eseguibili: se non tornano si ferma;
  4. scrive la cartella delle licenze: un testo per componente (licenza, copyright, avvisi
     delle parti con licenze diverse), LEGGIMI.txt e librerie.json;
  5. scrive l'archivio del sorgente: un .tar.xz per libreria al commit esatto, FFmpeg-Builds
     e questo repository al commit della build, LEGGIMI.txt, MANIFEST.json, SHA256SUMS.txt.

Uso (lo lancia compila.sh):
  python3 raccogli_librerie.py --ffmpeg-builds <commit> --lavoro lavoro/librerie \\
      --licenze uscita/licenze --archivio uscita/librerie-sorgente.tar \\
      --eseguibili uscita/ffmpeg.exe uscita/ffprobe.exe uscita/ffplay.exe \\
      --commit-ffmpeg uscita/commit_ffmpeg.txt --modifiche uscita/MODIFICHE_ECLIPSIS.txt \\
      --immagine "<immagine@sha256:...>" --versioni-immagine uscita/versioni_immagine.txt

Serve Python 3.9+ e git; solo la libreria standard.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import lzma
import re
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path

QUI = Path(__file__).resolve().parent

FFMPEG_BUILDS_REPO = ["https://github.com/FFmpeg-Builds/FFmpeg-Builds.git",
                      "https://github.com/BtbN/FFmpeg-Builds.git"]
GCC_REPO = ["https://github.com/gcc-mirror/gcc.git", "https://gcc.gnu.org/git/gcc.git"]
TUTTI = ["ffmpeg.exe", "ffprobe.exe", "ffplay.exe"]
LINEA = "=" * 78


# --------------------------------------------------------------------------------------
# versioni lette dal sorgente (l: funzione percorso -> testo del file al commit)
# --------------------------------------------------------------------------------------
def _cerca(l, percorso, regola):
    m = re.search(regola, l(percorso), re.M)
    if not m:
        raise RuntimeError(f"versione non trovata in {percorso}")
    return m.group(1).strip()


def _v_meson(l):
    # la prima "version:" dentro project(...), non "meson_version:"
    m = re.search(r"project\s*\(.*?(?<!\w)version\s*:\s*'([^']+)'", l("meson.build"), re.S)
    if not m:
        raise RuntimeError("versione non trovata in meson.build")
    return m.group(1).strip()


def _v_freetype(l):
    t = l("include/freetype/freetype.h")
    return ".".join(re.search(rf"#define FREETYPE_{k}\s+(\d+)", t).group(1)
                    for k in ("MAJOR", "MINOR", "PATCH"))


def _v_libiconv(l):
    n = int(_cerca(l, "include/iconv.h.in", r"#define _LIBICONV_VERSION (0x[0-9A-Fa-f]+)"), 16)
    return f"{n >> 8}.{n & 0xFF}"


def _v_sdl(l):
    t = l("include/SDL_version.h")
    return ".".join(re.search(rf"#define SDL_{k}\s+(\d+)", t).group(1)
                    for k in ("MAJOR_VERSION", "MINOR_VERSION", "PATCHLEVEL"))


def _v_mingw(l):
    t = l("mingw-w64-headers/crt/_mingw_mac.h")
    v = ".".join(re.search(rf"#define __MINGW64_VERSION_{k}\s+(\d+)", t).group(1)
                 for k in ("MAJOR", "MINOR", "BUGFIX"))
    stato = re.search(r'#define __MINGW64_VERSION_STATE\s+"([^"]*)"', t)
    return v + (f" ({stato.group(1)})" if stato and stato.group(1) != "stable" else "")


# --------------------------------------------------------------------------------------
# Le librerie collegate negli eseguibili: quelle di configurazione.txt (zlib, sdl2, libass,
# zimg, dav1d) e cio' che libass si porta dietro. Verificato sulla release v8.1-1 leggendo
# gli eseguibili: ci sono anche libiconv (tramite libass, libxml2, Fontconfig, SDL),
# winpthreads e, solo in ffplay.exe, libsamplerate (tramite SDL); NON ci sono libpng,
# brotli, bzip2, liblzma, libgomp (controlla_eseguibili si ferma se compaiono).
#   script: script di FFmpeg-Builds (scripts.d) che fissa repository e commit
#   file: file di licenza copiati per intero; intestazioni: file di cui si copia il primo
#   commento con la licenza (parti con licenze diverse da quella principale)
#   copyright_da: cartelle da cui raccogliere le righe "Copyright" (librerie LGPL)
#   parti: sorgenti che la compilazione aggiunge (sottomodulo = commit registrato nel
#   repository; altrimenti la seconda coppia SCRIPT_REPO2 / SCRIPT_COMMIT2 dello script)
#   immagine: nome della riga in versioni_immagine.txt
# --------------------------------------------------------------------------------------
RICETTA = [
    dict(id="zlib", nome="zlib", script="20-zlib.sh", licenza="Zlib", obbligo="ffmpeg",
         file=["LICENSE"], immagine="zlib",
         versione=lambda l: _cerca(l, "zlib.h", r'#define ZLIB_VERSION "([^"]+)"'),
         nota="compressione (PNG, filtri e formati di FFmpeg)"),
    dict(id="fribidi", nome="GNU FriBidi", script="25-fribidi.sh",
         licenza="LGPL-2.1-or-later", obbligo="propria", file=["COPYING", "AUTHORS"],
         copyright_da=["lib/"], versione=_v_meson, immagine="fribidi",
         nota="testo bidirezionale dei sottotitoli (libass)",
         avvisi=["FriBidi è collegata in modo statico. Il sorgente di tutte le parti degli",
                 "eseguibili (FFmpeg, ogni libreria e gli script di compilazione) è pubblicato",
                 "insieme ai programmi: si può modificare FriBidi e ricollegare i programmi",
                 "(LGPL-2.1 sez. 6)."]),
    dict(id="libiconv", nome="GNU libiconv", script="20-libiconv.sh",
         licenza="LGPL-2.1-or-later", obbligo="propria", file=["COPYING.LIB"],
         copyright_da=["lib/", "libcharset/lib/"], versione=_v_libiconv, immagine="libiconv",
         nota="conversione delle codifiche (libass, libxml2, Fontconfig, SDL)",
         parti=[dict(nome="gnulib", percorso="gnulib", var="SCRIPT_REPO2", mirror="SCRIPT_MIRROR2",
                     commit="SCRIPT_COMMIT2",
                     nota="usata da autogen.sh per preparare la compilazione")],
         avvisi=["Solo la libreria (lib/, libcharset/): il programma iconv (GPL) non c'è.",
                 "Collegata in modo statico: vedi l'avviso di FriBidi sul ricollegamento."]),
    dict(id="libxml2", nome="libxml2", script="25-libxml2.sh", licenza="MIT AND ISC-Veillard",
         obbligo="ffmpeg", file=["Copyright"], intestazioni=["dict.c", "list.c", "timsort.h"],
         versione=lambda l: l("VERSION").strip(), immagine="libxml-2.0",
         nota="lettura dei file di configurazione di Fontconfig"),
    dict(id="freetype", nome="FreeType", script="45-fonts/50-freetype.sh",
         licenza="FTL AND MIT AND Zlib AND MIT-Modern-Variant", obbligo="ffmpeg",
         file=["LICENSE.TXT", "docs/FTL.TXT", "src/bdf/README", "src/pcf/README"],
         intestazioni=["src/base/fthash.c", "src/gzip/zlib.h", "src/autofit/ft-hb-ft.c"],
         versione=_v_freetype, immagine="freetype",
         nota="lettura e disegno dei caratteri (libass); FTL scelta fra FTL e GPL-2.0-or-later",
         parti=[dict(nome="dlg", percorso="subprojects/dlg", sottomodulo=True,
                     nota="scaricato da autogen.sh; compilato solo con FT_DEBUG_LOGGING, "
                          "quindi non è negli eseguibili")],
         avvisi=["FreeType è distribuita a scelta con la FreeType License (FTL) o con la",
                 "GPL-2.0-or-later: qui si usa la FTL. Come chiede la FTL:",
                 "",
                 "    Portions of this software are copyright © {anno} The FreeType",
                 "    Project (https://freetype.org).  All rights reserved.",
                 "",
                 "Questo software si basa in parte sul lavoro del FreeType Team.",
                 "(This software is based in part on the work of the FreeType Team.)"]),
    dict(id="fontconfig", nome="Fontconfig", script="45-fonts/35-fontconfig.sh",
         licenza="HPND-sell-variant AND MIT-Modern-Variant AND MIT AND Unicode-3.0 AND "
                 "LicenseRef-Public-Domain", obbligo="ffmpeg", file=["COPYING"],
         versione=_v_meson, immagine="fontconfig",
         nota="ricerca dei caratteri per libass (insieme a DirectWrite di Windows)"),
    dict(id="harfbuzz", nome="HarfBuzz", script="45-fonts/45-harfbuzz.sh",
         licenza="MIT-Modern-Variant AND MIT AND ISC AND Unicode-3.0", obbligo="ffmpeg",
         file=["COPYING", "src/ms-use/COPYING"],
         intestazioni=["src/hb-ucd.cc", "src/hb-unicode-emoji-table.hh"],
         versione=_v_meson, immagine="harfbuzz",
         nota="composizione del testo (libass); compilata con FreeType e GDI/Uniscribe"),
    dict(id="libunibreak", nome="libunibreak", script="45-libunibreak.sh", licenza="Zlib",
         obbligo="ffmpeg", file=["LICENCE"], immagine="libunibreak",
         versione=lambda l: _cerca(l, "configure.ac", r"AC_INIT\(\[libunibreak\],\s*\[([^\]]+)\]"),
         nota="a capo dei sottotitoli secondo le regole Unicode (libass)"),
    dict(id="libass", nome="libass", script="50-libass.sh", licenza="ISC AND HPND-UC AND Unlicense",
         obbligo="ffmpeg", file=["COPYING"],
         intestazioni=["libass/ass_strtod.c", "libass/x86/x86inc.asm", "libass/wyhash.h"],
         versione=lambda l: l("RELEASEVERSION").strip(), immagine="libass",
         nota="disegno dei sottotitoli (filtro subtitles)",
         avvisi=["Compilata con Fontconfig, DirectWrite, HarfBuzz, FriBidi e libunibreak."]),
    dict(id="zimg", nome="zimg", script="50-zimg.sh", licenza="WTFPL", obbligo="ffmpeg",
         file=["COPYING"], parti_file={"graphengine": ["COPYING"]}, immagine="zimg",
         versione=lambda l: _cerca(l, "configure.ac", r"AC_INIT\(\[zimg\],\s*\[([^\]]+)\]"),
         nota="filtro zscale (conversione di colore e dimensioni)",
         parti=[dict(nome="graphengine", percorso="graphengine", sottomodulo=True,
                     nota="sottomodulo compilato dentro zimg")],
         avvisi=["Il sottomodulo test/extra/googletest serve solo alle prove: non è compilato",
                 "negli eseguibili né incluso nell'archivio del sorgente."]),
    dict(id="dav1d", nome="dav1d", script="50-dav1d.sh", licenza="BSD-2-Clause AND ISC",
         obbligo="ffmpeg", file=["COPYING", "doc/PATENTS"],
         intestazioni=["src/ext/x86/x86inc.asm"], versione=_v_meson, immagine="dav1d",
         nota="decoder AV1; doc/PATENTS (AOM Patent License 1.0) va riportata con i binari",
         avvisi=["doc/PATENTS è la Alliance for Open Media Patent License 1.0: la sua",
                 "sezione 1.2.1(b) chiede di riprodurla nella documentazione di chi",
                 "distribuisce un'implementazione in forma binaria (è qui sotto)."]),
    dict(id="libsamplerate", nome="libsamplerate (Secret Rabbit Code)",
         script="45-libsamplerate.sh", licenza="BSD-2-Clause", obbligo="ffmpeg",
         file=["COPYING"], immagine="samplerate", eseguibili=["ffplay.exe"],
         versione=lambda l: _cerca(l, "CMakeLists.txt", r"project\(libsamplerate VERSION ([0-9.]+)"),
         nota="conversione della frequenza dell'audio dentro SDL (solo ffplay.exe)"),
    dict(id="sdl2", nome="SDL 2", script="50-sdl.sh",
         licenza="Zlib AND BSD-3-Clause AND SunPro AND MIT", obbligo="ffmpeg",
         file=["LICENSE.txt", "src/hidapi/LICENSE.txt", "src/hidapi/LICENSE-bsd.txt",
               "src/video/yuv2rgb/LICENSE"],
         intestazioni=["src/libm/e_atan2.c", "src/video/khronos/KHR/khrplatform.h"],
         versione=_v_sdl, immagine="sdl2", eseguibili=["ffplay.exe"],
         nota="finestra, audio e tastiera di ffplay (solo ffplay.exe)",
         avvisi=["HIDAPI è usata con la licenza in stile BSD (LICENSE-bsd.txt), una delle",
                 "tre a scelta. Le intestazioni Khronos di Vulkan (src/video/khronos/vulkan,",
                 "Apache-2.0) danno solo tipi e costanti: nessun codice negli eseguibili."]),
    dict(id="mingw-w64-crt", nome="MinGW-w64 runtime (CRT)", script="10-mingw.sh",
         licenza="ZPL-2.1 AND ISC AND BSD-2-Clause AND MIT AND SunPro AND "
                 "LicenseRef-Lucent-gdtoa AND LicenseRef-Cephes AND LicenseRef-Public-Domain AND "
                 "LGPL-2.1-or-later", obbligo="sistema",
         file=["COPYING.MinGW-w64-runtime/COPYING.MinGW-w64-runtime.txt"],
         versione=_v_mingw, immagine="mingw", cartella="mingw-w64",
         nota="runtime C del compilatore (avvio, matematica, stampa, protezione dello stack)",
         avvisi=["Il runtime collegato in ogni programma per Windows fatto con MinGW-w64; i",
                 "programmi usano la UCRT di Windows. Le intestazioni e gli IDL importati da",
                 "Wine (LGPL-2.1-or-later) servono solo a compilare: nei programmi finiscono al",
                 "più dichiarazioni, costanti e piccole funzioni (LGPL-2.1 sez. 5). Il sorgente",
                 "completo di mingw-w64 è comunque nell'archivio del sorgente."]),
    dict(id="winpthreads", nome="winpthreads (MinGW-w64)", script="10-mingw.sh",
         licenza="MIT AND BSD-3-Clause", obbligo="sistema",
         file=["mingw-w64-libraries/winpthreads/COPYING"], versione=_v_mingw, immagine="mingw",
         stesso_sorgente="mingw-w64-crt", nota="thread POSIX per Windows (usati da libstdc++)"),
]

# Perche' il sorgente e' obbligatorio (o no). FFmpeg qui e' LGPL-3.0, cioe' GPL-3.0 con
# permessi in piu': per la GPL-3.0 (sez. 1 e 6) il sorgente corrispondente di un eseguibile
# comprende tutto cio' che ci e' collegato, tranne le "librerie di sistema" (fra cui quelle
# che accompagnano il compilatore). Le librerie "ffmpeg" non lo chiederebbero da sole.
MOTIVI = {
    "propria": "obbligatorio: la sua licenza (LGPL-2.1-or-later, collegata in modo statico) e la "
               "LGPL-3.0 di FFmpeg",
    "ffmpeg": "obbligatorio: fa parte del sorgente corrispondente degli eseguibili di FFmpeg "
              "(LGPL-3.0, cioè GPL-3.0 sez. 1 e 6), anche se la sua licenza da sola non lo chiede",
    "sistema": "non obbligatorio: libreria di sistema del compilatore (GPL-3.0 sez. 1); il "
               "sorgente è comunque nell'archivio",
}

GCC = dict(id="gcc-runtime", nome="Librerie di supporto di GCC (libgcc, libstdc++)",
           licenza="GPL-3.0-or-later WITH GCC-exception-3.1",
           avvisi=["Codice di supporto del compilatore GCC collegato negli eseguibili. La GCC",
                   "Runtime Library Exception 3.1 permette di distribuire il risultato con",
                   "qualsiasi licenza: non chiede di pubblicare il sorgente né di riportare",
                   "questo testo (incluso per completezza). Sorgente: https://gcc.gnu.org"])

UNICODE = dict(id="unicode", nome="Dati Unicode (Unicode Character Database)",
               licenza="Unicode-3.0",
               avvisi=["Tabelle generate dai dati Unicode e compilate dentro FriBidi, HarfBuzz,",
                       "libunibreak, libxml2 e Fontconfig. La licenza Unicode chiede che questo",
                       "avviso accompagni le copie o la loro documentazione."])


# --------------------------------------------------------------------------------------
# utilita'
# --------------------------------------------------------------------------------------
def scrivi(*parti):
    print(" ".join(str(p) for p in parti), flush=True)


def esegui(cmd, cwd=None, binario=False, controlla=True, timeout=900):
    r = subprocess.run([str(c) for c in cmd], cwd=cwd, capture_output=True, timeout=timeout)
    if controlla and r.returncode != 0:
        raise RuntimeError(f"comando fallito ({r.returncode}): {' '.join(map(str, cmd))}\n"
                           f"{r.stderr.decode('utf-8', 'replace')[-2000:]}")
    return r if binario else r.stdout.decode("utf-8", "replace")


def git(cart, *args, **kw):
    # niente conversioni dei fine riga: l'archivio deve essere uguale al checkout su Linux
    base = ["git", "-c", "core.autocrlf=false", "-c", "core.eol=lf", "-c", "core.quotepath=off"]
    if cart is not None:
        base += ["-C", str(cart)]
    return esegui(base + list(args), **kw)


def scrivi_testo(percorso: Path, testo: str):
    """UTF-8 con fine riga LF anche su Windows: file e impronte uguali su ogni sistema."""
    with open(percorso, "w", encoding="utf-8", newline="\n") as f:
        f.write(testo)


def sha256(percorso: Path) -> str:
    h = hashlib.sha256()
    with open(percorso, "rb") as f:
        for blocco in iter(lambda: f.read(1 << 20), b""):
            h.update(blocco)
    return h.hexdigest()


def nome_cartella(voce, commit):
    return f"{voce}-{commit[:12]}"


class Repo:
    """Repository nudo con un commit scaricato per SHA (come git-mini-clone)."""

    def __init__(self, cart: Path, commit: str):
        self.cart, self.commit = cart, commit

    def leggi(self, percorso: str) -> str:
        r = git(self.cart, "show", f"{self.commit}:{percorso}", binario=True)
        return r.stdout.decode("utf-8", "replace")

    def file(self) -> list:
        return git(self.cart, "ls-tree", "-r", "--name-only", self.commit).splitlines()

    def gitlink(self, percorso: str):
        """Commit e indirizzo di un sottomodulo registrato nel repository."""
        riga = git(self.cart, "ls-tree", self.commit, percorso).split()
        if len(riga) < 3 or riga[1] != "commit":
            raise RuntimeError(f"{percorso} non e' un sottomodulo")
        url = None
        for blocco in re.split(r"(?m)^\[", self.leggi(".gitmodules")):
            if re.search(rf"(?m)^\s*path\s*=\s*{re.escape(percorso)}\s*$", blocco):
                m = re.search(r"(?m)^\s*url\s*=\s*(\S+)", blocco)
                url = m.group(1) if m else None
        if not url:
            raise RuntimeError(f"indirizzo del sottomodulo {percorso} non trovato")
        return riga[2], url

    def data(self) -> str:
        return git(self.cart, "log", "-1", "--format=%cI", self.commit).strip()

    def archivia(self, prefisso: str, dest: Path):
        """git archive del commit, compresso in .tar.xz (export-ignore ed export-subst
        spenti in info/attributes da preleva)."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        r = git(self.cart, "archive", "--format=tar", f"--prefix={prefisso}/", self.commit,
                binario=True)
        with lzma.open(dest, "wb", preset=6) as f:
            f.write(r.stdout)


def preleva(cart: Path, indirizzi: list, commit: str, tentativi: int = 3) -> Repo:
    """Scarica UN commit (per SHA) dal primo indirizzo che risponde e controlla che sia
    proprio quello chiesto: lo SHA identifica il contenuto, qualunque sia la fonte."""
    if not re.fullmatch(r"[0-9a-f]{40}", commit or ""):
        raise RuntimeError(f"commit non valido: {commit!r} (serve lo SHA completo)")
    if cart.exists() and git(cart, "cat-file", "-e", f"{commit}^{{commit}}", binario=True,
                             controlla=False).returncode == 0:
        return Repo(cart, commit)
    cart.mkdir(parents=True, exist_ok=True)
    git(None, "init", "-q", "--bare", str(cart))
    (cart / "info").mkdir(exist_ok=True)
    scrivi_testo(cart / "info" / "attributes", "* -export-ignore -export-subst\n")
    errori = []
    for url in [u for u in indirizzi if u]:
        for n in range(tentativi):
            r = git(cart, "fetch", "-q", "--depth=1", "--no-tags", url, commit, binario=True,
                    controlla=False)
            if r.returncode == 0:
                ottenuto = git(cart, "rev-parse", "FETCH_HEAD^{commit}").strip()
                if ottenuto != commit:
                    raise RuntimeError(f"{url}: atteso {commit}, ottenuto {ottenuto}")
                git(cart, "update-ref", "refs/eclipsis/sorgente", commit)
                return Repo(cart, commit)
            errori.append(f"{url}: {r.stderr.decode('utf-8', 'replace').strip()[:300]}")
            time.sleep(5 * (n + 1))
    raise RuntimeError(f"impossibile scaricare {commit}:\n  " + "\n  ".join(errori))


def variabili_script(testo: str) -> dict:
    return dict(re.findall(r'^(SCRIPT_[A-Z0-9_]+)="([^"]*)"', testo, re.M))


def intestazione(testo: str) -> str:
    """Il primo commento che parla di copyright o licenza (blocco C o righe ; // #)."""
    parole = re.compile(r"Copyright|\(C\)|©|Permission|public domain|licen[cs]e", re.I)
    for m in re.finditer(r"/\*.*?\*/", testo[:30000], re.S):
        if parole.search(m.group(0)):
            return m.group(0)
    blocco = []
    for riga in testo.splitlines()[:300] + [""]:
        s = riga.strip()
        if s.startswith((";", "//")) or (s.startswith("#") and not re.match(
                r"#\s*(include|if|ifn?def|define|endif|else|elif|pragma|undef|error)\b", s)):
            blocco.append(riga)
            continue
        if blocco and parole.search("\n".join(blocco)):
            return "\n".join(blocco)
        blocco = []
    raise RuntimeError("nessuna intestazione di licenza trovata")


def righe_copyright(repo: Repo, prefissi: list) -> list:
    righe = set()
    for f in repo.file():
        if not f.startswith(tuple(prefissi)) or not f.endswith((".c", ".h", ".in")):
            continue
        for r in repo.leggi(f)[:6000].splitlines():
            m = re.search(r"Copyright\s*(\(C\)|©|\d).*", r, re.I)
            if m:
                righe.add(re.sub(r"\s+", " ", m.group(0).rstrip(" */")).strip())
    return sorted(righe)


# --------------------------------------------------------------------------------------
# avvisi dei file di FFmpeg con licenze diverse dalla LGPL (AVVISI_FFMPEG.txt)
# --------------------------------------------------------------------------------------
MARCATORI_AVVISI = [
    # le condizioni IJG valgono anche per il codice derivato ("based upon the IJG ... DCT")
    ("IJG", r"Independent JPEG Group|\bIJG\b"),
    ("BSD", r"Redistribution and use in source and binary forms|\bBSD[- ](style )?licen[cs]ed?|\(new\) BSD"),
    # (la Boost dice "to any person or organization": non e' la MIT)
    ("MIT", r"Permission is hereby granted,? free of charge,? to any person obtaining|"
            r"\bMIT[- ]licen[cs]ed?|\bExpat licen"),
    ("ISC", r"Permission to use, copy, modify,? (and/or |and )?distribute"),
    ("Zlib", r"provided ['\"]as-is['\"]"),
    ("Boost", r"Boost Software License"),
    ("WTFPL", r"\bWTFPL\b|DO WHAT THE FUCK"),
    ("Apache", r"Apache License"),
    ("SoX", r"freely redistributable"),
    ("PD", r"public domain|unencumbered software"),
]


def _normalizza(testo: str) -> str:
    return re.sub(r"\s+", " ", " ".join(re.sub(r"^[\s*/#;!]+", "", r).strip() for r in testo.splitlines()))


def blocchi_commento(testo: str, asm: bool):
    """[(prima riga, ultima riga, testo)] dei commenti: /* ... */ e righe // (C), righe ; (asm)."""
    righe = testo.splitlines()
    blocchi, i = [], 0
    while i < len(righe):
        r = righe[i].strip()
        if asm and r.startswith(";"):
            j = i
            while j + 1 < len(righe) and righe[j + 1].strip().startswith(";"):
                j += 1
            blocchi.append((i + 1, j + 1, "\n".join(righe[i:j + 1])))
            i = j + 1
            continue
        if not asm and r.startswith("//"):
            j = i
            while j + 1 < len(righe) and righe[j + 1].strip().startswith("//"):
                j += 1
            blocchi.append((i + 1, j + 1, "\n".join(righe[i:j + 1])))
            i = j + 1
            continue
        if not asm and "/*" in righe[i]:
            j = i
            while j < len(righe) and "*/" not in righe[j][(righe[j].find("/*") + 2) if j == i else 0:]:
                j += 1
            j = min(j, len(righe) - 1)
            blocchi.append((i + 1, j + 1, "\n".join(righe[i:j + 1])))
            i = j + 1
            continue
        i += 1
    return blocchi


def avvisi_ffmpeg(leggi, compilati: list) -> list:
    """Per ogni file compilato con una licenza diversa dalla LGPL (o in aggiunta): (file, tipi,
    [blocchi di commento con licenza e copyright, testuali])."""
    trovati = []
    for f in compilati:
        testo = leggi(f)
        if testo is None:
            continue
        blocchi = blocchi_commento(testo, f.endswith((".asm", ".inc")))
        scelti, tipi = [], []
        for i, (a, b, t) in enumerate(blocchi):
            norm = _normalizza(t)
            qui = [n for n, r in MARCATORI_AVVISI if re.search(r, norm, re.I)]
            if qui:
                scelti.append((a, b, t))
                tipi += [x for x in qui if x not in tipi]
        if not scelti:
            continue
        primo = blocchi[0]
        if primo not in scelti and re.search(r"copyright", primo[2], re.I):
            scelti.insert(0, primo)        # l'intestazione col copyright del file
        trovati.append((f, tipi, scelti))
    return trovati


def testo_avvisi_ffmpeg(trovati: list, versione: str, commit: str, origine_elenco: str,
                        release: str) -> str:
    ijg = [f for f, tipi, _ in trovati if "IJG" in tipi]
    righe = ["AVVISI DEI FILE DI FFmpeg CON LICENZE DIVERSE DALLA LGPL",
             "Notices of the FFmpeg files under licenses other than the LGPL", "",
             f"FFmpeg {versione} (commit {commit}) in ffmpeg.exe, ffprobe.exe e ffplay.exe"
             + (f" della release {release} di eclipsis-ffmpeg." if release else "."), "",
             "FFmpeg nel suo insieme è distribuito con la LGPL versione 3 o successiva (ffmpeg.txt).",
             "Alcuni dei file compilati nei programmi hanno anche, o soltanto, un'altra licenza (BSD,",
             "MIT, ISC, IJG, Zlib, Boost, pubblico dominio...): qui sotto, per ognuno, i commenti con",
             "la licenza e il copyright, copiati dal sorgente senza modifiche. Sono solo i file",
             f"davvero compilati con la nostra configurazione ({origine_elenco}).", "",
             "FFmpeg as a whole is distributed under the LGPL version 3 or later. Some files compiled",
             "into the programs also, or only, carry another license: below are their license and",
             "copyright comments, copied verbatim from the source. Only the files actually compiled",
             "with our configuration are listed."]
    if ijg:
        righe += ["", "Independent JPEG Group",
                  "  This software is based in part on the work of the Independent JPEG Group.",
                  "  (Questo software si basa in parte sul lavoro dell'Independent JPEG Group.)",
                  f"  File: {', '.join(ijg)}.",
                  "  Le modifiche di FFmpeg a questi file rispetto agli originali di libjpeg sono nella",
                  "  cronologia di FFmpeg (https://github.com/FFmpeg/FFmpeg, git log -- <file>). /",
                  "  FFmpeg's changes to these files are recorded in FFmpeg's history."]
    righe += ["", f"Indice / Index ({len(trovati)} file)"]
    righe += [f"  {'+'.join(tipi):24s} {f}" for f, tipi, _ in trovati]
    for f, tipi, scelti in trovati:
        righe += ["", LINEA, f"{f}   [{'+'.join(tipi)}]", LINEA]
        for a, b, t in scelti:
            righe += [f"(righe {a}-{b})", t.rstrip("\n")]
    return "\n".join(righe) + "\n"


def lettore_sorgente(percorso: Path, commit):
    """Legge un file del sorgente di FFmpeg: da una cartella (l'albero della compilazione) o da
    un repository git nudo al commit della build."""
    if percorso is None:
        raise RuntimeError("--ffmpeg-compilati richiede --sorgente-ffmpeg")
    percorso = Path(percorso)
    if (percorso / "configure").is_file():
        def leggi(f):
            p = percorso / f
            return p.read_text(encoding="utf-8", errors="replace") if p.is_file() else None
        return leggi
    if not commit:
        raise RuntimeError("sorgente di FFmpeg da un repository git: serve --commit-ffmpeg")
    processo = subprocess.Popen(["git", "-C", str(percorso), "cat-file", "--batch"], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE)

    def leggi_git(f):
        processo.stdin.write(f"{commit}:{f}\n".encode("utf-8"))
        processo.stdin.flush()
        testa = processo.stdout.readline().split()
        if len(testa) < 3 or testa[1] != b"blob":
            return None
        dati = processo.stdout.read(int(testa[2]))
        processo.stdout.read(1)
        return dati.decode("utf-8", "replace")
    return leggi_git


def leggi_elenco(percorso: Path) -> list:
    return [r.strip() for r in percorso.read_text(encoding="utf-8").splitlines()
            if r.strip() and not r.startswith("#")]


def testo_licenza(voce: dict, versione: str, commit, origine: str, eseguibili: list,
                  blocchi: list, copyright=()) -> str:
    righe = [f"{voce['nome']} {versione}".rstrip(), f"Origine / Source: {origine}"]
    if commit:
        righe.append(f"Commit: {commit}")
    righe += [f"Licenza / License (SPDX): {voce['licenza']}",
              f"Negli eseguibili / In: {', '.join(eseguibili)}"]
    if voce.get("avvisi"):
        righe += [""] + list(voce["avvisi"])
    if copyright:
        righe += ["", "Righe di copyright dei file sorgente / Copyright lines from the source files:"]
        righe += [f"  {c}" for c in copyright]
    for titolo, contenuto in blocchi:
        righe += ["", LINEA, titolo, LINEA, contenuto.rstrip("\n")]
    return "\n".join(righe) + "\n"


# --------------------------------------------------------------------------------------
# controlli
# --------------------------------------------------------------------------------------
def leggi_versioni_immagine(percorso: Path) -> dict:
    v = {}
    for riga in percorso.read_text(encoding="utf-8").splitlines():
        if "=" in riga and not riga.startswith("#"):
            k, _, val = riga.partition("=")
            v[k.strip()] = val.strip()
    return v


def controlla_immagine(info: dict, versioni: dict, gcc_versione: str) -> list:
    errori = []
    for voce in RICETTA:
        atteso = info[voce["id"]]["versione"].split(" ")[0]
        trovato = versioni.get(voce["immagine"])
        if not trovato:
            errori.append(f"{voce['nome']}: versione non letta nell'immagine ({voce['immagine']})")
        elif trovato != atteso:
            errori.append(f"{voce['nome']}: nell'immagine {trovato}, nel sorgente {atteso}")
    if versioni.get("gcc") != gcc_versione:
        errori.append(f"GCC: nell'immagine {versioni.get('gcc')}, in ct-ng-config {gcc_versione}")
    return errori


def controlla_eseguibili(eseguibili: dict, info: dict, gcc_versione: str) -> tuple:
    """Versioni e commit che le librerie lasciano negli eseguibili devono essere quelli del
    sorgente raccolto; le librerie escluse non devono comparire. Restituisce (errori, ok)."""
    errori, conferme = [], []
    mancanti = [n for n in TUTTI if n not in eseguibili]
    if mancanti:
        return [f"mancano {', '.join(mancanti)} fra gli eseguibili da controllare"], []
    d = eseguibili["ffmpeg.exe"].read_bytes()
    nul = lambda s: b"\x00" + s.encode() + b"\x00"

    def confronta(nome, trovato, atteso, come):
        if trovato is None:
            errori.append(f"{nome}: {come} non trovata in ffmpeg.exe")
        elif trovato != atteso:
            errori.append(f"{nome}: in ffmpeg.exe {trovato}, nel sorgente {atteso}")
        else:
            conferme.append(f"{nome}: {come} = {trovato}")

    m = re.search(rb"Shaper: FriBidi ([0-9.]+) ", d)
    confronta("FriBidi", m and m.group(1).decode(), info["fribidi"]["versione"], "versione (libass)")
    # zlibVersion() restituisce ZLIB_VERSION (con l'eventuale suffisso, es. "-motley"), la
    # riga del copyright di deflate.c solo i numeri
    v = info["zlib"]["versione"]
    m = re.search(rb" deflate ([0-9.]+) Copyright", d)
    if nul(v) in d and m and v.startswith(m.group(1).decode()):
        conferme.append(f"zlib: zlibVersion() = {v}")
    else:
        errori.append(f"zlib: versione {v} non trovata in ffmpeg.exe")
    m = re.search(rb"commit: ([0-9a-f]{40})\x00", d)
    if m:
        confronta("libass", m.group(1).decode(), info["libass"]["commit"], "commit")
    else:
        m = re.search(rb"tarball: ([0-9.]+)\x00", d)
        confronta("libass", m and m.group(1).decode(), info["libass"]["versione"], "versione")
    c, v = info["dav1d"]["commit"], info["dav1d"]["versione"]
    trovati = [c[:n] for n in range(7, 13) if nul(c[:n]) in d] + ([v] if nul(v) in d else [])
    if trovati:
        conferme.append(f"dav1d: dav1d_version() = {trovati[0]}")
    else:
        errori.append(f"dav1d: ne' il commit {c[:7]} ne' la versione {v} in ffmpeg.exe")
    v = info["harfbuzz"]["versione"]
    if nul(v) in d:
        conferme.append(f"HarfBuzz: hb_version_string() = {v}")
    else:
        errori.append(f"HarfBuzz: versione {v} non trovata in ffmpeg.exe")
    p = [int(x) for x in re.findall(r"\d+", info["libxml2"]["versione"])[:3]]
    lx = f"{p[0]}{p[1]:02d}{p[2]:02d}"
    if nul(lx) in d:
        conferme.append(f"libxml2: LIBXML_VERSION_STRING = {lx}")
    else:
        errori.append(f"libxml2: {lx} non trovato in ffmpeg.exe")
    m = re.search(rb"GCC: \(([^)]*)\) ([0-9.]+)", d)
    confronta("GCC", m and m.group(2).decode(), gcc_versione, "versione")
    for nome_exe in TUTTI:
        dd = eseguibili[nome_exe].read_bytes()
        sdl = re.search(rb"SDL-[0-9.]+-g([0-9a-f]{7,40})", dd)
        src = re.search(rb"libsamplerate-([0-9.]+) \(c\)", dd)
        if nome_exe != "ffplay.exe":
            if sdl or src:
                errori.append(f"SDL 2 o libsamplerate anche in {nome_exe}: aggiornare "
                              f"gli eseguibili delle due voci")
            continue
        if not sdl:
            errori.append("SDL 2: revisione non trovata in ffplay.exe")
        elif not info["sdl2"]["commit"].startswith(sdl.group(1).decode()):
            errori.append(f"SDL 2: in ffplay.exe {sdl.group(1).decode()}, "
                          f"nel sorgente {info['sdl2']['commit'][:12]}")
        else:
            conferme.append(f"SDL 2: revisione in ffplay.exe = {sdl.group(0).decode()}")
        if not src:
            errori.append("libsamplerate: non trovata in ffplay.exe (SDL compilata senza?)")
        elif src.group(1).decode() != info["libsamplerate"]["versione"]:
            errori.append(f"libsamplerate: in ffplay.exe {src.group(1).decode()}, "
                          f"nel sorgente {info['libsamplerate']['versione']}")
        else:
            conferme.append(f"libsamplerate: src_get_version() = {src.group(1).decode()}")
    for nome, firma in [("libpng", rb"libpng version"), ("brotli", rb"timedownlifeleftback"),
                        ("libgomp", rb"OMP_NUM_THREADS"), ("bzip2", rb"bzip2/libbzip2"),
                        ("mingw-std-threads", rb"mingw_stdthread")]:
        for nome_exe, percorso in eseguibili.items():
            if firma in percorso.read_bytes():
                errori.append(f"{nome} e' dentro {nome_exe} ma non e' nell'elenco delle librerie")
    return errori, conferme


# --------------------------------------------------------------------------------------
# testi
# --------------------------------------------------------------------------------------
LEGGIMI = """\
{titolo}
{sottolinea}

Questo archivio contiene il codice sorgente delle librerie collegate in modo statico
dentro ffmpeg.exe, ffprobe.exe e ffplay.exe della release {release} di
https://github.com/Sebiscapin/eclipsis-ffmpeg, esattamente ai commit da cui sono state
compilate, e gli script con cui sono state compilate. Il sorgente di FFmpeg stesso è
nella stessa release (ffmpeg-sorgente.tar.gz).

This archive contains the source code of the libraries statically linked into
ffmpeg.exe, ffprobe.exe and ffplay.exe of release {release} of
https://github.com/Sebiscapin/eclipsis-ffmpeg, at the exact commits they were built
from, plus the scripts that built them. FFmpeg's own source is in the same release
(ffmpeg-sorgente.tar.gz).

Contenuto / Contents
--------------------
- sorgenti/<libreria>-<commit>.tar.xz: ogni libreria al commit esatto (git archive,
  senza la cronologia). Le parti aggiunte dalla compilazione (sottomoduli, gnulib) sono
  in archivi a parte con il percorso giusto: estraendo tutto nella stessa cartella si
  ottengono gli alberi usati per compilare.
- script/FFmpeg-Builds-<commit>.tar.xz: gli script che hanno compilato le librerie
  (scripts.d), la configurazione del compilatore (images/base-win64/ct-ng-config) e
  dell'immagine Docker.
- script/eclipsis-ffmpeg-<commit>.tar.xz: il repository eclipsis-ffmpeg al commit della
  release (compila.sh, configurazione.txt, i decoder aac_mf e h264_mf).
- licenze/: la licenza di ogni componente, con gli avvisi; licenze/librerie.json.
- MANIFEST.json: componenti, versioni, commit, licenze e impronte SHA-256 degli archivi.
- SHA256SUMS.txt: impronte di tutti i file.

Componenti / Components
-----------------------
{elenco}

Come sono stati compilati / How they were built
-----------------------------------------------
- Immagine Docker: {immagine}
  costruita da FFmpeg-Builds al commit {ffmpeg_builds} (win64, lgpl, 8.1).{nota_immagine}
- Compilatore: GCC {gcc} (crosstool-NG), binutils {binutils}; intestazioni, runtime e
  winpthreads di mingw-w64 dal commit fissato in scripts.d/10-mingw.sh; configurazione in
  images/base-win64/ct-ng-config.
- Ogni libreria è compilata dal suo script in scripts.d (ffbuild_dockerbuild) a partire
  dal sorgente scaricato al commit fissato, senza modifiche. Per libiconv lo script
  scarica anche gnulib (SCRIPT_COMMIT2), per zimg il sottomodulo graphengine, per
  FreeType autogen.sh scarica il sottomodulo dlg.
- Poi compila.sh di eclipsis-ffmpeg compila FFmpeg dentro la stessa immagine con le
  opzioni di configurazione.txt e collega tutto in modo statico.

Per ricompilare: con FFmpeg-Builds a quel commit, "./makeimage.sh win64 lgpl 8.1" ricrea
l'immagine (gli script scaricano da soli le stesse versioni; questi archivi ne sono la
copia, se le fonti originali cambiano o spariscono); poi compila.sh con
IMMAGINE=<immagine creata>. Per usare una libreria modificata (per esempio FriBidi) basta
ricompilarla nell'immagine e rilanciare compila.sh: i programmi si ricollegano da soli.

To rebuild: with FFmpeg-Builds at that commit, "./makeimage.sh win64 lgpl 8.1" recreates
the image (its scripts fetch the same versions; these archives are a copy of them), then
run compila.sh with IMMAGINE=<that image>. To use a modified library (e.g. FriBidi),
rebuild it in the image and run compila.sh again: the programs are relinked.

Le librerie di supporto di GCC (libgcc, libstdc++) sono coperte dalla GCC Runtime Library
Exception; il loro sorgente è su https://gcc.gnu.org (GCC {gcc}, tag releases/gcc-{gcc}).

Impronte SHA-256 degli eseguibili / SHA-256 of the executables
-------------------------------------------------------------
{impronte}
"""


def scrivi_licenze(a, uscita: Path, info: dict, repos: dict, gcc: dict, eseguibili: dict) -> list:
    pref = "" if a.percorso_testo in ("", ".") else a.percorso_testo.strip("/") + "/"
    testi = uscita / pref if pref else uscita
    testi.mkdir(parents=True, exist_ok=True)
    elenco = []

    # FFmpeg
    versione_ff = a.versione_ffmpeg
    if not versione_ff and "ffmpeg.exe" in eseguibili:
        m = re.search(rb"FFmpeg version ([^\x00\s]+)", eseguibili["ffmpeg.exe"].read_bytes())
        versione_ff = m.group(1).decode() if m else ""
    commit_ff = a.commit_ffmpeg.read_text(encoding="utf-8").strip() if a.commit_ffmpeg else None
    blocchi = [("COPYING.LGPLv3", (a.repo_eclipsis / "COPYING.LGPLv3").read_text(encoding="utf-8")),
               ("COPYING.GPLv3", (a.repo_eclipsis / "COPYING.GPLv3").read_text(encoding="utf-8"))]
    if a.modifiche:
        blocchi.insert(0, ("MODIFICHE_ECLIPSIS.txt", a.modifiche.read_text(encoding="utf-8")))
    ff = dict(nome="FFmpeg (libavcodec, libavformat, libavfilter, libavutil, libswscale, "
                   "libswresample, libavdevice)", licenza="LGPL-3.0-or-later",
              avvisi=["Ramo release/8.1 con i decoder aac_mf e h264_mf di Eclipsis (vedi",
                      "MODIFICHE_ECLIPSIS.txt). Configurato con --enable-version3: LGPL",
                      "versione 3 o successiva, che rimanda alla GPL versione 3. Il sorgente",
                      "corrispondente è ffmpeg-sorgente.tar.gz della stessa release."])
    scrivi_testo(testi / "ffmpeg.txt", testo_licenza(
        ff, versione_ff or "", commit_ff, "https://github.com/FFmpeg/FFmpeg.git", TUTTI, blocchi))
    voce_ff = dict(id="ffmpeg", nome=ff["nome"], versione=versione_ff or None, commit=commit_ff,
                   origine="https://github.com/FFmpeg/FFmpeg.git", licenza=ff["licenza"],
                   testo=f"{pref}ffmpeg.txt", sorgente_obbligatorio=True,
                   motivo_sorgente="obbligatorio: licenza di FFmpeg (LGPL-3.0-or-later)",
                   eseguibili=TUTTI,
                   nota="commit del ramo release/8.1 a cui compila.sh aggiunge i decoder "
                        "aac_mf e h264_mf; sorgente in ffmpeg-sorgente.tar.gz della stessa release")
    if a.ffmpeg_compilati:
        # gli avvisi dei file di FFmpeg compilati con licenze diverse dalla LGPL
        leggi = lettore_sorgente(a.sorgente_ffmpeg, commit_ff)
        compilati = leggi_elenco(a.ffmpeg_compilati)
        trovati = avvisi_ffmpeg(leggi, compilati)
        if not trovati:
            raise RuntimeError("nessun avviso trovato nei file di FFmpeg compilati: elenco o sorgente sbagliati?")
        scrivi_testo(testi / "AVVISI_FFMPEG.txt", testo_avvisi_ffmpeg(
            trovati, versione_ff or "", commit_ff or "", a.origine_elenco or a.ffmpeg_compilati.name,
            a.release))
        voce_ff["avvisi"] = f"{pref}AVVISI_FFMPEG.txt"
        voce_ff["avvisi_file"] = len(trovati)
        tipi = sorted({t for _, ts, _ in trovati for t in ts})
        voce_ff["avvisi_licenze"] = tipi
        scrivi(f"avvisi di FFmpeg: {len(trovati)} file su {len(compilati)} compilati ({', '.join(tipi)})")
    elenco.append(voce_ff)

    for voce in RICETTA:
        i, repo = info[voce["id"]], repos[voce["id"]]
        blocchi = [(f, repo.leggi(f)) for f in voce.get("file", [])]
        for parte in i["parti"]:
            for f in voce.get("parti_file", {}).get(parte["nome"], []):
                blocchi.append((f"{parte['percorso']}/{f}", parte["repo"].leggi(f)))
        blocchi += [(f"{f} (intestazione / header)", intestazione(repo.leggi(f)))
                    for f in voce.get("intestazioni", [])]
        cr = righe_copyright(repo, voce["copyright_da"]) if voce.get("copyright_da") else ()
        if voce.get("avvisi"):
            voce = dict(voce, avvisi=[r.replace("{anno}", i["data"][:4]) for r in voce["avvisi"]])
        eseg = voce.get("eseguibili", TUTTI)
        scrivi_testo(testi / f"{voce['id']}.txt", testo_licenza(
            voce, i["versione"], i["commit"], i["origine"], eseg, blocchi, cr))
        elenco.append(dict(
            id=voce["id"], nome=voce["nome"], versione=i["versione"], commit=i["commit"],
            origine=i["origine"], licenza=voce["licenza"], testo=f"{pref}{voce['id']}.txt",
            sorgente_obbligatorio=voce["obbligo"] != "sistema",
            motivo_sorgente=MOTIVI[voce["obbligo"]], eseguibili=eseg, nota=voce["nota"],
            data_commit=i["data"], script_ffmpeg_builds=f"scripts.d/{i['script']}",
            parti=[dict(nome=p["nome"], percorso=p["percorso"], commit=p["commit"],
                        origine=p["origine"], nota=p.get("nota", "")) for p in i["parti"]]))

    fissi = a.repo_eclipsis / "testi_licenze"
    gpl3 = (a.repo_eclipsis / "COPYING.GPLv3").read_text(encoding="utf-8")
    scrivi_testo(testi / "gcc-runtime.txt", testo_licenza(
        GCC, gcc["versione"], gcc["commit"], "https://gcc.gnu.org/git/gcc.git", TUTTI,
        [("COPYING.RUNTIME", (fissi / "COPYING.RUNTIME").read_text(encoding="utf-8")),
         ("COPYING3", gpl3)]))
    elenco.append(dict(id=GCC["id"], nome=GCC["nome"], versione=gcc["versione"],
                       commit=gcc["commit"], origine="https://gcc.gnu.org/git/gcc.git",
                       licenza=GCC["licenza"], testo=f"{pref}gcc-runtime.txt",
                       sorgente_obbligatorio=False,
                       motivo_sorgente="non obbligatorio: libreria di sistema del compilatore "
                                       "(GPL-3.0 sez. 1) e GCC Runtime Library Exception 3.1",
                       eseguibili=TUTTI,
                       nota=f"tag releases/gcc-{gcc['versione']}; compilatore crosstool-NG con "
                            f"binutils {gcc['binutils']}; sorgente su https://gcc.gnu.org"))
    scrivi_testo(testi / "unicode.txt", testo_licenza(
        UNICODE, "", None, "https://www.unicode.org/ucd/", TUTTI,
        [("Unicode License V3", (fissi / "unicode-license-v3.txt").read_text(encoding="utf-8"))]))
    elenco.append(dict(id=UNICODE["id"], nome=UNICODE["nome"], versione=None, commit=None,
                       origine="https://www.unicode.org/ucd/", licenza=UNICODE["licenza"],
                       testo=f"{pref}unicode.txt", sorgente_obbligatorio=False,
                       motivo_sorgente="non obbligatorio a parte: i dati sono già nel sorgente "
                                       "delle librerie",
                       eseguibili=TUTTI,
                       nota="dati compilati dentro FriBidi, HarfBuzz, libunibreak, libxml2 e "
                            "Fontconfig"))
    for v in elenco:
        v["release"] = a.release or None
    scrivi_testo(uscita / "librerie.json", json.dumps(elenco, indent=2, ensure_ascii=False) + "\n")

    anno_ft = info["freetype"]["data"][:4]
    righe = [f"Licenze dei componenti di ffmpeg.exe, ffprobe.exe e ffplay.exe {a.release}".rstrip(),
             "Licenses of the components of ffmpeg.exe, ffprobe.exe and ffplay.exe", "",
             "Il sorgente di FFmpeg e di ogni libreria è pubblicato insieme agli eseguibili, nella",
             "stessa release (ffmpeg-sorgente.tar.gz e librerie-sorgente.tar):",
             "https://github.com/Sebiscapin/eclipsis-ffmpeg/releases", ""]
    righe += [f"- {v['nome']}{' ' + v['versione'] if v.get('versione') else ''}: {v['licenza']}"
              f"\n    {v['testo']}" + (f"\n    {v['avvisi']} (file di FFmpeg con licenze diverse dalla LGPL)"
                                       if v.get("avvisi") else "") for v in elenco]
    righe += ["", f"Portions of this software are copyright © {anno_ft} The FreeType Project",
              "(https://freetype.org).  All rights reserved."]
    if any("IJG" in v.get("avvisi_licenze", []) for v in elenco):
        righe += ["", "This software is based in part on the work of the Independent JPEG Group."]
    righe += [""]
    scrivi_testo(uscita / "LEGGIMI.txt", "\n".join(righe) + "\n")
    return elenco


def scrivi_archivio(a, lavoro: Path, uscita: Path, fb: Repo, info: dict, repos: dict,
                    gcc: dict, eseguibili: dict):
    nome_rad = "librerie-sorgente" + (f"-{a.release}" if a.release else "")
    tmp = lavoro / "archivio"
    if tmp.exists():
        shutil.rmtree(tmp)
    rad = tmp / nome_rad
    (rad / "sorgenti").mkdir(parents=True)
    (rad / "script").mkdir()
    manifest = dict(release=a.release or None, immagine=a.immagine or None,
                    ffmpeg_builds=dict(commit=a.ffmpeg_builds, origine=FFMPEG_BUILDS_REPO[0]),
                    compilatore=dict(gcc=gcc["versione"], gcc_commit=gcc["commit"],
                                     binutils=gcc["binutils"]),
                    componenti=[], script=[])
    for voce in RICETTA:
        if voce.get("stesso_sorgente"):
            continue
        i, repo = info[voce["id"]], repos[voce["id"]]
        cart = nome_cartella(voce.get("cartella", voce["id"]), i["commit"])
        scrivi(f"  archivio {cart}.tar.xz")
        repo.archivia(cart, rad / "sorgenti" / f"{cart}.tar.xz")
        archivi = [f"{cart}.tar.xz"]
        for parte in i["parti"]:
            nome = f"{cart}+{parte['nome']}-{parte['commit'][:12]}.tar.xz"
            parte["repo"].archivia(f"{cart}/{parte['percorso']}", rad / "sorgenti" / nome)
            archivi.append(nome)
        manifest["componenti"].append(dict(
            id=voce["id"], nome=voce["nome"], versione=i["versione"], commit=i["commit"],
            origine=i["origine"], licenza=voce["licenza"], archivi=archivi,
            parti=[dict(nome=p["nome"], percorso=p["percorso"], commit=p["commit"],
                        origine=p["origine"], nota=p.get("nota", "")) for p in i["parti"]]))
    fb_cart = nome_cartella("FFmpeg-Builds", a.ffmpeg_builds)
    fb.archivia(fb_cart, rad / "script" / f"{fb_cart}.tar.xz")
    manifest["script"].append(dict(nome="FFmpeg-Builds", commit=a.ffmpeg_builds,
                                   archivio=f"script/{fb_cart}.tar.xz", licenza="MIT"))
    rif = git(a.repo_eclipsis, "rev-parse", f"{a.rif_eclipsis}^{{commit}}").strip()
    ec_cart = nome_cartella("eclipsis-ffmpeg", rif)
    r = git(a.repo_eclipsis, "archive", "--format=tar", f"--prefix={ec_cart}/", rif, binario=True)
    with lzma.open(rad / "script" / f"{ec_cart}.tar.xz", "wb", preset=6) as f:
        f.write(r.stdout)
    manifest["script"].append(dict(nome="eclipsis-ffmpeg", commit=rif,
                                   archivio=f"script/{ec_cart}.tar.xz",
                                   licenza="LGPL-3.0-or-later"))
    shutil.copytree(uscita, rad / "licenze")
    impronte = "\n".join(f"{sha256(p)}  {n}" for n, p in sorted(eseguibili.items())) or "-"
    elenco = "\n".join(
        f"- {c['nome']} {c['versione']}: {c['licenza']}\n  {c['origine']}\n  commit {c['commit']}"
        + "".join(f"\n  + {p['percorso']}: {p['origine']} @ {p['commit']}" for p in c["parti"])
        for c in manifest["componenti"])
    titolo = f"SORGENTE CORRISPONDENTE DELLE LIBRERIE DI FFmpeg PER ECLIPSIS {a.release}".rstrip()
    scrivi_testo(rad / "LEGGIMI.txt", LEGGIMI.format(
        titolo=titolo, sottolinea="=" * len(titolo), release=a.release or "-", elenco=elenco,
        immagine=a.immagine or "-", nota_immagine=(" " + a.nota_immagine) if a.nota_immagine else "",
        ffmpeg_builds=a.ffmpeg_builds, gcc=gcc["versione"], binutils=gcc["binutils"],
        impronte=impronte))
    for c in manifest["componenti"]:
        c["sha256"] = {n: sha256(rad / "sorgenti" / n) for n in c["archivi"]}
    for s in manifest["script"]:
        s["sha256"] = sha256(rad / s["archivio"])
    manifest["eseguibili"] = {n: sha256(p) for n, p in sorted(eseguibili.items())}
    scrivi_testo(rad / "MANIFEST.json", json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    tutti = sorted(p for p in rad.rglob("*") if p.is_file())
    scrivi_testo(rad / "SHA256SUMS.txt",
                 "".join(f"{sha256(p)}  {p.relative_to(rad).as_posix()}\n" for p in tutti))

    # tar non compresso (le parti sono gia' .xz), riproducibile: ordine, date, proprietari fissi
    data = int(_dt.datetime.fromisoformat(fb.data()).timestamp())
    a.archivio.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(a.archivio, "w", format=tarfile.PAX_FORMAT) as t:
        for p in [rad] + sorted(rad.rglob("*")):
            ti = t.gettarinfo(str(p), arcname=p.relative_to(tmp).as_posix())
            ti.mtime, ti.uid, ti.gid, ti.uname, ti.gname = data, 0, 0, "", ""
            ti.mode = 0o755 if p.is_dir() else 0o644
            if p.is_file():
                with open(p, "rb") as f:
                    t.addfile(ti, f)
            else:
                t.addfile(ti)
    scrivi(f"archivio: {a.archivio} ({a.archivio.stat().st_size / 2**20:.1f} MB) "
           f"sha256 {sha256(a.archivio)}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ffmpeg-builds", required=True, help="commit di FFmpeg-Builds (SHA completo)")
    ap.add_argument("--lavoro", required=True, type=Path, help="cartella per i download")
    ap.add_argument("--licenze", required=True, type=Path,
                    help="dove scrivere licenze, LEGGIMI.txt e librerie.json")
    ap.add_argument("--percorso-testo", default=".",
                    help="sottocartella dei testi rispetto a librerie.json (. = la stessa)")
    ap.add_argument("--archivio", type=Path, help="librerie-sorgente.tar da creare")
    ap.add_argument("--eseguibili", nargs="*", type=Path, default=[])
    ap.add_argument("--commit-ffmpeg", type=Path, help="commit_ffmpeg.txt della build")
    ap.add_argument("--versione-ffmpeg", help="se manca, si legge in ffmpeg.exe")
    ap.add_argument("--modifiche", type=Path, help="MODIFICHE_ECLIPSIS.txt della build")
    ap.add_argument("--ffmpeg-compilati", type=Path,
                    help="elenco dei file del sorgente di FFmpeg compilati (uno per riga): con "
                         "--sorgente-ffmpeg scrive AVVISI_FFMPEG.txt")
    ap.add_argument("--sorgente-ffmpeg", type=Path,
                    help="albero di FFmpeg della compilazione, o repository git nudo (al commit di "
                         "--commit-ffmpeg)")
    ap.add_argument("--origine-elenco", default="",
                    help="come e' stato ricavato l'elenco dei file compilati (frase per AVVISI_FFMPEG.txt)")
    ap.add_argument("--immagine", default="", help="immagine Docker usata (con @sha256:)")
    ap.add_argument("--nota-immagine", default="", help="frase dopo l'immagine nel LEGGIMI")
    ap.add_argument("--versioni-immagine", type=Path, help="versioni_immagine.txt di compila.sh")
    ap.add_argument("--release", default="", help="nome della release (tag)")
    ap.add_argument("--repo-eclipsis", type=Path, default=QUI, help="questo repository")
    ap.add_argument("--rif-eclipsis", default="HEAD", help="commit o tag di questo repository")
    a = ap.parse_args()

    lavoro = a.lavoro.resolve()
    lavoro.mkdir(parents=True, exist_ok=True)
    git_dir = lavoro / "git"
    eseguibili = {p.name.lower(): p for p in a.eseguibili}
    for p in eseguibili.values():
        if not p.is_file():
            sys.exit(f"eseguibile mancante: {p}")
    uscita = a.licenze.resolve()
    if uscita.exists() and any(uscita.iterdir()) and not (uscita / "librerie.json").exists():
        sys.exit(f"{uscita} non e' vuota e non contiene librerie.json: non la svuoto")

    # 1) FFmpeg-Builds e versioni del compilatore
    scrivi(f"FFmpeg-Builds @ {a.ffmpeg_builds}")
    fb = preleva(git_dir / "FFmpeg-Builds.git", FFMPEG_BUILDS_REPO, a.ffmpeg_builds)
    ctng = fb.leggi("images/base-win64/ct-ng-config")
    gcc = dict(versione=re.search(r'^CT_GCC_VERSION="([^"]+)"', ctng, re.M).group(1),
               binutils=re.search(r'^CT_BINUTILS_VERSION="([^"]+)"', ctng, re.M).group(1),
               commit=None)
    tag = f"refs/tags/releases/gcc-{gcc['versione']}"
    for url in GCC_REPO:
        r = git(None, "ls-remote", url, f"{tag}*", controlla=False, binario=True, timeout=300)
        righe = [x.split() for x in r.stdout.decode().splitlines() if x.strip()]
        trovati = {nome: sha for sha, nome in righe}
        gcc["commit"] = trovati.get(f"{tag}^{{}}") or trovati.get(tag)
        if gcc["commit"]:
            break

    # 2) ogni libreria al commit fissato dal suo script
    info, repos = {}, {}
    for voce in RICETTA:
        var = variabili_script(fb.leggi(f"scripts.d/{voce['script']}"))
        commit = var["SCRIPT_COMMIT"]
        indirizzi = [var.get("SCRIPT_REPO"), var.get("SCRIPT_MIRROR")]
        if voce["script"] == "10-mingw.sh":
            indirizzi.append("https://github.com/mingw-w64/mingw-w64.git")
        scrivi(f"- {voce['nome']}: {var.get('SCRIPT_REPO')} @ {commit}")
        repo = preleva(git_dir / f"{voce.get('stesso_sorgente', voce['id'])}.git", indirizzi, commit)
        repos[voce["id"]] = repo
        parti = []
        for parte in voce.get("parti", []):
            if parte.get("sottomodulo"):
                p_commit, p_url = repo.gitlink(parte["percorso"])
                p_indirizzi = [p_url]
            else:
                p_commit, p_url = var[parte["commit"]], var[parte["var"]]
                p_indirizzi = [p_url, var.get(parte.get("mirror", ""))]
            scrivi(f"    + {parte['nome']}: {p_url} @ {p_commit}")
            p_repo = preleva(git_dir / f"{voce['id']}+{parte['nome']}.git", p_indirizzi, p_commit)
            parti.append(dict(parte, commit=p_commit, origine=p_url, repo=p_repo))
        info[voce["id"]] = dict(versione=voce["versione"](repo.leggi), commit=commit,
                                origine=var.get("SCRIPT_REPO"), data=repo.data(), parti=parti,
                                script=voce["script"])
        scrivi(f"    versione {info[voce['id']]['versione']}")

    # 3) controlli: immagine ed eseguibili
    errori = []
    if a.versioni_immagine:
        e = controlla_immagine(info, leggi_versioni_immagine(a.versioni_immagine), gcc["versione"])
        errori += e
        if not e:
            scrivi("immagine: le versioni coincidono con quelle del sorgente")
    if eseguibili:
        e, conferme = controlla_eseguibili(eseguibili, info, gcc["versione"])
        errori += e
        for c in conferme:
            scrivi(f"  ok {c}")
    if errori:
        for e in errori:
            scrivi(f"::error title=librerie::{e}")
        sys.exit("Le librerie non corrispondono all'immagine o agli eseguibili: IMMAGINE e "
                 "FFMPEG_BUILDS_COMMIT si aggiornano insieme (vedi immagine.txt).")

    # 4) licenze
    if uscita.exists():
        shutil.rmtree(uscita)
    elenco = scrivi_licenze(a, uscita, info, repos, gcc, eseguibili)
    scrivi(f"licenze: {uscita} ({len(elenco)} componenti)")

    # 5) archivio del sorgente
    if a.archivio:
        scrivi_archivio(a, lavoro, uscita, fb, info, repos, gcc, eseguibili)


if __name__ == "__main__":
    main()
