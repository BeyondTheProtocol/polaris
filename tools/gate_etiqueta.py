#!/usr/bin/env python3
"""tools/gate_etiqueta.py — etiqueta los hallazgos del gate de salida para PROMOVER un check de
aviso a bloqueo con datos, no a ojo.

POR QUÉ EXISTE (13-sep-2026, Arreglo B de
`00_FUENTE-DE-VERDAD/04 · IA/Notas/plan-enrutado-crudo-solo-local-y-gate-por-check-13-sep-26-...md`).
El gate (`.claude/hooks/gate_salida.py`) ya soporta modo por check y ya tiene precedente real
(`falsa_certeza`, 31-jul-26: "2 hallazgos en 6 días de 69") — pero promover a ojo no escala y no
deja rastro de CUÁNTOS de esos hallazgos eran aciertos de verdad. Con `_apunta()` instrumentado
(ts, session_hash, bloqueo_real, extracto de-identificado) esta tool cierra el círculo: lista lo
sin etiquetar por check y guarda el veredicto de quien lo revisa.

QUIÉN ETIQUETA (decisión de {{TITULAR}}, 13-sep-26): por defecto el comité `verificacion` — ya es el
watchdog adversarial, no gasta su energía. **{{TITULAR}} solo ve el `resumen` (N aciertos / M falsos
positivos por check), nunca fila a fila.**

CRITERIO DE PROMOCIÓN — por check, según volumen (no un umbral único de 20 casos; ver el plan):
  · `secuencia_sin_tabla`, `coste_no_bloquea` (1 hallazgo cada uno hoy) — revisar el único caso;
    acierto claro → promover ya.
  · `no_se_sin_mirar` (7) — revisar los 7; 0 falsos positivos → promover.
  · `tells_ia` (68) — muestra de 20, ≤2 falsos positivos → promover.
  · `convergencia` (129) — el último: antes de promover, medir cuánta charla de bajo riesgo hay
    frente a entregable con peso — puede que el arreglo real sea afinar el PATRÓN, no solo
    bloquear más.
  · `falsa_certeza`, `citas_fabricadas` ya bloquean; `enrutado_incumplido` tiene su propio plan
    (nota "jaunty-greeting-pelican" en `tools/normas.json`) — ninguno de los tres se toca aquí.

Promover de verdad es una línea en `tools/normas.json` (`"modo": "bloqueo"`), a mano, con la nota
del porqué — esta tool NO escribe ahí: mide, no decide.

CLI:
  python3 tools/gate_etiqueta.py list --check <check> [--sin-etiquetar]
  python3 tools/gate_etiqueta.py marcar <id> acierto|falso_positivo ["nota"]
  python3 tools/gate_etiqueta.py resumen [--check <check>] [--json]
  python3 tools/gate_etiqueta.py contexto [--check <check>] [--todas]   # respuesta entera + petición

El log vivo (`gate_salida.jsonl`) y las etiquetas viven en el estado de CASA BASE (gitignored) —
misma resolución que el hook: `_casa.state_dir()` / `BTP_STATE_DIR`
(`feedback-estado-vivo-resuelve-casa-base`). Determinista, stdlib, local. Escritura atómica
(`os.replace`, mismo patrón que `tools/deuda.py::_guardar`).
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _casa  # noqa: E402

STATE = _casa.state_dir()
LOG = os.path.join(STATE, "gate_salida.jsonl")
ETIQUETAS = os.path.join(STATE, "gate_etiquetas.json")
VEREDICTOS = ("acierto", "falso_positivo")


def _leer_log():
    """[(id, fila)] — id = índice de línea (string), estable porque el log es append-only:
    una fila nunca cambia de posición, solo se añaden nuevas detrás."""
    if not os.path.exists(LOG):
        return []
    filas = []
    with open(LOG, encoding="utf-8") as f:
        for i, ln in enumerate(f):
            ln = ln.strip()
            if not ln:
                continue
            try:
                filas.append((str(i), json.loads(ln)))
            except Exception:
                continue                                # una línea corrupta no tira las demás
    return filas


def _cargar_etiquetas():
    try:
        with open(ETIQUETAS, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _guardar_etiquetas(d):
    os.makedirs(os.path.dirname(ETIQUETAS), exist_ok=True)
    tmp = ETIQUETAS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, ETIQUETAS)


def listar(check=None, solo_sin_etiquetar=False):
    etiquetas = _cargar_etiquetas()
    out = []
    for id_, fila in _leer_log():
        if check and fila.get("check") != check:
            continue
        if solo_sin_etiquetar and id_ in etiquetas:
            continue
        out.append((id_, fila, etiquetas.get(id_)))
    return out


def marcar(id_, veredicto, nota=""):
    if veredicto not in VEREDICTOS:
        return False, "veredicto debe ser %s" % " o ".join(VEREDICTOS)
    filas = dict(_leer_log())
    if id_ not in filas:
        return False, "no existe la fila %r en %s" % (id_, LOG)
    etiquetas = _cargar_etiquetas()
    etiquetas[id_] = {"veredicto": veredicto, "nota": nota, "ts": time.time(),
                      "check": filas[id_].get("check")}
    _guardar_etiquetas(etiquetas)
    return True, "%s → %s" % (id_, veredicto)


def resumen(check=None):
    """{check: {total, aciertos, falsos_positivos, sin_etiquetar}} — lo único que ve {{TITULAR}}."""
    etiquetas = _cargar_etiquetas()
    out = {}
    for id_, fila in _leer_log():
        c = fila.get("check")
        if check and c != check:
            continue
        r = out.setdefault(c, {"total": 0, "aciertos": 0, "falsos_positivos": 0,
                               "sin_etiquetar": 0})
        r["total"] += 1
        et = etiquetas.get(id_)
        if not et:
            r["sin_etiquetar"] += 1
        elif et.get("veredicto") == "acierto":
            r["aciertos"] += 1
        elif et.get("veredicto") == "falso_positivo":
            r["falsos_positivos"] += 1
    return out


def _transcripts_por_hash(hashes, raiz=None):
    """{session_hash: ruta del .jsonl} — el hook guarda sha256(session_id)[:16], no el id."""
    import glob
    import hashlib
    raiz = raiz or os.environ.get("BTP_PROJECTS_DIR") or os.path.expanduser("~/.claude/projects")
    out = {}
    for ruta in glob.glob(os.path.join(raiz, "*", "*.jsonl")):
        h = hashlib.sha256(os.path.basename(ruta)[:-6].encode("utf-8")).hexdigest()[:16]
        if h in hashes:
            out[h] = ruta
    return out


def _texto(o):
    c = (o.get("message") or {}).get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "\n".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text")
    return ""


def contexto(check=None, solo_sin_etiquetar=True, max_resp=3000, max_pide=800):
    """Para ETIQUETAR con criterio: la frase suelta no basta. Por cada fila, la respuesta entera
    donde salió, lo que se pidió en ese turno y las tools usadas. Escala P2 (25-sep-26), idea de
    {{CONTACTO}} (https://contacto), con su agente KAI, revisión del 25-sep-2026.
    Sale texto CLÍNICO: solo a Claude o local, nunca a un modelo de fuera (`enruta.py`)."""
    filas = listar(check, solo_sin_etiquetar)
    rutas = _transcripts_por_hash({f.get("session_hash") for _i, f, _e in filas} - {None})
    cache, out = {}, []
    for id_, fila, _et in filas:
        frase = (fila.get("extracto") or "").rstrip("…").strip()[:60]
        item = {"id": id_, "check": fila.get("check"), "motivo": fila.get("motivo"),
                "bloqueo_real": fila.get("bloqueo_real"), "encontrado": False}
        ruta = rutas.get(fila.get("session_hash"))
        if ruta and frase:
            if ruta not in cache:
                with open(ruta, encoding="utf-8", errors="ignore") as f:
                    cache[ruta] = [json.loads(l) for l in f if l.strip().startswith("{")]
            ultimo_pide, tools = "", []
            for o in cache[ruta]:
                if o.get("type") == "user" and not o.get("isSidechain"):
                    t = _texto(o)
                    if t and not t.lstrip().startswith("<"):
                        ultimo_pide, tools = t, []
                if o.get("type") == "assistant":
                    for x in (o.get("message") or {}).get("content") or []:
                        if isinstance(x, dict) and x.get("type") == "tool_use":
                            tools.append(x.get("name"))
                    t = _texto(o)
                    if frase in t:
                        pos = t.index(frase)                   # ventana centrada en la frase
                        ini = max(0, pos - max_resp * 2 // 3)
                        item.update(encontrado=True, respuesta=t[ini:ini + max_resp],
                                    frase=frase,
                                    pidio=ultimo_pide[:max_pide], tools_del_turno=tools[-40:])
                        break
        out.append(item)
    return out


def _arg(a, flag, defecto=None):
    return a[a.index(flag) + 1] if flag in a and len(a) > a.index(flag) + 1 else defecto


def main(argv):
    cmd = argv[0] if argv else "resumen"
    a = argv[1:]
    if cmd == "list":
        filas = listar(_arg(a, "--check"), "--sin-etiquetar" in a)
        for id_, fila, et in filas:
            marca = "· %s" % et["veredicto"] if et else "(sin etiquetar)"
            print("[%s] %-22s bloqueo_real=%-5s %s — «%s»" % (
                id_, fila.get("check"), fila.get("bloqueo_real"), marca,
                (fila.get("extracto") or "")[:120]))
        print("— %d fila(s)" % len(filas))
        return 0
    if cmd == "marcar":
        if len(a) < 2:
            print('uso: gate_etiqueta.py marcar <id> acierto|falso_positivo ["nota"]')
            return 2
        ok, motivo = marcar(a[0], a[1], a[2] if len(a) > 2 else "")
        print(("✅ " if ok else "❌ ") + motivo)
        return 0 if ok else 1
    if cmd == "contexto":
        print(json.dumps(contexto(_arg(a, "--check"), "--todas" not in a), ensure_ascii=False,
                         indent=1))
        return 0
    if cmd == "resumen":
        r = resumen(_arg(a, "--check"))
        if "--json" in a:
            print(json.dumps(r, ensure_ascii=False, indent=1, sort_keys=True))
        else:
            for c, v in sorted(r.items()):
                print("%-22s total=%-4d aciertos=%-4d FP=%-4d sin_etiquetar=%d"
                      % (c, v["total"], v["aciertos"], v["falsos_positivos"], v["sin_etiquetar"]))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
