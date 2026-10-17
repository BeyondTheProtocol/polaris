#!/usr/bin/env python3
"""tools/flex_ingesta.py — ingesta segura del tar Flex (single-cell, DFCI) desde Drive, en un solo pase.

Procesador SANCIONADO de la ventanilla (`lector_clinico.py procesa flex_ingesta -- …`): exige
BTP_VENTANILLA=1 y solo escribe en zona clínica (`zonas_clinicas.es_ruta_clinica`, fail-closed).

Por qué un solo pase (16-oct-26, Fase 1 del plan Flex): el tar pesa 52 GB y el Mac tiene ~44 GB
libres. No cabe entero en disco, así que se baja en STREAMING: el flujo comprimido pasa por
hashlib.sha256 y, a la vez, por `tarfile` en modo `r|gz`. Solo se escriben a disco los miembros de
la LISTA BLANCA (matrices, métricas, config; nunca el BAM ni los .tgz internos). Al terminar se
compara el sha256 del flujo con el esperado: si no coincide, lo extraído se APARTA a `_RETIRADOS/`
(no se borra) y se falla en rojo.

Qué sale por stdout: rutas relativas, tamaños, sha256 y booleanos. NUNCA contenido de las matrices
ni recuentos de células/genes: el análisis posterior tiene que ser ciego al resultado.

Transporte: Drive por `Range` + token de la cuenta de servicio de `drive.py` (lectura). Si la red se
corta o el token caduca (1 h; la bajada dura horas) se reabre en el MISMO offset, así que el hash
y el tar siguen sin repetir ni perder un byte. Con `--desde-fichero` el transporte es un fichero
local (para el test, sin red).

Uso:
  python3 tools/lector_clinico.py procesa flex_ingesta -- \\
      --file-id <ID de Drive> --sha256 <hex> --bytes <N> [--destino-base ~/Clinico-PRIVADO/flex]
      [--min-libre-gb 15] [--desde-fichero <tar.gz local>]
"""
import argparse
import datetime
import fnmatch
import hashlib
import io
import json
import os
import shutil
import sys
import tarfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(HERE)
HOME = os.path.expanduser("~")
DESTINO_BASE = os.path.join(HOME, "Clinico-PRIVADO", "flex")
RETIRADOS = "_RETIRADOS"
CHUNK = 8 << 20            # 8 MiB por lectura de red
MAX_MIEMBRO = 8 << 30      # un solo miembro no puede pasar de 8 GiB (una matriz raw .h5 cabe de sobra)
REINTENTOS_RED = 30

# ---------------------------------------------------------------------------
# Lista blanca (relativa a la raíz del tar, sin `./`). Primero las denegaciones duras.
# ---------------------------------------------------------------------------
DENEGADOS = ("*.bam", "*.bam.bai", "*.bai", "*.cram", "*.fastq", "*.fastq.gz", "*.fq.gz",
             "*.mri.tgz", "*.tgz", "*.tar", "*.tar.gz", "*.cloupe", "*/_perf*", "*/_log*",
             "*_perf", "*_log", "*/_perf/*", "*/_log/*")
STRUCTIFY = "MP/SC_MULTI_CS/SC_MULTI_CORE/STRUCTIFY_PER_SAMPLE_OUTS/fork0/files/"
PERMITIDOS = (
    # procedencia del pipeline (texto diminuto: versión, línea de comandos, invocación)
    "MP/_cmdline", "MP/_versions", "MP/_invocation", "MP/_tags", "MP/_timestamp",
    # outs/ de cellranger multi
    "MP/outs/config.csv", "MP/outs/probe_set.csv", "MP/outs/qc_sample_metrics.csv",
    "MP/outs/*filtered_feature_bc_matrix.h5", "MP/outs/*raw_feature_bc_matrix.h5",
    "MP/outs/*/*filtered_feature_bc_matrix.h5", "MP/outs/*/*raw_feature_bc_matrix.h5",
    "MP/outs/*/*/*filtered_feature_bc_matrix.h5", "MP/outs/*/*/*raw_feature_bc_matrix.h5",
    "MP/outs/*/*/*raw_probe_bc_matrix.h5", "MP/outs/*/*/*/*filtered_feature_bc_matrix.h5",
    "MP/outs/per_sample_outs/*/metrics_summary.csv",
    "MP/outs/per_sample_outs/*/web_summary.html",
    "MP/outs/per_sample_outs/*/analysis/*", "MP/outs/per_sample_outs/*/*/analysis/*",
    "MP/outs/per_sample_outs/*/*/*feature_bc_matrix/*",
    "MP/outs/per_sample_outs/*/*/*.h5",
    # matrices por muestra en la fase STRUCTIFY
    STRUCTIFY + "*_filtered_feature_barcode_matrix/*",
    STRUCTIFY + "*_raw_feature_barcode_matrix.h5",
    STRUCTIFY + "*_filtered_barcodes.csv",
    STRUCTIFY + "*_per_barcode_metrics.csv",
    STRUCTIFY + "*_filtered_feature_bc_matrix.h5",
    STRUCTIFY + "*.h5",
)


# Modo --solo-cloupe (16-oct-26): los .cloupe de 10x para Loupe Browser. Llevan la ANOTACIÓN AUTOMÁTICA
# de 10x, que los analistas ciegos de `<sha12>/` no deben ver: por eso van a un destino NUEVO y aparte
# (`<base>/cloupe_10x/`, planos) y la lista blanca de este modo no admite NADA más.
CLOUPE_DESTINO = "cloupe_10x"
CLOUPE_PERMITIDOS = (
    # El fichero real vive en chnk0/files/; fork0/files/ es un hardlink (se salta como hardlink).
    "MP/SC_MULTI_CS/SANITIZE_MAP_CALLS/fork0/chnk0/files/*_cell_annotation_sample_cloupe.cloupe",
    "MP/SC_MULTI_CS/SANITIZE_MAP_CALLS/fork0/files/*_cell_annotation_sample_cloupe.cloupe",
)


def _casa(nombre, patrones):
    return any(fnmatch.fnmatchcase(nombre, p) for p in patrones)


def clasifica(miembro, solo_cloupe=False):
    """→ ("extrae", rel) | ("salta", motivo, rel). Fail-closed: cualquier duda es saltar.
    `solo_cloupe`: lista blanca alternativa (CLOUPE_PERMITIDOS) y sin las denegaciones de *.cloupe."""
    nombre = miembro.name
    rel = nombre[2:] if nombre.startswith("./") else nombre
    if not rel or rel.startswith("/") or os.path.isabs(rel):
        return ("salta", "ruta-absoluta", rel)
    partes = rel.split("/")
    if ".." in partes or "" in partes[:-1]:
        return ("salta", "ruta-con-..", rel)
    if miembro.issym() or miembro.islnk():
        return ("salta", "symlink" if miembro.issym() else "hardlink", rel)
    if miembro.isdir():
        return ("salta", "directorio", rel)
    if not miembro.isreg():
        return ("salta", "no-es-fichero-regular", rel)
    if solo_cloupe:
        if _casa(rel, tuple(d for d in DENEGADOS if d != "*.cloupe")):
            return ("salta", "denegado", rel)
        if not _casa(rel, CLOUPE_PERMITIDOS):
            return ("salta", "fuera-de-lista-blanca", rel)
        if miembro.size > MAX_MIEMBRO:
            return ("salta", "demasiado-grande", rel)
        return ("extrae", rel)
    if _casa(rel, DENEGADOS):
        return ("salta", "denegado", rel)
    if not _casa(rel, PERMITIDOS):
        return ("salta", "fuera-de-lista-blanca", rel)
    if miembro.size > MAX_MIEMBRO:
        return ("salta", "demasiado-grande", rel)
    return ("extrae", rel)


# ---------------------------------------------------------------------------
# Política de zona clínica (mismo predicado que el guard y la ventanilla)
# ---------------------------------------------------------------------------
def _politica():
    import importlib.util
    for ruta in (os.path.join(RAIZ, ".claude", "hooks", "zonas_clinicas.py"),
                 os.path.join(HOME, "claudecode", ".claude", "hooks", "zonas_clinicas.py")):
        if os.path.exists(ruta):
            spec = importlib.util.spec_from_file_location("zonas_clinicas", ruta)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    raise RuntimeError("no encuentro zonas_clinicas.py")


# ---------------------------------------------------------------------------
# Transporte
# ---------------------------------------------------------------------------
class LectorFichero:
    """Transporte simulado: un tar.gz local. Misma interfaz que LectorDrive."""
    def __init__(self, ruta):
        self.f = open(ruta, "rb")
        self.leidos = 0

    def read(self, n=-1):
        b = self.f.read(n if n is not None and n >= 0 else CHUNK)
        self.leidos += len(b)
        return b

    def close(self):
        self.f.close()


class LectorDrive:
    """Lee un fichero de Drive por `Range` con la cuenta de servicio de drive.py. Reabre en el
    mismo offset ante corte de red o token caducado. `read(n)` devuelve b"" solo al final real."""
    URL = "https://www.googleapis.com/drive/v3/files/%s?alt=media&supportsAllDrives=true"

    def __init__(self, file_id, total, log):
        sys.path.insert(0, HERE)
        import drive                                   # noqa: E402
        self.file_id, self.total, self.log = file_id, total, log
        self.leidos = 0
        self._resp = None
        self._creds = drive._sa_creds()
        self._abre()

    def _token(self):
        from google.auth.transport.requests import Request
        caduca = self._creds.expiry
        pronto = caduca is not None and (caduca - datetime.datetime.utcnow()).total_seconds() < 300
        if not self._creds.valid or pronto:
            self._creds.refresh(Request())
        return self._creds.token

    def _abre(self):
        if self._resp is not None:
            try:
                self._resp.close()
            except Exception:
                pass
        req = urllib.request.Request(self.URL % self.file_id, headers={
            "Authorization": "Bearer %s" % self._token(),
            "Range": "bytes=%d-" % self.leidos,
        })
        self._resp = urllib.request.urlopen(req, timeout=120)
        if self.leidos and self._resp.status != 206:
            raise IOError("Drive no respetó Range (status %s)" % self._resp.status)

    def read(self, n=-1):
        if n is None or n < 0:
            n = CHUNK
        if self.leidos >= self.total:
            return b""
        intentos = 0
        while True:
            try:
                b = self._resp.read(n)
                if not b and self.leidos < self.total:
                    raise IOError("flujo cerrado antes de tiempo en el byte %d" % self.leidos)
                self.leidos += len(b)
                return b
            except Exception as e:
                intentos += 1
                if intentos > REINTENTOS_RED:
                    raise
                espera = min(60, 2 ** min(intentos, 6))
                self.log("red: %s; reabro en offset %d en %ds (intento %d/%d)"
                         % (type(e).__name__, self.leidos, espera, intentos, REINTENTOS_RED))
                time.sleep(espera)
                try:
                    self._abre()
                except Exception as e2:
                    self.log("red: al reabrir, %s" % type(e2).__name__)

    def close(self):
        try:
            self._resp.close()
        except Exception:
            pass


class FlujoHash(io.RawIOBase):
    """Envuelve el transporte: todo byte leído por tarfile pasa antes por sha256. Lleva el ritmo."""
    def __init__(self, fuente, total, log):
        self.fuente, self.total, self.log = fuente, total, log
        self.h = hashlib.sha256()
        self.n = 0
        self.t0 = time.monotonic()
        self._ult_log = self.t0

    def readable(self):
        return True

    def readinto(self, b):
        datos = self.fuente.read(len(b))
        k = len(datos)
        b[:k] = datos
        self.h.update(datos)
        self.n += k
        ahora = time.monotonic()
        if ahora - self._ult_log >= 30:
            self._ult_log = ahora
            self.progreso()
        return k

    def progreso(self, final=False):
        t = max(time.monotonic() - self.t0, 1e-6)
        mbps = self.n / t / 1e6
        faltan = (self.total - self.n) / max(self.n / t, 1e-6) if self.total else 0
        self.log("progreso: %.1f%% · %.2f GB de %.2f · %.1f MB/s · faltan ~%d min%s"
                 % (100.0 * self.n / max(self.total, 1), self.n / 1e9, self.total / 1e9, mbps,
                    faltan / 60, " · FIN" if final else ""))

    def drena(self):
        """Lee hasta EOF lo que tarfile no consumió (relleno final, cola gzip): el hash es del
        fichero ENTERO, no de lo que el tar necesitó."""
        while True:
            b = self.fuente.read(CHUNK)
            if not b:
                break
            self.h.update(b)
            self.n += len(b)


# ---------------------------------------------------------------------------
# Ingesta
# ---------------------------------------------------------------------------
def _abre_0600(ruta):
    os.makedirs(os.path.dirname(ruta), mode=0o700, exist_ok=True)
    if os.path.lexists(ruta):
        raise FileExistsError(ruta)
    fd = os.open(ruta, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    return os.fdopen(fd, "wb")


def _retira(destino, motivo, log):
    """Aparta el destino entero a `<base>/_RETIRADOS/<nombre>-<ts>/` (sigue en zona clínica). No borra."""
    base = os.path.dirname(destino)
    ret_dir = os.path.join(base, RETIRADOS)
    os.makedirs(ret_dir, mode=0o700, exist_ok=True)
    sello = "%s-%s" % (os.path.basename(destino), time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()))
    nuevo, k = os.path.join(ret_dir, sello), 1
    while os.path.lexists(nuevo):          # dos retiradas en el mismo segundo no se pisan
        k += 1
        nuevo = os.path.join(ret_dir, "%s-%d" % (sello, k))
    os.rename(destino, nuevo)
    with open(nuevo + ".retirada.txt", "a", encoding="utf-8") as f:
        f.write("%s\tflex_ingesta\t%s\t%s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"),
                                                 motivo.replace("\n", " "), destino))
    log("RETIRADO a %s (%s)" % (nuevo, motivo))
    return nuevo


def ingesta(a):
    if os.environ.get("BTP_VENTANILLA") != "1":
        print("RECHAZADO: flex_ingesta solo corre por la ventanilla "
              "(python3 tools/lector_clinico.py procesa flex_ingesta -- …).", file=sys.stderr)
        return 2
    ZC = _politica()
    esperado = a.sha256.lower().strip()
    if len(esperado) != 64 or any(c not in "0123456789abcdef" for c in esperado):
        print("RECHAZADO: --sha256 no es un hex de 64.", file=sys.stderr)
        return 2
    base = os.path.realpath(os.path.expanduser(a.destino_base))
    solo_cloupe = bool(getattr(a, "solo_cloupe", False))
    destino = os.path.join(base, CLOUPE_DESTINO if solo_cloupe else esperado[:12])
    for r in (base, destino):
        if not ZC.es_ruta_clinica(r):
            print("RECHAZADO: %s no es zona clínica (fail-closed)." % r, file=sys.stderr)
            return 1
    if (solo_cloupe and getattr(a, "retira_vacio", False) and os.path.isdir(destino)
            and not any(f.endswith(".cloupe") for f in os.listdir(destino))):
        # restos de una pasada que no extrajo nada: se APARTAN (no se borran) para poder repetir
        _retira(destino, "pasada solo-cloupe previa sin ningún .cloupe", lambda m: print(m, file=sys.stderr))
    if os.path.lexists(destino):
        print("RECHAZADO: %s ya existe; esta ingesta no sobrescribe. Retíralo antes si procede."
              % destino, file=sys.stderr)
        return 1
    os.makedirs(destino, mode=0o700)
    ruta_log = os.path.join(destino, "INGESTA.log")
    log_f = _abre_0600(ruta_log)

    def log(msg):
        linea = "%s %s" % (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), msg)
        print(linea, file=sys.stderr, flush=True)
        log_f.write((linea + "\n").encode("utf-8"))
        log_f.flush()

    min_libre = int(a.min_libre_gb * 1e9)
    extraidos, saltados = [], []
    inicio = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    log("inicio · destino=%s · bytes esperados=%d · min libre=%g GB" % (destino, a.bytes, a.min_libre_gb))

    fuente = None
    fallo = None
    obtenido = None
    try:
        if a.desde_fichero:
            fuente = LectorFichero(a.desde_fichero)
            log("transporte: fichero local (simulado)")
        else:
            fuente = LectorDrive(a.file_id, a.bytes, log)
            log("transporte: Drive file_id=%s (cuenta de servicio, Range)" % a.file_id)
        flujo = FlujoHash(fuente, a.bytes, log)
        with tarfile.open(fileobj=io.BufferedReader(flujo, buffer_size=CHUNK), mode="r|gz") as tar:
            for m in tar:
                c = clasifica(m, solo_cloupe)
                if c[0] == "salta":
                    if c[1] != "directorio":
                        saltados.append({"ruta": c[2], "motivo": c[1], "bytes": m.size})
                        log("salta [%s] %s (%d B)" % (c[1], c[2], m.size))
                    continue
                rel = c[1]
                libre = shutil.disk_usage(destino).free
                if libre - m.size < min_libre:
                    raise RuntimeError("disco: libre %d B menos miembro %d B bajaría de %g GB"
                                       % (libre, m.size, a.min_libre_gb))
                src = tar.extractfile(m)
                h = hashlib.sha256()
                n = 0
                rel_out = os.path.basename(rel) if solo_cloupe else rel   # cloupe: planos
                with _abre_0600(os.path.join(destino, rel_out)) as out:
                    while True:
                        b = src.read(CHUNK)
                        if not b:
                            break
                        h.update(b)
                        n += len(b)
                        out.write(b)
                if n != m.size:
                    raise RuntimeError("miembro %s: %d bytes escritos, %d declarados" % (rel, n, m.size))
                extraidos.append({"ruta": rel, "bytes": n, "sha256": h.hexdigest(),
                                  **({"guardado_como": rel_out} if solo_cloupe else {})})
                log("extraído %s · %d B · %s" % (rel, n, h.hexdigest()))
        flujo.drena()
        flujo.progreso(final=True)
        obtenido = flujo.h.hexdigest()
        coincide = (obtenido == esperado) and (flujo.n == a.bytes)
        log("sha256 del flujo: %s · esperado: %s · coincide=%s · bytes=%d/%d"
            % (obtenido, esperado, coincide, flujo.n, a.bytes))
        if not coincide:
            fallo = "sha256 o tamaño no coinciden (obtenido %s, %d B)" % (obtenido, flujo.n)
        elif solo_cloupe and not extraidos:
            # una pasada de 52 GB que no extrae nada es un fallo, no un éxito vacío (16-oct-26)
            fallo = "solo-cloupe: ningún miembro casó con la lista blanca"
    except Exception as e:
        fallo = "%s: %s" % (type(e).__name__, str(e)[:300])
        log("FALLO %s" % fallo)
    finally:
        if fuente is not None:
            fuente.close()

    manifiesto = {
        "herramienta": "flex_ingesta", "version": 1, "modo": "solo_cloupe" if solo_cloupe else "lista_blanca",
        "fuente": {"drive_file_id": a.file_id, "desde_fichero": bool(a.desde_fichero)},
        "tar": {"sha256_esperado": esperado, "sha256_obtenido": obtenido, "bytes_esperados": a.bytes,
                "coincide": fallo is None},
        "inicio_utc": inicio, "fin_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "extraidos": extraidos, "saltados": saltados,
        "n_extraidos": len(extraidos), "n_saltados": len(saltados),
        "fallo": fallo,
    }
    nombre_man = "MANIFIESTO.json" if fallo is None else "MANIFIESTO-FALLIDO.json"
    with _abre_0600(os.path.join(destino, nombre_man)) as f:
        f.write(json.dumps(manifiesto, indent=1, ensure_ascii=False).encode("utf-8"))
    log_f.close()

    # stdout: solo rutas, tamaños, hashes y booleanos.
    for e in extraidos:
        print("EXTRAIDO\t%s\t%d\t%s" % (e["ruta"], e["bytes"], e["sha256"]))
    for s in saltados:
        print("SALTADO\t%s\t%s\t%d" % (s["motivo"], s["ruta"], s["bytes"]))
    print("SHA256_COINCIDE\t%s" % (fallo is None))
    print("LIBRE_GB\t%.1f" % (shutil.disk_usage(base).free / 1e9))
    if fallo is not None:
        nuevo = _retira(destino, fallo, lambda m: print(m, file=sys.stderr))
        print("RETIRADO\t%s" % os.path.relpath(nuevo, base))
        print("FALLO: %s" % fallo, file=sys.stderr)
        return 1
    print("DESTINO\t%s" % destino)
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--file-id", required=True)
    p.add_argument("--sha256", required=True)
    p.add_argument("--bytes", type=int, required=True)
    p.add_argument("--destino-base", default=DESTINO_BASE)
    p.add_argument("--min-libre-gb", type=float, default=15.0)
    p.add_argument("--solo-cloupe", action="store_true",
                   help="extrae SOLO los *_cell_annotation_sample_cloupe.cloupe a <base>/cloupe_10x/ (planos)")
    p.add_argument("--retira-vacio", action="store_true",
                   help="con --solo-cloupe: aparta a _RETIRADOS un cloupe_10x/ previo que NO contenga ningún .cloupe")
    p.add_argument("--desde-fichero", default=None, help="transporte simulado (test): un tar.gz local")
    a = p.parse_args(argv)
    return ingesta(a)


if __name__ == "__main__":
    sys.exit(main())
