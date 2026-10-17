#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""flex_ingesta: extrae SOLO la lista blanca, rechaza BAM/symlink/`..`, detecta hash erróneo y aparta.

Tar.gz SINTÉTICO (cero dato de la paciente) con 5 miembros: uno en lista blanca (`MP/outs/config.csv`),
un BAM, un symlink, una ruta con `..` y un hardlink. Transporte simulado (`--desde-fichero`, sin red).
La zona clínica es un `_PRIVADO_CLINICO/` temporal (familia A de `zonas_clinicas`).

Es un test de MURO: comprueba que no se escribe fuera de zona clínica, que no corre sin la ventanilla,
que stdout no lleva CONTENIDO (solo rutas/tamaños/hashes/booleanos), que el fichero nace en 0600 y
que con un hash equivocado lo extraído acaba en `_RETIRADOS/` y el proceso falla en rojo.
"""
import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(RAIZ, "tools", "flex_ingesta.py")
LECTOR = os.path.join(RAIZ, "tools", "lector_clinico.py")
SECRETO = b"gene_a,cell_1,42\nSECRETO-DE-CONTENIDO\n"

fallos = []
total = [0]


def check(cond, desc):
    total[0] += 1
    print("  %s %s" % ("✅" if cond else "❌", desc))
    if not cond:
        fallos.append(desc)


def tar_sintetico(ruta):
    with tarfile.open(ruta, "w:gz") as t:
        def add_bytes(nombre, datos, **kw):
            ti = tarfile.TarInfo(nombre)
            ti.size = len(datos)
            for k, v in kw.items():
                setattr(ti, k, v)
            t.addfile(ti, io.BytesIO(datos))
        d = tarfile.TarInfo("MP/outs"); d.type = tarfile.DIRTYPE; t.addfile(d)
        add_bytes("MP/outs/config.csv", SECRETO)
        add_bytes("MP/outs/possorted_genome_bam.bam", b"BAM\x01" * 100)
        s = tarfile.TarInfo("MP/outs/enlace.csv"); s.type = tarfile.SYMTYPE; s.linkname = "/etc/passwd"; t.addfile(s)
        add_bytes("MP/../fuera.csv", b"fuera\n")
        h = tarfile.TarInfo("MP/outs/config2.csv"); h.type = tarfile.LNKTYPE; h.linkname = "MP/outs/config.csv"; t.addfile(h)
    with open(ruta, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest(), os.path.getsize(ruta)


def corre(args, ventanilla=True, python=sys.executable):
    env = dict(os.environ)
    env.pop("BTP_VENTANILLA", None)
    if ventanilla:
        env["BTP_VENTANILLA"] = "1"
    p = subprocess.run([python, TOOL] + args, capture_output=True, timeout=120, env=env)
    return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")


def main():
    tmp = tempfile.mkdtemp(prefix="flex-test-")
    try:
        tar = os.path.join(tmp, "sintetico.tar.gz")
        sha, nbytes = tar_sintetico(tar)
        zona = os.path.join(tmp, "_PRIVADO_CLINICO", "flex")
        comunes = ["--file-id", "FAKE", "--bytes", str(nbytes), "--desde-fichero", tar, "--min-libre-gb", "0.001"]

        print("1) sin ventanilla no corre")
        rc, out, err = corre(comunes + ["--sha256", sha, "--destino-base", zona], ventanilla=False)
        check(rc == 2 and "RECHAZADO" in err and not os.path.exists(zona), "rechaza sin BTP_VENTANILLA=1 y no escribe")

        print("2) destino fuera de zona clínica → fail-closed")
        fuera = os.path.join(tmp, "normal", "flex")
        rc, out, err = corre(comunes + ["--sha256", sha, "--destino-base", fuera])
        check(rc == 1 and "zona clínica" in err and not os.path.exists(fuera), "no escribe fuera de zona clínica")

        print("3) hash correcto: extrae solo la lista blanca")
        rc, out, err = corre(comunes + ["--sha256", sha, "--destino-base", zona])
        destino = os.path.join(zona, sha[:12])
        check(rc == 0, "rc 0 (stderr: %s)" % err[-300:].replace("\n", " | "))
        cfg = os.path.join(destino, "MP", "outs", "config.csv")
        check(os.path.isfile(cfg) and open(cfg, "rb").read() == SECRETO, "config.csv extraído byte a byte")
        check(stat.S_IMODE(os.stat(cfg).st_mode) == 0o600, "fichero extraído en 0600")
        man = json.load(open(os.path.join(destino, "MANIFIESTO.json")))
        check(man["n_extraidos"] == 1 and man["extraidos"][0]["ruta"] == "MP/outs/config.csv", "manifiesto: 1 extraído")
        check(man["extraidos"][0]["sha256"] == hashlib.sha256(SECRETO).hexdigest(), "manifiesto: sha256 del miembro")
        motivos = sorted(s["motivo"] for s in man["saltados"])
        check(motivos == ["denegado", "hardlink", "ruta-con-..", "symlink"], "saltados: BAM, hardlink, .., symlink (%s)" % motivos)
        check(man["tar"]["coincide"] is True and man["tar"]["sha256_obtenido"] == sha, "manifiesto: sha256 del tar coincide")
        nada_mas = not any(os.path.exists(os.path.join(destino, p)) for p in
                           ("MP/outs/possorted_genome_bam.bam", "MP/outs/enlace.csv", "MP/outs/config2.csv"))
        check(nada_mas and not os.path.exists(os.path.join(zona, "fuera.csv")) and not os.path.exists(os.path.join(tmp, "_PRIVADO_CLINICO", "fuera.csv")),
              "ni BAM, ni symlink, ni hardlink, ni `..` en disco")
        check("SECRETO-DE-CONTENIDO" not in out and "SECRETO-DE-CONTENIDO" not in err, "stdout/stderr sin contenido del miembro")
        check("EXTRAIDO\tMP/outs/config.csv\t%d\t" % len(SECRETO) in out and "SHA256_COINCIDE\tTrue" in out, "stdout: ruta, tamaño, booleano")

        print("4) segunda pasada al mismo destino → no sobrescribe")
        rc, out, err = corre(comunes + ["--sha256", sha, "--destino-base", zona])
        check(rc == 1 and "ya existe" in err, "rechaza destino existente")

        print("5) hash erróneo → aparta a _RETIRADOS y rojo")
        malo = "0" * 63 + "1"
        rc, out, err = corre(comunes + ["--sha256", malo, "--destino-base", zona])
        check(rc == 1 and "SHA256_COINCIDE\tFalse" in out, "rc 1 y SHA256_COINCIDE False")
        check(not os.path.exists(os.path.join(zona, malo[:12])), "el destino no queda en su sitio")
        ret = os.path.join(zona, "_RETIRADOS")
        retirados = [d for d in os.listdir(ret) if d.startswith(malo[:12]) and not d.endswith(".txt")] if os.path.isdir(ret) else []
        check(len(retirados) == 1 and os.path.isfile(os.path.join(ret, retirados[0], "MANIFIESTO-FALLIDO.json")),
              "lo extraído está en _RETIRADOS/ con MANIFIESTO-FALLIDO.json (no se borró)")
        check(any(f.endswith(".retirada.txt") for f in os.listdir(ret)), "nota .retirada.txt con el motivo")

        print("6) registrado en la ventanilla como procesador")
        sys.path.insert(0, os.path.join(RAIZ, "tools"))
        import lector_clinico
        check("flex_ingesta" in lector_clinico.FLEX and "flex_ingesta" in lector_clinico.PROCESADORES
              and "flex_ingesta" not in lector_clinico.LAMINILLAS, "FLEX registrado aparte de LAMINILLAS")
        rel, interp = lector_clinico.PROCESADORES["flex_ingesta"]
        check(rel == "tools/flex_ingesta.py", "apunta a tools/flex_ingesta.py")
        if os.path.isfile(interp):
            zona2 = os.path.join(tmp, "_PRIVADO_CLINICO", "flex2")
            env = dict(os.environ, BTP_REPO=tmp, BTP_AGENT="test-flex")
            p = subprocess.run([sys.executable, LECTOR, "procesa", "flex_ingesta", "--"] + comunes +
                               ["--sha256", sha, "--destino-base", zona2], capture_output=True, timeout=120, env=env)
            check(p.returncode == 0 and os.path.isfile(os.path.join(zona2, sha[:12], "MP", "outs", "config.csv")),
                  "por la ventanilla (`procesa flex_ingesta`) extrae igual")
            log = os.path.join(tmp, ".claude", "logs", "clinico-access.log")
            check(os.path.isfile(log) and "PROCESA-flex_ingesta" in open(log).read(), "deja su línea en el log de accesos")
        else:
            print("  ⚠️  intérprete %s ausente en esta máquina: se salta la pasada por la ventanilla" % interp)

        print("7) --solo-cloupe: solo los .cloupe, planos, a cloupe_10x/ y sin tocar el destino existente")
        CL = "MP/SC_MULTI_CS/SANITIZE_MAP_CALLS/fork0/files/"
        tar2 = os.path.join(tmp, "cloupe.tar.gz")
        contenido_cl = b"CLOUPE-SINTETICO-ANOTACION\n"
        CH = "MP/SC_MULTI_CS/SANITIZE_MAP_CALLS/fork0/chnk0/files/"   # forma REAL del tar de 10x
        with tarfile.open(tar2, "w:gz") as t:
            for nombre, datos in ((CH + "X_M1_cell_annotation_sample_cloupe.cloupe", contenido_cl),
                                  (CL + "otro.cloupe", b"no"), ("MP/outs/config.csv", SECRETO),
                                  ("MP/outs/x_cell_annotation_sample_cloupe.cloupe", b"fuera de ruta")):
                ti = tarfile.TarInfo(nombre); ti.size = len(datos); t.addfile(ti, io.BytesIO(datos))
            h = tarfile.TarInfo(CL + "X_M1_cell_annotation_sample_cloupe.cloupe")   # hardlink al regular
            h.type = tarfile.LNKTYPE; h.linkname = CH + "X_M1_cell_annotation_sample_cloupe.cloupe"
            t.addfile(h)
        sha2 = hashlib.sha256(open(tar2, "rb").read()).hexdigest()
        n2 = os.path.getsize(tar2)
        zona3 = os.path.join(tmp, "_PRIVADO_CLINICO", "flex3")
        c2 = ["--file-id", "FAKE", "--bytes", str(n2), "--desde-fichero", tar2, "--min-libre-gb", "0.001",
              "--destino-base", zona3]
        rc, out, err = corre(c2 + ["--sha256", sha2])                      # modo normal: .cloupe denegado
        d_norm = os.path.join(zona3, sha2[:12])
        check(rc == 0 and not any(f.endswith(".cloupe") for _, _, fs in os.walk(d_norm) for f in fs),
              "modo normal NO extrae ningún .cloupe (canario: la denegación sigue en pie)")
        rc, out, err = corre(c2 + ["--sha256", sha2, "--solo-cloupe"])
        cd = os.path.join(zona3, "cloupe_10x")
        f1 = os.path.join(cd, "X_M1_cell_annotation_sample_cloupe.cloupe")
        check(rc == 0 and os.path.isfile(f1) and open(f1, "rb").read() == contenido_cl, "extrae el .cloupe de la ruta exacta (plano)")
        check(sorted(os.listdir(cd)) == sorted(["X_M1_cell_annotation_sample_cloupe.cloupe", "MANIFIESTO.json", "INGESTA.log"]),
              "en cloupe_10x/ solo el .cloupe + manifiesto + log (ni otro.cloupe, ni config.csv, ni el de fuera de ruta)")
        check(stat.S_IMODE(os.stat(f1).st_mode) == 0o600, ".cloupe en 0600")
        check(not os.path.exists(os.path.join(d_norm, "MP", "SC_MULTI_CS")), "el destino <sha12>/ de los ciegos no recibe nada")
        check("ANOTACION" not in out + err, "stdout/stderr sin contenido")
        rc, out, err = corre(c2 + ["--sha256", sha2, "--solo-cloupe"])
        check(rc == 1 and "ya existe" in err, "segunda pasada: no sobrescribe cloupe_10x/")
        rc, out, err = corre(c2 + ["--sha256", sha2, "--solo-cloupe", "--retira-vacio"])
        check(rc == 1 and "ya existe" in err and os.path.isfile(f1), "--retira-vacio NO aparta un cloupe_10x/ que ya tiene .cloupe")
        tar3 = os.path.join(tmp, "vacio.tar.gz")      # sin ningún .cloupe: la pasada debe ir en ROJO
        with tarfile.open(tar3, "w:gz") as t3:
            ti = tarfile.TarInfo("MP/outs/config.csv"); ti.size = 1; t3.addfile(ti, io.BytesIO(b"x"))
        sha3 = hashlib.sha256(open(tar3, "rb").read()).hexdigest()
        rc, out, err = corre(["--file-id", "FAKE", "--bytes", str(os.path.getsize(tar3)), "--desde-fichero", tar3,
                              "--min-libre-gb", "0.001", "--destino-base", os.path.join(tmp, "_PRIVADO_CLINICO", "flex4"),
                              "--sha256", sha3, "--solo-cloupe"])
        check(rc == 1 and "ningún miembro" in err, "solo-cloupe sin ningún .cloupe → rojo, no éxito vacío")
        z4 = os.path.join(tmp, "_PRIVADO_CLINICO", "flex4")
        os.makedirs(os.path.join(z4, "cloupe_10x"), exist_ok=True)       # resto vacío de una pasada fallida
        open(os.path.join(z4, "cloupe_10x", "MANIFIESTO.json"), "w").write("{}")
        rc, out, err = corre(["--file-id", "FAKE", "--bytes", str(n2), "--desde-fichero", tar2, "--min-libre-gb", "0.001",
                              "--destino-base", z4, "--sha256", sha2, "--solo-cloupe", "--retira-vacio"])
        check(rc == 0 and os.path.isfile(os.path.join(z4, "cloupe_10x", "X_M1_cell_annotation_sample_cloupe.cloupe"))
              and any(d.startswith("cloupe_10x-") for d in os.listdir(os.path.join(z4, "_RETIRADOS"))),
              "--retira-vacio aparta (no borra) el resto vacío y repite la extracción (%s)" % err[-300:].replace("\n", " | "))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n%d comprobaciones, %d fallos" % (total[0], len(fallos)))
    for f in fallos:
        print("  ❌ " + f)
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
