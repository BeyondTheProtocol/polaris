#!/usr/bin/env python3
"""anatomia_push.py — sube el mapa del sistema al panel, cifrado.

POLARIS EMPUJA, NADIE ENTRA. Esa es toda la idea de seguridad: no se abre ningún
túnel ni ningún puerto hacia casa base. Esta tool renderiza La Anatomía, la cifra
y la deja en una rama de datos de un repo PRIVADO; la función de Netlify la
recoge de ahí y la sirve SIN ABRIRLA: quien descifra es el navegador de {{TITULAR}}.

Cuatro capas: repo privado · AES-256-GCM · la puerta (contraseña, luego Google) ·
y la de verdad, que la clave no está en el servidor sino en su cabeza.

Formato del fichero (`snapshot.enc`, texto para que git lo trate bien):
    base64( sal[16] || nonce[12] || AES-256-GCM(gzip(html)) || tag[16] )

La clave NO existe guardada en ninguna parte: se DERIVA de una frase con PBKDF2
(600.000 vueltas, SHA-256). La frase vive en el Llavero de Polaris para cifrar, y
{{TITULAR}} la teclea una vez en cada dispositivo para descifrar. **Netlify no la
tiene y no puede tenerla**, así que sirve un sobre que no sabe abrir.

Uso:
  python3 tools/anatomia_push.py --dry     # cifra y descifra, sin tocar GitHub
  python3 tools/anatomia_push.py           # cifra y empuja
  python3 tools/anatomia_push.py --frase-nueva    # crea la frase (una vez)
  python3 tools/anatomia_push.py --rotar-frase    # la cambia (si se quemó)
  python3 tools/anatomia_push.py --copiar-frase   # al portapapeles, sin imprimirla
"""

import base64
import os
import subprocess
import sys

REPO = os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode")
sys.path.insert(0, os.path.join(REPO, "tools"))

SERVICIO_FRASE = "btp-anatomia-panel-frase"
REPO_PANEL = os.environ.get("BTP_PANEL_REPO", "BeyondTheProtocol/anatomia-panel")
RAMA_DATOS = "datos"
FICHERO = "snapshot.enc"
# Clon de trabajo, fuera de la vista: solo lleva la rama de datos.
CLON = os.path.join(os.environ.get("BTP_STATE_DIR")
                    or os.path.join(REPO, "tools", "state"), "anatomia-panel")


def _frase():
    """La frase con la que se cifra, desde el Llavero. Sin ella no se cifra."""
    import _secrets
    v = _secrets.get(SERVICIO_FRASE)
    if not v:
        raise SystemExit(
            "sin frase en el Llavero. Créala una vez con:\n"
            "  python3 tools/anatomia_push.py --frase-nueva")
    return v


# El cifrado lo hace NODE, no Python, y a propósito:
#   · el python del sistema (/usr/bin/python3 3.9, el que usan los daemons) NO
#     trae `cryptography`; solo lo tiene el de Homebrew. Con la librería, esto
#     funcionaba en mi consola y habría fallado en producción.
#   · el panel descifra con `node:crypto`. Usando la MISMA implementación en los
#     dos lados no hay forma de que uno cifre distinto de como el otro descifra.
# La frase viaja por stdin, nunca por argv: `ps` deja ver los argumentos.
# La clave NO se guarda en ningún sitio: se DERIVA de una frase con PBKDF2, y el
# navegador de {{TITULAR}} hace exactamente lo mismo con Web Crypto. Así el servidor
# de Netlify no tiene con qué abrir el sobre, ni aunque quisiera: solo ve bytes.
# Los parámetros tienen que cuadrar CLAVADOS con los del visor del panel.
PBKDF2_VUELTAS = 600_000
_GUION = r"""
import crypto from "node:crypto"; import zlib from "node:zlib";
const derivar=(frase,sal)=>crypto.pbkdf2Sync(frase,sal,600000,32,"sha256");
let s=""; process.stdin.on("data",c=>s+=c).on("end",()=>{
  const d=JSON.parse(s);
  if(d.op==="cifrar"){
    const sal=crypto.randomBytes(16), n=crypto.randomBytes(12);
    const c=crypto.createCipheriv("aes-256-gcm",derivar(d.frase,sal),n);
    const cuerpo=Buffer.concat([c.update(zlib.gzipSync(Buffer.from(d.datos,"utf-8"))),c.final()]);
    process.stdout.write(Buffer.concat([sal,n,cuerpo,c.getAuthTag()]).toString("base64"));
  } else {
    const b=Buffer.from(d.datos,"base64");
    const x=crypto.createDecipheriv("aes-256-gcm",derivar(d.frase,b.subarray(0,16)),
      b.subarray(16,28));
    x.setAuthTag(b.subarray(b.length-16));
    const g=Buffer.concat([x.update(b.subarray(28,b.length-16)),x.final()]);
    process.stdout.write(zlib.gunzipSync(g).toString("utf-8"));
  }});
"""


def _node(op, frase, datos):
    import json
    import shutil
    exe = shutil.which("node") or "/opt/homebrew/bin/node"
    if not os.path.exists(exe):
        raise SystemExit("hace falta node para cifrar y no lo encuentro.")
    if not frase:
        raise ValueError("sin frase no se cifra")
    r = subprocess.run(
        [exe, "--input-type=module", "-e", _GUION],
        input=json.dumps({"op": op, "frase": frase, "datos": datos}),
        capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        raise ValueError("node no pudo %s: %s" % (op, r.stderr.strip()[:200]))
    return r.stdout


def cifrar(html, frase):
    """gzip + AES-256-GCM con clave derivada. Formato: sal[16]·nonce[12]·ct·tag[16]."""
    return _node("cifrar", frase, html)


def descifrar(texto, frase):
    """El camino de vuelta. Existe para poder PROBAR el ida y vuelta de verdad."""
    return _node("descifrar", frase, texto)


def _git(*args, cwd=None, check=True):
    r = subprocess.run(["git"] + list(args), cwd=cwd or CLON,
                       capture_output=True, text=True, timeout=120)
    if check and r.returncode != 0:
        raise SystemExit("git %s falló:\n%s" % (" ".join(args), r.stderr.strip()))
    return r


def _asegurar_identidad_git():
    """Identidad LOCAL fija para este clon de solo-datos (12-sep-2026,
    daemon_fallando:com.btp.anatomia-push).

    Sin user.name/user.email configurados (ni local ni global en esta máquina),
    git intenta AUTODETECTAR el autor a partir del hostname. Cuando Tailscale/mDNS
    no resuelve el hostname (mismo tipo de fallo de red que tumbaba enviar-hoy y
    calendar-sync ese día), git da 'polaris@Polaris.(none)' y lo rechaza:
    'fatal: no es posible auto-detectar la dirección de correo' → commit --amend
    falla → el daemon sale con exit≠0 aunque el snapshot en sí esté bien.
    Fija una identidad LOCAL (--local, no toca la global de nadie) para que el
    commit nunca dependa de resolver nada por red. Idempotente y barato
    (check=False: si el repo aún no existe, no hace nada)."""
    _git("config", "--local", "user.name", "Polaris (anatomia-push)", check=False)
    _git("config", "--local", "user.email", "anatomia-push@btp.local", check=False)


def _preparar_clon():
    """Clon mínimo con SOLO la rama de datos. Si no existe, la crea huérfana."""
    if os.path.isdir(os.path.join(CLON, ".git")):
        _asegurar_identidad_git()
        return
    os.makedirs(os.path.dirname(CLON), exist_ok=True)
    url = "https://github.com/%s.git" % REPO_PANEL
    r = subprocess.run(["git", "clone", "--depth", "1", "--branch", RAMA_DATOS,
                        url, CLON], capture_output=True, text=True, timeout=300)
    if r.returncode == 0:
        _asegurar_identidad_git()
        return
    # Aún no hay rama de datos: se arranca huérfana (sin historia del código).
    subprocess.run(["git", "clone", "--depth", "1", url, CLON],
                   capture_output=True, text=True, timeout=300, check=True)
    _asegurar_identidad_git()
    _git("checkout", "--orphan", RAMA_DATOS)
    _git("rm", "-rf", "--cached", ".", check=False)
    for n in os.listdir(CLON):
        if n != ".git":
            p = os.path.join(CLON, n)
            subprocess.run(["rm", "-rf", p], timeout=60)


def empujar(texto):
    """Un commit SIEMPRE, no mil: se enmienda y se fuerza.

    La rama de datos no es historia que interese; guardar un commit cada 5
    minutos serían ~1.400 al día y un repo que engorda para nada.
    """
    _preparar_clon()
    with open(os.path.join(CLON, FICHERO), "w", encoding="utf-8") as f:
        f.write(texto + "\n")
    _git("add", FICHERO)
    if not _git("status", "--porcelain", check=False).stdout.strip():
        return False
    hay_commit = _git("rev-parse", "--verify", "HEAD",
                      check=False).returncode == 0
    mensaje = "datos: instantanea de La Anatomia (cifrada)"
    if hay_commit:
        _git("commit", "--amend", "-m", mensaje, "--no-edit", "--allow-empty")
    else:
        _git("commit", "-m", mensaje)
    _git("push", "--force", "origin", "%s:%s" % (RAMA_DATOS, RAMA_DATOS))
    return True


def _al_portapapeles(valor):
    """La clave va al portapapeles, NUNCA a stdout.

    Lección del 25-jul-26: la primera versión la imprimía «para que {{TITULAR}} la
    pegase en Netlify» y con eso la dejó escrita en la transcripción del chat,
    que se guarda. Un secreto que se imprime está quemado. Por el portapapeles
    va de la máquina a Netlify sin pasar por ningún registro.
    """
    p = subprocess.run(["pbcopy"], input=valor, text=True, timeout=10)
    return p.returncode == 0


def _palabras(n=4):
    """Frase legible: n grupos de cinco, sin caracteres que se confundan."""
    import secrets
    alfabeto = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "-".join("".join(secrets.choice(alfabeto) for _ in range(5))
                    for _ in range(n))


def _frase_nueva(rotar=False):
    """Crea la frase con la que se cifra el mapa y la deja en el portapapeles.

    Esta frase **no se sube a ningún sitio**: se queda en el Llavero de Polaris
    para cifrar, y {{TITULAR}} la teclea UNA vez en cada dispositivo desde el que
    mire el panel. Netlify nunca la ve, y por eso no puede abrir el sobre.
    """
    import _secrets
    if _secrets.get(SERVICIO_FRASE) and not rotar:
        print("ya hay frase en el Llavero; no la toco. Para cambiarla: --rotar-frase")
        return 0
    frase = _palabras()
    r = subprocess.run(["security", "add-generic-password", "-s", SERVICIO_FRASE,
                        "-a", "panel", "-w", frase, "-U"],
                       capture_output=True, text=True, timeout=15)
    if r.returncode != 0:
        raise SystemExit("no pude guardarla en el Llavero:\n" + r.stderr)
    print("frase %s y guardada en el Llavero (no la ha visto nadie)."
          % ("ROTADA" if rotar else "creada"))
    print("  · está en el portapapeles → guárdala en 1Password"
          if _al_portapapeles(frase) else "  · no pude copiarla")
    print("  · la tecleas UNA vez en cada dispositivo; el navegador la recuerda")
    if rotar:
        print("  ⚠️  hay que volver a empujar el mapa: lo ya subido se cifró con la vieja")
    return 0


SERVICIO_PASS = "btp-anatomia-panel-pass"


def _hash_de(clara):
    """scrypt con los MISMOS parámetros que usa el panel en Node al validar."""
    import hashlib
    sal = os.urandom(16)
    h = hashlib.scrypt(clara.encode("utf-8"), salt=sal, n=32768, r=8, p=1,
                       dklen=32, maxmem=96 * 1024 * 1024)
    return "scrypt$32768$8$1$%s$%s" % (base64.b64encode(sal).decode(),
                                       base64.b64encode(h).decode())


def _pass_nueva():
    """Inventa una contraseña fuerte para el panel sin que la vea nadie.

    La genera, la guarda en el Llavero y deja en el portapapeles SOLO EL HASH,
    para pegarlo en Netlify. La contraseña en claro se saca aparte con
    `--copiar-pass`, para que {{TITULAR}} la guarde en 1Password. Ni se imprime ni
    pasa por el chat. Alternativa a `--hash-pass`, que es para cuando ella
    prefiere elegirla.
    """
    import secrets
    # Cuatro grupos de cinco, sin caracteres ambiguos: fuerte y aun tecleable.
    alfabeto = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    clara = "-".join("".join(secrets.choice(alfabeto) for _ in range(5))
                     for _ in range(4))
    r = subprocess.run(["security", "add-generic-password", "-s", SERVICIO_PASS,
                        "-a", "panel", "-w", clara, "-U"],
                       capture_output=True, text=True, timeout=15)
    if r.returncode != 0:
        raise SystemExit("no pude guardarla en el Llavero:\n" + r.stderr)
    print("contraseña generada y guardada en el Llavero (no la ha visto nadie).")
    print("  · el HASH está en el portapapeles → pégalo en PANEL_PASS_HASH"
          if _al_portapapeles(_hash_de(clara)) else "  · no pude copiar el hash")
    print("  · para guardarla en 1Password:"
          " python3 tools/anatomia_push.py --copiar-pass")
    return 0


def _copiar_pass():
    import _secrets
    v = _secrets.get(SERVICIO_PASS)
    if not v:
        raise SystemExit("no hay contraseña guardada: corre --pass-nueva")
    print("contraseña en el portapapeles: guárdala en 1Password y listo"
          if _al_portapapeles(v) else "no pude copiarla")
    return 0


def _hash_pass():
    """Convierte la contraseña que elija {{TITULAR}} en el hash que va a Netlify.

    La contraseña NO se teclea en el chat ni se guarda: se pide aquí sin eco, se
    pasa por scrypt y solo sale el hash, al portapapeles. Yo no la veo nunca.
    Los parámetros tienen que cuadrar con los del panel (Node hace el mismo
    scrypt al validar): N=32768, r=8, p=1, 32 bytes.
    """
    import getpass
    p1 = getpass.getpass("Contraseña para el panel: ")
    if len(p1) < 12:
        raise SystemExit("demasiado corta: mínimo 12 caracteres.")
    if p1 != getpass.getpass("Otra vez, para confirmar: "):
        raise SystemExit("no coinciden.")
    print("hecho. El HASH (no la contraseña) está en el portapapeles."
          if _al_portapapeles(_hash_de(p1)) else "no pude copiarlo")
    print("Pégalo en Netlify como PANEL_PASS_HASH.")
    return 0


def _copiar_frase():
    import _secrets
    v = _secrets.get(SERVICIO_FRASE)
    if not v:
        raise SystemExit("no hay frase todavía: corre --frase-nueva")
    print("frase en el portapapeles" if _al_portapapeles(v)
          else "no pude copiarla")
    return 0


def main(argv):
    if "--frase-nueva" in argv or "--rotar-frase" in argv:
        return _frase_nueva(rotar="--rotar-frase" in argv)
    if "--copiar-frase" in argv:
        return _copiar_frase()
    if "--hash-pass" in argv:
        return _hash_pass()
    if "--pass-nueva" in argv:
        return _pass_nueva()
    if "--copiar-pass" in argv:
        return _copiar_pass()

    import anatomia
    html = anatomia.render(cara="privada")
    frase = _frase()
    texto = cifrar(html, frase)

    # Comprobar SIEMPRE el ida y vuelta antes de publicar: más vale no subir nada
    # que subir algo que el panel no sepa abrir.
    if descifrar(texto, frase) != html:
        raise SystemExit("el ida y vuelta no cuadra: NO subo nada.")

    if "--dry" in argv:
        print("ok: %d KB de HTML → %d KB cifrados (ida y vuelta verificado)"
              % (len(html) // 1024, len(texto) // 1024))
        return 0

    print("subido" if empujar(texto) else "sin cambios")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
