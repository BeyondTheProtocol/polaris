#!/usr/bin/env python3
"""tools/persecucion.py — F4.1: Vega no solo AVISA, EJECUTA hasta cerrar (determinista, drive-to-close).

`seguimiento.perseguir()` clasifica cada hilo que se cae: codigo_rojo / aviso / entrega / desbloquea.
Hoy esos veredictos solo se funden en el parte; el ENCARGO al comité dependía de que el LLM decidiera
(frágil, cuesta, se puede caer por saldo). Este módulo hace DETERMINISTA y $0 el caso **`entrega`**
(hilo NO-clínico con comité dueño REAL que se está cayendo): encola el trabajo INTERNO al comité
(`cola.enqueue`) y lo persigue, en vez de esperar a que {{TITULAR}} lo pase a mano. De "avisar" a "hacer".

Muro / límites (el muro es diseño, no se relaja):
  · SOLO el caso `entrega` (interno, comité de confianza). `aviso` (toca a {{TITULAR}}) / `codigo_rojo` /
    `desbloquea` (hacia un tercero, FUERA) NO se auto-ejecutan: siguen por su aviso/borrador CON su OK.
  · Lo CLÍNICO nunca se delega (perseguir ya lo manda a `aviso`).
  · 0 egress propio: solo encola trabajo INTERNO; el resultado del comité sale en BORRADOR y el muro
    del comité + `salida.py` gatean cualquier acto externo (nada hacia fuera sin la firma de {{TITULAR}}).
  · Idempotente con COOLDOWN por hilo (`state/persecucion/<id>.json`): no re-encola el mismo hilo en
    cada pasada (anti fork-bomb). Para solo cuando el hilo se sella `hecho` (perseguir lo salta).
  · La intención se SANEA (texto de seguimiento, determinista; anti-inyección) antes de encolar.
  · Resuelve el estado a CASA BASE (BTP_REPO o ~/claudecode), como el resto del estado vivo.

Uso:
  python3 tools/persecucion.py run     # delega al comité lo que se cae y persigue; sella idempotencia
  python3 tools/persecucion.py check   # qué delegaría, SIN encolar (dry)
  python3 tools/persecucion.py status
"""
import json
import os
import re
import sys
from datetime import datetime, timedelta

REPO = os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode")
STATE = os.environ.get("BTP_STATE_DIR") or os.path.join(REPO, "tools", "state")
PERS_DIR = os.path.join(STATE, "persecucion")
COOLDOWN_H = 24.0                 # no re-delegar el mismo hilo más de 1×/día
MAX_INTENCION = 240
_CTRL = re.compile(r"[\x00-\x1f\x7f]")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _sanea(s):
    s = _CTRL.sub(" ", str(s or ""))
    return re.sub(r"\s+", " ", s).strip()[:MAX_INTENCION]


def _pers_path(hid):
    safe = re.sub(r"[^\w\-]", "_", str(hid))[:60] or "x"
    return os.path.join(PERS_DIR, safe + ".json")


def _cargar(hid):
    try:
        with open(_pers_path(hid), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _guardar(hid, d):
    os.makedirs(PERS_DIR, mode=0o700, exist_ok=True)
    p = _pers_path(hid)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


def _en_cooldown(hid):
    d = _cargar(hid)
    if not d or not d.get("ts"):
        return False
    try:
        return (datetime.now() - datetime.fromisoformat(d["ts"])) < timedelta(hours=COOLDOWN_H)
    except Exception:
        return False


def _intencion(it):
    titulo = _sanea(it.get("titulo"))
    sig = _sanea(it.get("siguiente_accion") or it.get("siguiente") or "")
    return ("Avanza hacia NED el hilo «%s»%s. Es trabajo INTERNO: investiga/prepara y deja el resultado "
            "en BORRADOR para el OK de {{TITULAR}} — NADA hacia fuera sin su firma (el muro)."
            % (titulo or "(sin título)", (" — siguiente: " + sig) if sig else ""))


def evaluar(dry=False):
    """[(hilo_id, comité, intencion, accion)] de lo que se delega. accion ∈ {encolado, encolaría,
    cooldown, sin_datos, error:...}. dry=True no encola ni sella. Nunca lanza (devuelve error en línea)."""
    import seguimiento as sg
    try:
        res = sg.perseguir(ejecutar=False)
        items = sg.recopilar().get("items", [])
    except Exception as e:
        return [(None, None, "", "error:%r" % e)]
    by_id = {it.get("id"): it for it in items if isinstance(it, dict)}
    out = []
    for s in res.get("salidas", []):
        if s.get("salida") != "entrega":          # SOLO el handoff interno a un comité
            continue
        hid, comite = s.get("hilo_id") or "", s.get("destino") or ""
        if not hid or not comite:
            continue
        if _en_cooldown(hid):
            out.append((hid, comite, "", "cooldown"))
            continue
        it = by_id.get(hid)
        if it is None:
            out.append((hid, comite, "", "sin_datos"))
            continue
        intencion = _intencion(it)
        if dry:
            out.append((hid, comite, intencion, "encolaría"))
            continue
        try:
            import cola as q
            jid = q.enqueue(intencion, agente=comite, prioridad="normal",
                            procedencia="vega-persecucion", criticidad="rutina", max_intentos=2)
            _guardar(hid, {"ts": datetime.now().isoformat(timespec="seconds"), "job_id": jid,
                           "comite": comite, "intentos": ((_cargar(hid) or {}).get("intentos", 0)) + 1})
            out.append((hid, comite, intencion, "encolado"))
        except Exception as e:
            out.append((hid, comite, "", "error:%r" % e))
    return out


def main(argv):
    cmd = argv[0] if argv else "run"
    if cmd in ("-h", "--help"):
        print(__doc__)
        return 0
    if cmd in ("run", "check"):
        out = evaluar(dry=(cmd == "check"))
        encolados = [o for o in out if o[3] == "encolado"]
        if cmd == "check":
            for hid, comite, _i, accion in out:
                print("· %s → %s [%s]" % (hid, comite, accion))
            print("(dry) delegaría %d hilo(s) a su comité" % sum(1 for o in out if o[3] == "encolaría"))
        else:
            print("delegué %d hilo(s) a su comité (drive-to-close)" % len(encolados) if encolados
                  else "nada que delegar ahora")
        return 0
    if cmd == "status":
        try:
            n = len([f for f in os.listdir(PERS_DIR) if f.endswith(".json")])
        except OSError:
            n = 0
        print(json.dumps({"hilos_en_persecucion": n, "cooldown_h": COOLDOWN_H}, ensure_ascii=False))
        return 0
    print("uso: persecucion.py [run | check | status]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
