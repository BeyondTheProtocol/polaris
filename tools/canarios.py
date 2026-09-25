#!/usr/bin/env python3
"""tools/canarios.py — los SEÑUELOS del muro: si su token sale, es exfiltración.

POR QUÉ EXISTE (24-sep-2026, deuda `canarios-reales-sin-configurar`). `borde.egress_check` lleva
meses bloqueando, sellando y escalando a código rojo cualquier canario que aparezca en algo que
sale… pero `tools/state/borde/canarios.json` NO EXISTÍA: cero canarios sembrados, o sea, un
tripwire que no podía dispararse. Y desde el 24-sep, al arreglar el falso positivo de las pruebas,
solo escala el canario SEMBRADO: sin este fichero no escala nada.

QUÉ ES UN SEÑUELO, Y QUÉ NO
  NO es un documento suyo marcado: es un cebo que **ningún flujo legítimo lee**. Por eso su token
  en una salida no tiene explicación inocente. Marcar documentos reales daría falsos positivos, y
  un falso positivo aquí cuesta una tarde: para las máquinas y le llega una alerta.

LOS QUE SE SIEMBRAN (y por qué no más)
  · `llavero` — un secreto señuelo (`btp-canario-llavero`). Ninguna tool lo pide; si aparece en
                una salida, algo está barriendo el Llavero.
  · `estado`  — un fichero señuelo en `tools/state/borde/`. Ningún consumidor lo lee (su nombre no
                casa con ninguna tool); cubre el volcado de estado interno a un tercero.
  · `boveda`  — OPCIONAL y manual: plantarlo exige atravesar `clinico_guard.py` (la ventanilla
                auditada) y la bóveda ya tiene ese guard propio. Si se quiere: se copia con
                `lector_clinico.py --a` un fichero `.canario` (el RAG indexa .md/.txt/.pdf, así
                que no lo arrastra a una respuesta interna) y se registra con `registrar`.

EL VALOR NO SE IMPRIME NUNCA. `estado` dice cuántos hay y de cuándo, no cuáles.

Uso:
  python3 tools/canarios.py sembrar [--solo llavero,estado]
  python3 tools/canarios.py estado
  python3 tools/canarios.py rotar <etiqueta>
  python3 tools/canarios.py registrar --etiqueta boveda --valor-de <fichero> [--donde "..."]
"""
import argparse
import hashlib
import json
import os
import secrets
import sys
from datetime import datetime, timezone

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)

REPO = os.path.dirname(AQUI)
STATE = os.environ.get("BTP_STATE_DIR") or os.path.join(REPO, "tools", "state")
BORDE_DIR = os.path.join(STATE, "borde")
CANARIOS = os.path.join(BORDE_DIR, "canarios.json")           # lo lee borde._canarios_con_origen
REGISTRO = os.path.join(BORDE_DIR, "canarios_registro.json")  # metadatos, sin valores
SENUELO_ESTADO = os.path.join(BORDE_DIR, "senuelo_no_leer.json")
SERVICIO_LLAVERO = "btp-canario-llavero"
PREFIJO = "BTPCAN-"


def _token():
    return PREFIJO + secrets.token_urlsafe(18)


def _sello(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]


def _leer(ruta, por_defecto):
    try:
        with open(ruta, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return por_defecto


def _escribir(ruta, datos):
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    tmp = ruta + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, indent=1)
    os.replace(tmp, ruta)


def _registrar(etiqueta, token, donde):
    """Mete el token en canarios.json (lo que lee el borde) y su ficha en el registro (sin valor).
    Al rotar retira el token anterior de esa etiqueta: si no, el viejo seguiría dando alarma."""
    vals = [c for c in _leer(CANARIOS, []) if isinstance(c, str)]
    reg = _leer(REGISTRO, {})
    viejo = (reg.get(etiqueta) or {}).get("sello")
    vals = [v for v in vals if _sello(v) != viejo]
    if token not in vals:
        vals.append(token)
    _escribir(CANARIOS, vals)
    reg[etiqueta] = {"sello": _sello(token), "donde": donde,
                     "sembrado": datetime.now(timezone.utc).isoformat()}
    _escribir(REGISTRO, reg)


def _bonita(ruta):
    """Ruta legible: relativa al repo si cuelga de él, absoluta si el estado vive fuera (tests)."""
    r = os.path.abspath(ruta)
    return os.path.relpath(r, REPO) if r.startswith(os.path.abspath(REPO) + os.sep) else r


def sembrar_llavero():
    """Secreto señuelo en el Llavero. Devuelve (ok, detalle). Nunca imprime el valor."""
    token = _token()
    try:
        from _secrets import set as guardar
        guardar(SERVICIO_LLAVERO, token)
    except Exception as e:
        return False, "el Llavero lo rechazó (%r)" % (e,)
    _registrar("llavero", token, "Llavero: %s" % SERVICIO_LLAVERO)
    return True, "Llavero: %s" % SERVICIO_LLAVERO


def sembrar_estado():
    """Fichero señuelo en el estado del borde. Ninguna tool lo lee: su nombre no casa con nadie."""
    token = _token()
    _escribir(SENUELO_ESTADO, {
        "_que_es": ("SEÑUELO del muro: ningún flujo legítimo lee este fichero. Si su token aparece "
                    "en algo que sale, es exfiltración y el borde dispara código rojo. No lo "
                    "borres ni lo cites: se gestiona con tools/canarios.py."),
        "token": token,
    })
    donde = _bonita(SENUELO_ESTADO)
    _registrar("estado", token, donde)
    return True, donde


SEMBRADORES = {"llavero": sembrar_llavero, "estado": sembrar_estado}


def cmd_sembrar(a):
    fallos = 0
    for etiqueta in [x.strip() for x in (a.solo or "llavero,estado").split(",") if x.strip()]:
        f = SEMBRADORES.get(etiqueta)
        if not f:
            print("· %s: no sé sembrar eso (hay: %s)" % (etiqueta, ", ".join(SEMBRADORES)))
            fallos += 1
            continue
        ok, detalle = f()
        print(("✅ %s sembrado → %s" if ok else "❌ %s: %s") % (etiqueta, detalle))
        fallos += 0 if ok else 1
    print("canarios activos: %d" % len(_leer(CANARIOS, [])))
    return 1 if fallos else 0


def cmd_estado(a):
    vals = _leer(CANARIOS, [])
    reg = _leer(REGISTRO, {})
    if not vals:
        print("⚠️  NINGÚN canario sembrado: el tripwire de exfiltración no puede dispararse.")
        print("    python3 tools/canarios.py sembrar")
        return 1
    print("canarios sembrados: %d (el valor NO se imprime)" % len(vals))
    for etiqueta, d in sorted(reg.items()):
        print("  · %-8s %s · desde %s · sello %s"
              % (etiqueta, d.get("donde", "?"), (d.get("sembrado") or "?")[:10], d.get("sello")))
    if len(vals) > len(reg):
        print("  · %d sin ficha en el registro (sembrados a mano)" % (len(vals) - len(reg)))
    return 0


def cmd_rotar(a):
    f = SEMBRADORES.get(a.etiqueta)
    if not f:
        print("no sé rotar %r (hay: %s)" % (a.etiqueta, ", ".join(SEMBRADORES)))
        return 1
    ok, detalle = f()
    print(("✅ %s rotado (%s): el token viejo ya no vale" if ok else "❌ %s: %s")
          % (a.etiqueta, detalle))
    return 0 if ok else 1


def cmd_registrar(a):
    """Registra un señuelo plantado FUERA de aquí (p.ej. el de la bóveda, por la ventanilla)."""
    try:
        with open(a.valor_de, encoding="utf-8") as f:
            token = f.read().strip()
    except OSError as e:
        print("no puedo leer el fichero del token: %r" % (e,))
        return 1
    if not token.startswith(PREFIJO) or len(token) < len(PREFIJO) + 16:
        print("eso no parece un token de canario (prefijo %s y ≥16 caracteres)" % PREFIJO)
        return 1
    _registrar(a.etiqueta, token, a.donde or "(plantado a mano)")
    print("✅ %s registrado (el valor no se imprime)" % a.etiqueta)
    return 0


def main():
    p = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    sub = p.add_subparsers(dest="cmd")
    s = sub.add_parser("sembrar")
    s.add_argument("--solo")
    sub.add_parser("estado")
    s = sub.add_parser("rotar")
    s.add_argument("etiqueta")
    s = sub.add_parser("registrar")
    s.add_argument("--etiqueta", required=True)
    s.add_argument("--valor-de", dest="valor_de", required=True)
    s.add_argument("--donde")
    a = p.parse_args()
    return {"sembrar": cmd_sembrar, "estado": cmd_estado, "rotar": cmd_rotar,
            "registrar": cmd_registrar}.get(a.cmd, lambda _a: (p.print_help(), 0)[1])(a)


if __name__ == "__main__":
    sys.exit(main())
