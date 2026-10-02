#!/usr/bin/env python3
"""tools/laminillas_jaulas.py — perfiles `sandbox-exec` (SBPL) de las laminillas DFCI.

Plan «laminillas DFCI», F1-infra, «Jaulas». Se GENERAN en cada arranque de la ventanilla
(`lector_clinico.py procesa laminillas_*`), nunca se versionan: llevan las raíces clínicas del
overlay local (`zonas_clinicas.local.json`), que pueden contener nombre y fecha de nacimiento.
Por eso se escriben en `JAULAS` (zona clínica, 0700/0600) y no en el repo.

Orden dentro de cada perfil (en SBPL gana la ÚLTIMA regla que casa):
  1. base: `(allow default)` o `(deny default)` + lo mínimo enumerado en el plan;
  2. los EXTRA de quien llama (`extra_lectura`, `extra_codigo`): ANTES de cualquier deny, para que
     un extra no pueda reabrir zona clínica ni secretos que cuelguen de él. `extra_codigo` abre
     SOLO los `*.py` del primer nivel que git tiene versionados (`git ls-files`), uno a uno, y la
     carpeta para listarla: ni un `.py` sin versionar ni `__pycache__/` (2-oct-26);
  3. denies: familia B (`zonas_clinicas.raices()`, con realpath), familia A (las carpetas que
     descubre `lector_clinico._raices_para_listar()`, con realpath, más una regex de SEGMENTO por
     cada `NOMBRES_CUALQUIER_NIVEL` y `PREFIJOS_CUALQUIER_NIVEL`; `_privado_dms` queda exento
     porque no está en ninguna de las dos), el Llavero (mach-lookup a los servicios de
     `LLAVERO_MACH` —el de los ficheros, SecurityServer, y el de protección de datos, secd— y exec
     de /usr/bin/security), escritura y red según el perfil;
  4. los allow de SESION / ORIGEN / N1 de la tabla del plan. Sin ellos, ninguna jaula lee las
     láminas; antes de los deny, el deny los taparía (`orden_correcto` lo comprueba);
  5. y lo ÚLTIMO, tras `MARCA_FINAL`, los deny de SECRETOS (credenciales en el repo y en HOME, y
     las carpetas que se sincronizan con la nube: iCloud y CloudStorage; en las jaulas con red,
     además el estado vivo y los overlays de PII del repo). Nada va detrás: ni un allow del plan ni
     un extra puede reabrirlos (`secretos_al_final` lo comprueba). Hasta el 2-oct-26 iban en el
     paso 3 y el extra `tools/` de red-sin-zona reabría `tools/.x_secrets.json`.

Perfiles: red-sin-zona, analisis, analisis-ingesta, visor-clinico, visor-n1, exporta, vision.

Medido el 2-oct-26 (verificador independiente, 2.ª pasada; tests en
`tests/test_laminillas_jaulas_secretos.py`):
  · Llavero: Python por ctypes llegaba al Llavero de protección de datos (secd, SecItemCopyMatching
    con un servicio sintético: «no encontrado», -25300) desde las 5 jaulas `(allow default)`; solo
    SecurityServer estaba negado. Ahora `LLAVERO_MACH` lleva también los servicios de secd y los
    de su familia. Una COPIA de /usr/bin/security fuera de /usr/bin no llega a arrancar: macOS la
    mata por «Launch Constraint Violation» (SIGKILL), dentro y fuera de las jaulas; no es mérito
    de la jaula, y por eso el test mira también la vía ctypes, que sí depende de ella.
  · iCloud (`~/Library/Mobile Documents`) y `~/Library/CloudStorage` se listaban desde las 5
    jaulas `(allow default)`. Ninguna los necesita: van al bloque final.

LÍMITE DECLARADO (regla de convergencia: solo lo explota un proceso de FUERA de la jaula, con
permisos del usuario, que ya podría leer el secreto directamente):
  · `test_limite_declarado_enlace_duro_plantado`: SBPL compara RUTAS. Un enlace duro a un secreto o
    a zona clínica plantado desde fuera en una carpeta que la jaula lee (N1, VENVS, SESION) se lee
    desde las 7 jaulas por su ruta permitida. Desde DENTRO no se puede crear (`link` y `clonefile`
    dan EPERM en las cuatro jaulas que escriben).

Uso:  python3 tools/laminillas_jaulas.py mostrar <perfil>        # imprime el perfil (¡PII!)
      python3 tools/laminillas_jaulas.py comprobar                # orden y presencia, sin imprimir
"""
import os
import pwd
import subprocess
import sys

HOME = os.path.expanduser("~")
_AQUI = os.path.dirname(os.path.abspath(__file__))

# Rutas del plan. SESION y ORIGEN cuelgan de ~/Clinico-PRIVADO: son zona clínica (familia B) y
# por eso los píxeles N2 quedan bajo `clinico_guard` fuera de la jaula.
LAMINILLAS = os.path.join(HOME, "Clinico-PRIVADO", "laminillas-DFCI")
SESION = os.path.join(LAMINILLAS, "sesion")
ORIGEN = os.path.join(LAMINILLAS, "origen")
JAULAS = os.path.join(LAMINILLAS, "jaulas")
N1 = os.path.join(HOME, "Laminillas-N1")
VENVS = os.path.join(HOME, ".polaris-venvs")
CACHE = os.path.join(VENVS, "cache")          # pesos públicos: fuera de zona clínica
TMP_RED = os.path.join(VENVS, "tmp")          # TMPDIR propio de red-sin-zona
WSINFER_ZOO = os.path.join(HOME, ".wsinfer-zoo")

PERFILES = ("red-sin-zona", "analisis", "analisis-ingesta", "visor-clinico", "visor-n1",
            "exporta", "vision")

# Servicios del Llavero (plan: deny mach-lookup + deny exec de /usr/bin/security). SecurityServer
# es el de los llaveros en fichero (login.keychain-db: donde viven los `btp-*`). Desde el 2-oct-26,
# además, los de secd (Llavero de protección de datos, SecItem*: `com.apple.secd.plist`) y los de
# su familia (llavero del sistema, CryptoTokenKit, KeychainStasher, proxy de iCloud Keychain): con
# solo SecurityServer negado, Python por ctypes llegaba a secd desde las jaulas `(allow default)`.
# «com.apple.securityd» no lo publica hoy ningún plist; se queda (no cuesta nada y el test lo pide).
LLAVERO_MACH = ("com.apple.SecurityServer", "com.apple.securityd",
                "com.apple.securityd.xpc", "com.apple.securityd.general", "com.apple.securityd.ckks",
                "com.apple.securityd.sos", "com.apple.securityd.aps", "com.apple.security.octagon",
                "com.apple.security.kcsharing", "com.apple.security.escrow-update",
                "com.apple.securityd.systemkeychain", "com.apple.security.KeychainStasher",
                "com.apple.security.cloudkeychainproxy3", "com.apple.keychainsharingmessagingd",
                "com.apple.ctkd.token-client", "com.apple.ctkd.slot-client")
SECURITY_BIN = "/usr/bin/security"


def _zc():
    sys.path.insert(0, _AQUI)
    import lector_clinico               # trae `ZC` (zonas_clinicas) ya cargado, fail-closed
    if lector_clinico.ZC is None:
        raise RuntimeError("sin política de zonas clínicas: no genero jaulas")
    return lector_clinico


def _q(ruta):
    """Literal SBPL. Las comillas y barras invertidas no deberían aparecer en una ruta, pero si
    aparecen se escapan en vez de romper el perfil (o, peor, abrirlo)."""
    return '"%s"' % str(ruta).replace("\\", "\\\\").replace('"', '\\"')


def _variantes(ruta):
    """La ruta tal cual y su realpath (el kernel casa sobre la ruta resuelta: /tmp → /private/tmp,
    y un enlace a la carpeta clínica ES la carpeta clínica)."""
    r = os.path.normpath(os.path.expanduser(ruta))
    out = [r]
    try:
        real = os.path.realpath(r)
        if real not in out:
            out.append(real)
    except OSError:
        pass
    return out


# ── Regex de segmento, insensible a mayúsculas y a tildes ──────────────────────────────────────
# APFS es insensible a mayúsculas, y `zonas_clinicas.normaliza` quita tildes: `historial clinico`
# tiene que casar «Historial clínico» en NFC y en NFD. SBPL no tiene (?i): cada letra ASCII va como
# [xX]. Las letras que pueden llevar tilde (a e i o u n c) y cualquier carácter no ASCII casan 1, 2
# o 3 posiciones (ASCII; NFC de 2 bytes; NFD = letra + U+0301) por ALTERNANCIA de clases fijas.
# Tres trampas medidas el 1-oct-26 con `sandbox-exec`:
#   · alternancia exacta (a|á|Á|á…): el compilador revienta con nombres largos
#     (`INSTR_JUMP_NE_MAX_LENGTH`, rc -6) tras comerse 3 GB de RAM por perfil;
#   · `[^/]{1,3}`: COMPILA pero no casa nada (un deny que no deniega, sin error);
#   · `[^/][^/]?[^/]?` (opcionales encadenados): casa, pero con 8 nombres vuelve el rc -6; y
#     `([^/]|[^/][^/]|[^/][^/][^/])` no termina de compilar. Lo que compila en 0,02 s: la letra
#     en su clase [xX], o DOS posiciones cualesquiera (NFC), o la letra + DOS (NFD). El motor
#     compara bytes: «í» NFC son 2.
# Sobra-casar es el lado seguro: deniega de más, nunca de menos. El test lo comprueba con
# carpetas NFC y NFD de verdad.
_CON_TILDE = set("aeiounc")
_ESPECIALES = set(".^$*+?()[]{}|\\/\"")


def _patron_letra(c):
    if c.isascii() and c.isalpha():
        cl = "[%s%s]" % (c.lower(), c.upper())
        if c.lower() in _CON_TILDE:
            # ASCII | NFC (2 bytes) | NFD (letra + marca combinante de 2 bytes)
            return "(%s|[^/][^/]|%s[^/][^/])" % (cl, cl)
        return cl
    if c in _ESPECIALES:
        return "\\" + c
    if not c.isascii():
        return "([^/][^/]|[^/][^/][^/])"
    return c


def _solo_caso(c):
    if c.isascii() and c.isalpha():
        return "[%s%s]" % (c.lower(), c.upper())
    return "\\" + c if c in _ESPECIALES else c


def regex_segmento(nombre, prefijo=False):
    """Regex SBPL del segmento. Siempre termina en `[^/]*(/|$)`, también para nombres exactos:
    medido el 1-oct-26, el mismo patrón cerrado en `(/|$)` desborda el compilador («data object
    length 174733 exceeds maximum» o rc -6) y con `[^/]*` compila en 0,01 s. El precio es casar
    también `<nombre>loquesea`: deniega de más, que es el lado seguro (`_privado_dms` sigue
    fuera: ningún nombre de la lista es prefijo suyo; lo comprueba el test)."""
    cuerpo = "".join(_patron_letra(c) for c in nombre)
    return "/%s[^/]*(/|$)" % cuerpo


# ── Denies comunes ─────────────────────────────────────────────────────────────────────────────
def denies_clinicos(lc=None):
    """Líneas SBPL que niegan lectura y escritura de TODA zona clínica (familias A y B)."""
    lc = lc or _zc()
    zc = lc.ZC
    lineas = [";; ── familia B: raíces ancladas (zonas_clinicas.raices), con realpath"]
    vistas = []
    for raiz in zc.raices():
        for v in _variantes(raiz):
            if v not in vistas:
                vistas.append(v)
    # El repo en un worktree: <repo>/.claude/worktrees/<rama>/informes es la misma raíz.
    for raiz in vistas:
        lineas.append("(deny file-read* file-write* (subpath %s))" % _q(raiz))
    # Aquí el nombre es EXACTO (lo fija el repo): solo mayúsculas, sin la tolerancia a tildes, que
    # tras `[^/]+` hace que el compilador de sandbox no termine.
    for r in zc.RAICES_EN_REPO:
        lineas.append('(deny file-read* file-write* (regex #"/\\.claude/worktrees/[^/]+/%s(/|$)"))'
                      % "".join(_solo_caso(c) for c in r))
    lineas.append(";; ── familia A: carpetas descubiertas (_raices_para_listar), con realpath")
    try:
        descubiertas = lc._raices_para_listar()
    except Exception as e:                       # fail-closed: sin descubrimiento, no hay jaula
        raise RuntimeError("no pude descubrir las raíces clínicas (%r)" % e)
    for raiz in descubiertas:
        for v in _variantes(raiz):
            lineas.append("(deny file-read* file-write* (subpath %s))" % _q(v))
    lineas.append(";; ── familia A: regex de segmento (NOMBRES / PREFIJOS_CUALQUIER_NIVEL)")
    for n in sorted(zc.NOMBRES_CUALQUIER_NIVEL):
        if n in zc.NOMBRES_EXENTOS:
            continue
        lineas.append('(deny file-read* file-write* (regex #"%s"))' % regex_segmento(n))
    for p in zc.PREFIJOS_CUALQUIER_NIVEL:
        lineas.append('(deny file-read* file-write* (regex #"%s"))' % regex_segmento(p, True))
    return lineas


# ── Secretos: el bloque FINAL de todo perfil ───────────────────────────────────────────────────
MARCA_FINAL = (";; ── FINAL: secretos. Lo ÚLTIMO del perfil: ninguna regla detrás "
               "(en SBPL gana la última que casa)")

# Credenciales en fichero, en cualquier árbol del repo (casa base, worktrees, tests): un regex
# casa la ruta entera, así que no depende de dónde esté el checkout.
_REPO_SECRETOS = (
    "/tools/\\.[^/]*secrets[^/]*$",                                  # tools/.<x>_secrets.json
    "/tools/state/nube(/|$)",                                        # datos de cuenta (Scaleway)
    "/tools/state/[^/]+/(token|tokens\\.json|client\\.json)$",       # OAuth scite/consensus, borde
    "/tools/state/[^/]+/[^/]*([sS]ecret|[cC]redential)[^/]*$",
)
# Solo en las jaulas CON RED (red-sin-zona, vision): lo que el repo guarda de ella y que una jaula
# con salida a internet no debe ver nunca: el estado vivo entero (tablero, correo, mensajes,
# biomarcadores…), los logs de los daemons y los overlays de PII (`tools/*.local.json`). En las
# jaulas sin red no: exporta necesita los overlays de deid y el estado de la puerta de N1.
_REPO_PRIVADO_CON_RED = (
    "/tools/state(/|$)",
    "/tools/launchd(/|$)",
    "/tools/[^/]*\\.local\\.json$",
)
# Credenciales en HOME. Carpetas enteras (subpath) y ficheros sueltos (literal). Ninguna jaula las
# necesita: el único que lleva token (laminillas_pesos) lo recibe del padre por entorno, y el
# Llavero ya está cerrado por mach-lookup.
_HOME_CARPETAS = (".ssh", ".config/gh", ".config/1Password", "Library/Keychains",
                  ".cache/huggingface", ".claude", ".docker", ".aws", ".gnupg", ".kube",
                  ".config/gcloud", ".password-store")
_HOME_FICHEROS = (".config/gh-token.env", ".netrc", ".git-credentials", ".pypirc", ".npmrc",
                  ".claude.json")
# Lo que se sincroniza con la nube (2-oct-26): iCloud Drive y los proveedores de File Provider
# (Google Drive, OneDrive…). Se listaban desde las 5 jaulas `(allow default)`; ninguna los necesita.
_HOME_NUBE = ("Library/Mobile Documents", "Library/CloudStorage")


def _homes():
    """HOME del proceso y el de la cuenta (pwd): si la ventanilla corriera con otro HOME, las
    credenciales de verdad siguen negadas."""
    out = [HOME]
    try:
        out.append(pwd.getpwuid(os.getuid()).pw_dir)
    except (KeyError, OSError):
        pass
    return list(dict.fromkeys(os.path.normpath(h) for h in out))


def denies_secretos(con_red=False):
    """Bloque FINAL de todo perfil (va tras `MARCA_FINAL` y nada lo sigue). Credenciales en el
    repo y en HOME, el token de huggingface_hub (el de ~/.cache y el del HF_HOME de la ventanilla),
    las carpetas de la nube (`_HOME_NUBE`) y, con red, lo privado del repo (`_REPO_PRIVADO_CON_RED`)."""
    l = [MARCA_FINAL]
    for rx in _REPO_SECRETOS + (_REPO_PRIVADO_CON_RED if con_red else ()):
        l.append('(deny file-read* file-write* (regex #"%s"))' % rx)
    vistas = []
    for h in _homes():
        for rel in _HOME_CARPETAS + _HOME_NUBE:
            for v in _variantes(os.path.join(h, rel)):
                if ("subpath", v) not in vistas:
                    vistas.append(("subpath", v))
        for rel in _HOME_FICHEROS:
            for v in _variantes(os.path.join(h, rel)):
                if ("literal", v) not in vistas:
                    vistas.append(("literal", v))
    for rel in ("token", "stored_tokens"):                  # HF_HOME = CACHE/hf (ventanilla)
        for v in _variantes(os.path.join(CACHE, "hf", rel)):
            if ("literal", v) not in vistas:
                vistas.append(("literal", v))
    for tipo, v in vistas:
        l.append("(deny file-read* file-write* (%s %s))" % (tipo, _q(v)))
    return l


def denies_llavero():
    return [";; ── Llavero",
            "(deny mach-lookup %s)" % " ".join('(global-name "%s")' % s for s in LLAVERO_MACH),
            "(deny process-exec (literal %s))" % _q(SECURITY_BIN)]


def _sub(rutas, op="file-read*"):
    vs = []
    for r in rutas:
        for v in _variantes(r):
            if v not in vs:
                vs.append(v)
    return "(allow %s %s)" % (op, " ".join("(subpath %s)" % _q(v) for v in vs))


def py_versionados(d):
    """Nombres de los `*.py` del PRIMER nivel de `d` que git tiene versionados (`git ls-files`, el
    índice del checkout de `d`). Fail-closed: si `d` no está en un checkout de git o git falla,
    ninguno (la jaula no lee código y el script no arranca: se ve, no se cuela nada)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    try:
        r = subprocess.run(["git", "ls-files", "-z", "--", "*.py"], cwd=d, env=env,
                           capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return []
    if r.returncode != 0:
        return []
    nombres = r.stdout.decode("utf-8", "surrogateescape").split("\0")
    return sorted(n for n in nombres if n.endswith(".py") and "/" not in n)


def _sub_codigo(dirs):
    """Lectura SOLO del código Python VERSIONADO de cada carpeta: la carpeta (para listarla: el
    FileFinder de import lo hace) y, uno a uno por `literal`, sus `*.py` del primer nivel que git
    tiene versionados (`py_versionados`). Nada más de dentro: ni `state/`, ni overlays, ni logs, ni
    un `.py` sin versionar (un borrador suelto en `tools/` puede llevar cualquier cosa), ni
    `__pycache__/` (el `.pyc` de un módulo sin versionar lleva sus constantes; sin él, Python
    compila desde el fuente). La ruta va por la carpeta y por su realpath, nunca por el realpath
    del fichero: un `.py` que fuera un enlace a otro sitio no abre su destino. Es lo que necesita
    un script de `tools/` lanzado en una jaula con red (lee su propio fichero y, con
    `sys.path[0] = tools`, los módulos que se importen de ahí). Hasta el 2-oct-26 era una regex
    `tools/[^/]+\\.py` y `__pycache__/` entero: el verificador vio que un `.py` sin versionar de la
    casa base se leía desde la jaula con red."""
    partes = []
    for d in dirs:
        nombres = py_versionados(d)
        for v in _variantes(d):
            for x in ["(literal %s)" % _q(v)] + ["(literal %s)" % _q(os.path.join(v, n)) for n in nombres]:
                if x not in partes:
                    partes.append(x)
    return "(allow file-read* %s)" % " ".join(partes)


_DEV_ESCRITURA = ('(allow file-write* (literal "/dev/null") (literal "/dev/zero") '
                  '(literal "/dev/dtracehelper") (regex #"^/dev/tty") (regex #"^/dev/fd/"))')


def _base_deny_default():
    """red-sin-zona y vision: (deny default) + EXACTAMENTE lo enumerado en el plan."""
    return ["(version 1)", "(deny default)",
            "(allow process*)", "(allow sysctl-read)", "(allow signal)",
            "(allow file-read-metadata)", "(allow network*)",
            '(allow mach-lookup (global-name "com.apple.trustd.agent"))',
            ";; lectura de sistema",
            '(allow file-read* (literal "/") (subpath "/usr") (subpath "/System")'
            ' (subpath "/Library") (subpath "/private/etc") (subpath "/private/var/db")'
            ' (subpath "/dev") (subpath "/opt/homebrew"))']


CON_RED = ("red-sin-zona", "vision")


def _extras(extra_lectura, extra_codigo):
    """Allows de quien llama. Van ANTES de todo deny: un extra nunca reabre zona clínica ni
    secretos que cuelguen de él (el 2-oct-26 el `tools/` de la ventanilla reabría ambos)."""
    l = []
    if extra_lectura or extra_codigo:
        l.append(";; ── extras de quien llama (ANTES de los deny: no reabren nada)")
    if extra_lectura:
        l.append(_sub(extra_lectura))
    if extra_codigo:
        l.append(_sub_codigo(extra_codigo))
    return l


def perfil(nombre, *, sesion=None, origen=None, n1=None, tmpdir=None, lc=None,
           extra_lectura=(), extra_codigo=(), metal_cache=None):
    """Texto SBPL del perfil `nombre`. Las rutas se pueden inyectar (tests con canarios).
    `extra_lectura`: carpetas enteras de solo lectura; `extra_codigo`: solo el código Python de
    cada carpeta (`_sub_codigo`). Los dos van antes de los deny y no reabren nada."""
    sesion = sesion or SESION
    origen = origen or ORIGEN
    n1 = n1 or N1
    if nombre not in PERFILES:
        raise ValueError("perfil desconocido: %s" % nombre)
    clin = denies_clinicos(lc)
    llav = denies_llavero()
    final = denies_secretos(con_red=nombre in CON_RED)
    extras = _extras(extra_lectura, extra_codigo)
    if nombre in CON_RED:
        if nombre == "red-sin-zona":
            lect = [VENVS, WSINFER_ZOO]
            escr = [VENVS, WSINFER_ZOO] + ([tmpdir] if tmpdir else [])
        else:
            # vision (la llamada HTTP de vision_n1 y nube_n1): lee SOLO N1; el intérprete es el
            # de /opt/homebrew, que ya da la base. Sin VENVS (2-oct-26): ni pesos ni el TMPDIR de
            # red-sin-zona. No escribe nada; la clave llega por entorno.
            lect = [n1]
            escr = []
        cuerpo = _base_deny_default() + extras + clin + llav
        # Los allows del plan, tras los deny clínicos (SESION no aparece: en estas jaulas falla
        # todo dato) y ANTES del bloque final de secretos.
        cuerpo.append(";; ── allows del plan (tras los deny clínicos; los secretos van detrás)")
        cuerpo.append(_sub(lect))
        cuerpo.append(_DEV_ESCRITURA)
        if escr:
            cuerpo.append(_sub(escr, "file-write*"))
        return "\n".join(cuerpo + final) + "\n"
    l = ["(version 1)", "(allow default)"] + extras + clin + llav
    l.append(";; ── sin red")
    l.append("(deny network*)")
    l.append(";; ── escritura: nada salvo lo de abajo")
    l.append('(deny file-write* (subpath "/"))')
    # ── a partir de aquí, SOLO allows (el orden es la defensa) hasta el bloque final de secretos
    l.append(";; ── allows del plan (tras los deny clínicos; los secretos van detrás)")
    l.append(_DEV_ESCRITURA)
    if nombre.startswith("visor"):
        l.append('(allow network-bind network-inbound (local ip "localhost:*"))')
        l.append('(allow network-outbound (remote ip "localhost:*"))')
    if nombre in ("analisis", "analisis-ingesta"):
        l.append(_sub([sesion]))
        l.append(_sub([sesion] + ([tmpdir] if tmpdir else []), "file-write*"))
        if metal_cache:
            l.append(_sub([metal_cache], "file-write*"))
        if nombre == "analisis-ingesta":
            l.append(_sub([origen]))
            l.append(_sub([origen], "file-write*"))
    elif nombre == "visor-clinico":
        l.append(_sub([sesion]))
    elif nombre == "visor-n1":
        l.append(_sub([n1]))
    elif nombre == "exporta":
        l.append(_sub([sesion]))
        l.append(_sub([n1], "file-write*"))
    return "\n".join(l + final) + "\n"


def secretos_al_final(texto):
    """¿El bloque de secretos es lo ÚLTIMO del perfil? `MARCA_FINAL` una sola vez, al menos un deny
    detrás y, detrás, solo `(deny …)` o comentarios: ningún allow ni ninguna otra regla puede
    reabrirlos. Devuelve (bool, motivo)."""
    lineas = texto.splitlines()
    marcas = [i for i, ln in enumerate(lineas) if ln == MARCA_FINAL]
    if len(marcas) != 1:
        return False, "MARCA_FINAL aparece %d veces (tiene que ser 1)" % len(marcas)
    cola = [ln for ln in lineas[marcas[0] + 1:] if ln.strip() and not ln.startswith(";;")]
    if not cola:
        return False, "el bloque final de secretos está vacío"
    for ln in cola:
        if not ln.startswith("(deny file-read* file-write* "):
            return False, "regla tras el bloque de secretos: %s" % ln[:60]
    return True, "ok"


def orden_correcto(texto, rutas_permitidas):
    """¿Todo allow que nombra una ruta permitida (SESION, ORIGEN, N1) va DESPUÉS del último deny
    de fichero previo al bloque final de secretos? Devuelve (bool, motivo). Lo usa el test: un
    perfil con el allow antes del deny tiene que dar False. El bloque final (`MARCA_FINAL`) no
    cuenta: va detrás de todo allow a propósito, y `secretos_al_final` lo comprueba aparte."""
    lineas = texto.splitlines()
    if MARCA_FINAL in lineas:
        lineas = lineas[:lineas.index(MARCA_FINAL)]
    ult_deny = max((i for i, ln in enumerate(lineas) if ln.startswith("(deny file-")), default=-1)
    reales = set()
    for r in rutas_permitidas:
        reales.update(_variantes(r))
    for i, ln in enumerate(lineas):
        if ln.startswith("(allow file") and any(_q(r) in ln for r in reales):
            if i < ult_deny:
                return False, "allow de %s en la línea %d, antes del deny de la %d" % (
                    ln[:60], i + 1, ult_deny + 1)
    return True, "ok"


def _asegura_dir(d, modo=0o700):
    os.makedirs(d, mode=modo, exist_ok=True)
    try:
        os.chmod(d, modo)
    except OSError:
        pass


def escribe(nombre, destino_dir=None, **kw):
    """Genera el perfil y lo escribe 0600 en `destino_dir` (por defecto JAULAS). Devuelve la ruta."""
    destino_dir = destino_dir or JAULAS
    _asegura_dir(destino_dir)
    texto = perfil(nombre, **kw)
    ruta = os.path.join(destino_dir, nombre + ".sb")
    tmp = ruta + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(texto)
    os.replace(tmp, ruta)
    return ruta


def main(argv):
    if argv[:1] == ["mostrar"] and len(argv) == 2:
        sys.stdout.write(perfil(argv[1]))
        return 0
    if argv[:1] == ["comprobar"]:
        mal = 0
        for p in PERFILES:
            texto = perfil(p)
            ok, motivo = orden_correcto(texto, [SESION, ORIGEN, N1])
            if ok:
                ok, motivo = secretos_al_final(texto)
            print("%-16s %s" % (p, "ok" if ok else "MAL: " + motivo))
            mal += not ok
        return 1 if mal else 0
    print(__doc__.split("\n\n")[-1], file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
