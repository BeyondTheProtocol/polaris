#!/usr/bin/env python3
"""tools/panel_vision/calibra.py — arnés de CALIBRACIÓN del panel de visión: qué modelo revisa qué
capa, decidido por lo que DEMUESTRA detectar sobre errores sembrados, no por su fama.

POR QUÉ (plan «laminillas DFCI», actualización 3 y decisión 3 de {{TITULAR}}: «debería procesarlas lo
mejor, no solo Anthropic»). El panel REVISA capas y figuras; no puntúa biomarcadores. Candidatos:
Qwen3-VL y Qwen3.5 en local (Ollama), Gemini, Claude y MedGemma. La evidencia abierta (arXiv
2606.16658: Gemini 3 Pro, el mejor revisando máscaras en láminas renales, sin Claude) no traslada a
NUESTRAS capas, y además es de OTRO modelo: Gemini 3 Pro se apagó el 9-mar-2026 y el vigente es
`gemini-3.1-pro-preview` (auditoría de la API de Gemini, 2-oct-2026: «apto con condiciones»). Se
mide aquí, tarea a tarea, y cada modelo se usa SOLO donde pasa el criterio.

PROTOCOLO
  · Conjunto (`generar`): por tarea, n casos con UN error sembrado y n controles sin error
    (`sinteticos.py`), tipos y cuadrantes equilibrados, magnitudes registradas. Nombres opacos; la
    verdad vive en `verdad.json` y NUNCA va al modelo. Orden barajado. El manifiesto sella el sha256
    de cada PNG y la huella del conjunto (imágenes + preguntas + verdad).
  · Pregunta: una imagen por petición, sin historial, con la plantilla de su tarea (`pregunta()`).
    Respuesta CERRADA, un objeto JSON y nada más:
        {"hay_error": bool, "tipo": <tipo de la tarea> | null, "cuadrante": <cuadrante> | null}
    con hay_error=false ⇔ tipo=null y cuadrante=null. Se tolera envoltorio (```json, <think>);
    claves de más, valores fuera del vocabulario o tipos JSON equivocados = respuesta INVÁLIDA.
  · Puntuación, por tarea y por modelo:
        sensibilidad = aciertos LOCALIZADOS (hay_error y cuadrante correctos) / casos con error
        especificidad = hay_error=false / controles
    Inválida, error del adaptador o sin respuesta = fallo en ambos sentidos (fail-closed: un
    modelo que se calla o se sale del formato no puntúa). IC 95 % de Wilson. Se informan además la
    sensibilidad laxa (sin cuadrante), el acierto de tipo y la sensibilidad por tipo y magnitud.
  · Criterio («cada modelo se usa solo para lo que demuestre detectar»): en ESA tarea, n ≥ 30
    casos con error y n ≥ 30 controles, sensibilidad ≥ 0,8 y especificidad ≥ 0,8, y el límite
    inferior de Wilson de ambas ≥ 0,6. Si no, ese modelo NO se usa en esa tarea. Sin resultado =
    no se usa (deny por defecto). Con n ≥ 30 y p ≥ 0,8 el límite inferior ya es ≥ 0,627, así que el
    IC solo muerde si se tocan los umbrales; se mantiene como salvaguarda explícita.
  · `modelos_autorizados()` es la consulta del panel: recalcula la decisión desde los recuentos
    (no se fía del «usar» escrito en el JSON) y exige que el protocolo y las preguntas sean los
    vigentes: cambiar una plantilla invalida la calibración.
  · SOLO A CIEGAS (2-oct-26): una calibración `excluido()` se informa pero no habilita nada: la
    marcada al exportar con `--no-ciega <clave o nombre>=<motivo>` (p. ej. el evaluador conocía el
    generador) y la de un `transcrito` que no se declaró a ciegas con `--ciega` (deny por defecto).

ADAPTADORES
  · `ollama:<modelo>` — IMPLEMENTADO, solo local: host de loopback literal (127.0.0.0/8, ::1;
    «localhost» se traduce a 127.0.0.1), sin proxies (urllib los tomaría del entorno), y rechaza
    los modelos «cloud» de Ollama (nombre «…-cloud» o `/api/show` con campos `remote_*`: esos
    salen a ollama.com aunque se llamen por localhost) y los que no declaran visión. Sella en la
    configuración el digest del modelo: otro digest es otra calibración. `--num-ctx` fija el
    contexto (entra en la configuración: otro contexto, otra clave).
  · `gemini[:gemini-3.1-pro-preview]` — CONECTADO a `tools/vision_n1.py` (2-oct-26): este arnés
    no habla con ninguna API; `Externo.responder` delega en `vision_n1.enviar("vision-n1:gemini",
    …)`, que hace la Puerta, la confianza tecleada, las condiciones de la auditoría, la clave del
    Llavero, el aviso del primer envío y el sello. El modelo está FIJADO: otro id es ValueError
    (`gemini-3-pro-preview`, apagado, también). `comprueba()` falla cerrado con `ProveedorNoListo`
    si `auditorias.json` de casa base no dice `condiciones_cumplidas: true` (sin importar
    vision_n1) o si `vision_n1.listo` no pasa (trust-cloud sin teclear, Puerta…), antes de escribir
    nada. Las imágenes de calibración SINTÉTICAS también pasan por N1: `a-n1 --dir <dir>` exporta
    cada PNG del conjunto por `exporta_n1.exporta_png` (nombre opaco `CAL-<n>`, OCR + Puerta +
    trazos), comprueba que los píxeles son los mismos y deja el mapa caso → fichero N1 en
    `<dir>/n1.json`; sin ese mapa, `responder` lanza `FueraDeN1`. Los PNG de una revisión de capas
    ya viven en N1 y van tal cual. El CLI pone `avisar` (el aviso del primer envío es opt-in).
  · `claude[:claude-opus-5-5]` — por `vision_n1` como Gemini (adaptador del 8-oct-26, modelo
    fijado, clave cedida `btp-anthropic-api-prestada`); sin su entrada en la auditoría de casa
    base y su `trust-cloud vision-n1:claude`, `ProveedorNoListo`. Su calibración es PROPIA: la que
    Claude pasó como `transcrito` (dentro de la sesión) no habilita a `claude:` por API.
  · `medgemma[:google/medgemma-4b-it]` — LOCAL (`medgemma_local.py`, transformers en MPS, sin red,
    pesos del commit sellado en `laminillas_stack/pesos.json` con sha256): hoy «gated» y sin pesos,
    así que `comprueba()` lanza `PendienteDeAcceso` («pendiente de acceso») y `modelos` lo marca así.
  · `transcrito:<nombre>` — respuestas YA ESCRITAS por un evaluador que miró los PNG fuera del
    arnés, en un JSONL {"id", "crudo"}: Claude dentro de la sesión (leyendo cada PNG con Read, que
    es como lo usa el tribunal: subagentes con las imágenes delante) o una persona. Pasan por el
    mismo `interpreta` y la misma puntuación. El evaluador declara que no abrió `verdad.json`; con
    `--ciega` declara además que no conocía el generador, y solo así su calibración habilita.

RECURSOS (solo `ollama:`, antes de CADA lote de `--lote` casos): se descarga el propio modelo y se
exige `memory_pressure` con ≥25 % libre y ningún OTRO modelo grande (≥3 GB) cargado en Ollama. Si
no, espera 5 min y vuelve a mirar; a la hora, para con `SinMemoria` y lo dice. Al terminar, descarga
el modelo (keep_alive 0). (Medido con el propio 9B cargado, un Mac de 16 GB marca ~20 % libre.)

SALIDA (contrato con `laminillas_proc tribunal-listo`, punto 5-bis del plan):
  (i)   `exportar` escribe `tools/panel_vision/calibracion_sintetica.json`: protocolo, huella de las
        preguntas, sha_conjunto, puntuado, recuentos, métricas y decisión por modelo y tarea, la
        verdad sintética y la respuesta de cada modelo por caso, y los límites. Nada clínico. La
        calibración sobre capas N1 reales sale por el MISMO `exportar --salida
        <n1>/panel_vision/calibracion_n1.json`, de otro conjunto y puntuada después: el que escribe
        `sembrar-n1` (`siembra_n1.py`) con errores sembrados sobre las capas de `capas_piloto.json`,
        solo dentro de `<n1>/panel_vision/` y cada imagen por la Puerta de N1.
  (ii)  `revisar` escribe `<n1>/panel_vision/revisiones.jsonl` (una línea por clave, tarea y sha256)
        a partir de `<n1>/panel_vision/capas_piloto.json`.
  (iii) `tools/panel_vision/auditorias.json`: veredicto de cada proveedor externo auditado.

Uso (la generación necesita numpy/scipy/Pillow: venv `patologia`; puntuar y consultar, no):
  PY=~/.polaris-venvs/patologia/bin/python
  $PY tools/panel_vision/calibra.py generar --dir <dir> [--n 30] [--lado 768] [--semilla 20261001]
  $PY tools/panel_vision/calibra.py correr  --dir <dir> --modelo ollama:qwen3-vl:8b-instruct --num-ctx 4096 [--tareas a,b] [--limite 10] [--lote 10]
  $PY tools/panel_vision/calibra.py correr  --dir <dir> --modelo transcrito:<nombre> --respuestas <f.jsonl> --evaluador "<quién y cómo>" [--ciega]
  python3 tools/panel_vision/calibra.py puntuar  --dir <dir>
  python3 tools/panel_vision/calibra.py exportar --dir <dir> [--salida tools/panel_vision/calibracion_sintetica.json] [--no-ciega <nombre>=<motivo>]
  $PY tools/panel_vision/calibra.py sembrar-n1 [--n1 ~/Laminillas-N1] [--n 30] [--lado 512]   # → <n1>/panel_vision/conjunto_n1
  python3 tools/panel_vision/calibra.py uso      --dir <dir> | --fichero <calibracion_sintetica.json>
  $PY tools/panel_vision/calibra.py revisar  --modelo ollama:<m> [--n1 ~/Laminillas-N1] [--calibracion <calibracion_n1.json>]
  python3 tools/panel_vision/calibra.py a-n1     --dir <dir>   # PNG del conjunto → N1 (exporta_n1), para externos
  python3 tools/panel_vision/calibra.py correr   --dir <dir> --modelo gemini      # por vision_n1, tras a-n1
  python3 tools/panel_vision/calibra.py modelos             # Ollama local y estado de gemini, claude y medgemma
"""
import argparse
import base64
import datetime as _dt
import hashlib
import ipaddress
import json
import math
import os
import random
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request

if __name__ == "__main__":                     # un solo módulo aunque sinteticos haga `import calibra`
    sys.modules.setdefault("calibra", sys.modules["__main__"])

PROTOCOLO = "panel-vision/1"
CUADRANTES = ("superior_izquierdo", "superior_derecho", "inferior_izquierdo", "inferior_derecho")
TIPOS = {
    "nucleos": ("omitido", "fusionado", "fuera_de_nucleo"),
    "ck19": ("estroma_incluido", "epitelio_excluido"),
    "registro": ("desalineacion",),
    "figura": ("etiqueta", "escala", "leyenda"),
    "artefacto": ("pliegue", "desenfoque", "burbuja"),
}
TAREAS = tuple(TIPOS)
UMBRAL = {"sensibilidad": 0.8, "especificidad": 0.8, "ic_inferior": 0.6, "n_min": 30}
Z95 = 1.959963984540054
SEMILLA = 20261001
CLAVES = ("hay_error", "tipo", "cuadrante")

# ── preguntas (en inglés: rinden mejor en los VLM pequeños; claves y valores son códigos) ─────
_RESPUESTA = (
    "Reply with exactly one JSON object and nothing else, with exactly these three keys:\n"
    '{{"hay_error": true or false, "tipo": {tipos} or null, "cuadrante": one of "superior_izquierdo", '
    '"superior_derecho", "inferior_izquierdo", "inferior_derecho", or null}}\n'
    "superior = top half of the image, inferior = bottom half, izquierdo = left half, derecho = right half. "
    "The image contains at most one error. If there is no error: hay_error false, tipo null, cuadrante null. "
    "If there is one: hay_error true, its tipo, and the quadrant that contains it.")

_TAREA_TXT = {
    "nucleos": (
        "You are checking an automatic nuclear segmentation drawn over a haematoxylin and eosin (H&E) "
        "image at 0.5 micrometres per pixel. Each nucleus should be enclosed by its own thin amber "
        "(yellow-orange) outline. Possible errors: \"omitido\" = a group of nuclei with no outline; "
        "\"fusionado\" = a single outline enclosing two or more adjacent nuclei; \"fuera_de_nucleo\" = "
        "outlines drawn where there is no nucleus (on stroma)."),
    "ck19": (
        "You are checking an automatic epithelial mask drawn over a CK19 immunohistochemistry image at "
        "0.5 micrometres per pixel: brown (DAB) = CK19-positive epithelium, pale blue = haematoxylin "
        "counterstain. The magenta outline should enclose the brown epithelial nests and nothing else. "
        "Possible errors: \"estroma_incluido\" = the mask encloses an area of stroma that is not brown; "
        "\"epitelio_excluido\" = a brown epithelial nest is left outside the mask."),
    "registro": (
        "You are checking the registration of two serial sections of the same tissue, shown as a colour "
        "overlay at {mpp:g} micrometres per pixel: section A is yellow, section B is violet, and where both "
        "coincide the tissue looks greyish mauve-brown. The grey bar at the bottom centre is 100 micrometres long. "
        "Offsets below 50 micrometres are acceptable. Possible error: \"desalineacion\" = a tissue fragment "
        "whose two sections are offset by more than 50 micrometres (a yellow copy and a violet copy "
        "visibly shifted apart)."),
    "figura": (
        "You are checking a report figure against its specification. Specification: the marker label must "
        "read \"{marcador}\"; the image is shown at {mpp:g} micrometres per pixel, so the scale bar labelled "
        "\"{escala_um} um\" must be {escala_px} pixels long; the legend must say red = pos (positive nuclei) "
        "and blue = neg (negative nuclei). Possible errors: \"etiqueta\" = the marker label differs from the "
        "specification; \"escala\" = the scale bar length does not match its label (off by 30% or more); "
        "\"leyenda\" = the legend colours or words do not match the specification."),
    "artefacto": (
        "You are checking an automatic artefact detector on an H&E image at 0.5 micrometres per pixel. "
        "Every artefact it detected is outlined in orange. An artefact that is outlined is NOT an error. "
        "Possible errors (an artefact present but NOT outlined): \"pliegue\" = tissue fold (a darker band "
        "where the tissue is doubled over); \"desenfoque\" = out-of-focus (blurred) area; \"burbuja\" = air "
        "bubble (a round area with a dark rim)."),
}
CONTEXTO = {"nucleos": (), "ck19": (), "registro": ("mpp",), "figura": ("marcador", "mpp", "escala_um", "escala_px"),
            "artefacto": ()}


def _tarea(tarea):
    if tarea not in TIPOS:
        raise ValueError("tarea desconocida: %r (son %s)" % (tarea, ", ".join(TAREAS)))
    return tarea


def pregunta(tarea, contexto=None):
    """El texto que ve el modelo. `contexto` solo lleva lo que la tarea declara en CONTEXTO."""
    _tarea(tarea)
    contexto = dict(contexto or {})
    if set(contexto) != set(CONTEXTO[tarea]):
        raise ValueError("contexto de %s: se esperan %s, llegan %s" % (tarea, sorted(CONTEXTO[tarea]), sorted(contexto)))
    tipos = TIPOS[tarea]
    t = ('exactly "%s"' % tipos[0]) if len(tipos) == 1 else "one of " + ", ".join('"%s"' % x for x in tipos)
    return _TAREA_TXT[tarea].format(**contexto) + "\n\n" + _RESPUESTA.format(tipos=t)


def esquema_respuesta(tarea):
    """JSON Schema cerrado de la respuesta (Ollama lo usa como `format`; los demás, como salida
    estructurada cuando existan)."""
    return {"type": "object",
            "properties": {"hay_error": {"type": "boolean"},
                           "tipo": {"type": ["string", "null"], "enum": list(TIPOS[_tarea(tarea)]) + [None]},
                           "cuadrante": {"type": ["string", "null"], "enum": list(CUADRANTES) + [None]}},
            "required": list(CLAVES), "additionalProperties": False}


def _canon(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _sha(texto):
    return hashlib.sha256(texto.encode("utf-8") if isinstance(texto, str) else texto).hexdigest()


def huella_preguntas():
    """Huella del protocolo de pregunta: plantillas, vocabulario y esquema. Cambia uno, cambia ella,
    y toda calibración anterior deja de autorizar."""
    return _sha(_canon({"protocolo": PROTOCOLO, "respuesta": _RESPUESTA, "tareas": _TAREA_TXT,
                        "contexto": {k: list(v) for k, v in CONTEXTO.items()},
                        "tipos": {k: list(v) for k, v in TIPOS.items()}, "cuadrantes": list(CUADRANTES)}))


# ── respuesta ─────────────────────────────────────────────────────────────────────────────
_RE_PIENSA = re.compile(r"<think>.*?</think>", re.S | re.I)
_RE_VALLA = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.S | re.I)


def interpreta(texto, tarea):
    """(respuesta, None) si `texto` es una respuesta cerrada válida para `tarea`; (None, motivo) si no."""
    _tarea(tarea)
    if not isinstance(texto, str):
        return None, "no es texto"
    t = _RE_PIENSA.sub("", texto).strip()
    m = _RE_VALLA.match(t)
    if m:
        t = m.group(1).strip()
    try:
        obj = json.loads(t)
    except ValueError:
        return None, "no es JSON"
    if not isinstance(obj, dict):
        return None, "no es un objeto JSON"
    if set(obj) != set(CLAVES):
        return None, "claves %s (se esperan exactamente %s)" % (sorted(obj), list(CLAVES))
    h, tipo, cuad = obj["hay_error"], obj["tipo"], obj["cuadrante"]
    if not isinstance(h, bool):
        return None, "hay_error no es booleano"
    if h:
        if tipo not in TIPOS[tarea]:
            return None, "tipo fuera del vocabulario de %s" % tarea
        if cuad not in CUADRANTES:
            return None, "cuadrante fuera del vocabulario"
    elif tipo is not None or cuad is not None:
        return None, "sin error, tipo y cuadrante tienen que ser null"
    return {"hay_error": h, "tipo": tipo, "cuadrante": cuad}, None


# ── estadística y criterio ─────────────────────────────────────────────────────────────────
def wilson(k, n, z=Z95):
    """IC de Wilson para k/n. n=0 → (0, 1): sin datos no se sabe nada."""
    if n <= 0:
        return 0.0, 1.0
    p = k / n
    den = 1.0 + z * z / n
    centro = (p + z * z / (2 * n)) / den
    medio = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    lo = 0.0 if k == 0 else max(0.0, centro - medio)
    hi = 1.0 if k == n else min(1.0, centro + medio)
    return lo, hi


def _proporcion(k, n):
    lo, hi = wilson(k, n)
    return {"k": k, "n": n, "p": (k / n) if n else None, "ic95": [round(lo, 4), round(hi, 4)]}


def decide(m, umbral=None):
    """{"usar": bool, "motivos": [...]} para UN modelo en UNA tarea, desde sus RECUENTOS."""
    u = dict(UMBRAL, **(umbral or {}))
    motivos = []
    for nombre, k, n in (("sensibilidad", m["vp"], m["n_error"]), ("especificidad", m["vn"], m["n_control"])):
        casos = "casos con error" if nombre == "sensibilidad" else "controles"
        if n < u["n_min"]:
            motivos.append("%d %s < %d" % (n, casos, u["n_min"]))
        p = k / n if n else 0.0
        lo, _hi = wilson(k, n)
        if p < u[nombre]:
            motivos.append("%s %.3f < %.2f" % (nombre, p, u[nombre]))
        if lo < u["ic_inferior"]:
            motivos.append("IC95 inferior de %s %.3f < %.2f" % (nombre, lo, u["ic_inferior"]))
    return {"usar": not motivos, "motivos": motivos}


def puntua(verdades, respuestas, tarea, umbral=None):
    """Métricas de UN modelo en UNA tarea. `verdades`: {id: verdad}; `respuestas`: {id: registro}
    con "resp" (dict o None) o "error". Lo que falta, es inválido o falló cuenta como fallo."""
    m = {"n_error": 0, "n_control": 0, "vp": 0, "vp_mal_cuadrante": 0, "fn": 0, "vn": 0, "fp": 0,
         "invalidas": 0, "sin_respuesta": 0, "errores_adaptador": 0, "tipo_ok": 0}
    por_tipo, por_variante, magnitudes = {}, {}, []
    for cid, v in sorted(verdades.items()):
        if v["tarea"] != tarea:
            continue
        reg = respuestas.get(cid)
        resp = None
        if reg is None:
            m["sin_respuesta"] += 1
        elif reg.get("error"):
            m["errores_adaptador"] += 1
        elif reg.get("resp") is None:
            m["invalidas"] += 1
        else:
            resp = reg["resp"]
        if v["hay_error"]:
            m["n_error"] += 1
            t = por_tipo.setdefault(v["tipo"], {"n": 0, "vp": 0})
            t["n"] += 1
            ok = bool(resp and resp["hay_error"] and resp["cuadrante"] == v["cuadrante"])
            if ok:
                m["vp"] += 1
                t["vp"] += 1
                m["tipo_ok"] += resp["tipo"] == v["tipo"]
            else:
                m["fn"] += 1
                m["vp_mal_cuadrante"] += bool(resp and resp["hay_error"])
            magnitudes.append((v["tipo"], v.get("magnitud"), v.get("unidad"), ok))
        else:
            m["n_control"] += 1
            pv = por_variante.setdefault(v.get("variante") or "correcto", {"n": 0, "vn": 0})
            pv["n"] += 1
            if resp is not None and resp["hay_error"] is False:
                m["vn"] += 1
                pv["vn"] += 1
            else:
                m["fp"] += 1
    m["sensibilidad"] = _proporcion(m["vp"], m["n_error"])
    m["sensibilidad_laxa"] = _proporcion(m["vp"] + m["vp_mal_cuadrante"], m["n_error"])
    m["especificidad"] = _proporcion(m["vn"], m["n_control"])
    m["acierto_tipo"] = round(m["tipo_ok"] / m["vp"], 4) if m["vp"] else None
    m["por_tipo"] = {t: dict(d, sensibilidad=_proporcion(d["vp"], d["n"])) for t, d in sorted(por_tipo.items())}
    m["por_variante_control"] = {k: dict(d, especificidad=_proporcion(d["vn"], d["n"]))
                                 for k, d in sorted(por_variante.items())}
    m["por_magnitud"] = _por_magnitud(magnitudes)
    m["decision"] = decide(m, umbral)
    return m


def _por_magnitud(filas):
    """Sensibilidad por tercil de magnitud dentro de cada tipo (informativo: dónde deja de ver)."""
    out = {}
    for tipo in sorted({f[0] for f in filas}):
        xs = sorted((f[1], f[3]) for f in filas if f[0] == tipo and isinstance(f[1], (int, float)))
        if len(xs) < 3:
            continue
        unidad = next(f[2] for f in filas if f[0] == tipo)
        tramos = []
        for i in range(3):
            parte = xs[i * len(xs) // 3:(i + 1) * len(xs) // 3]
            tramos.append({"desde": parte[0][0], "hasta": parte[-1][0], "n": len(parte),
                           "detectados": sum(ok for _x, ok in parte)})
        out[tipo] = {"unidad": unidad, "terciles": tramos}
    return out


# ── conjunto ────────────────────────────────────────────────────────────────────────────────
def _semilla_caso(semilla, tarea, clase, i):
    return int(_sha("%s|%s|%s|%d" % (semilla, tarea, clase, i))[:8], 16)


def _id_caso(semilla, tarea, clase, i):
    return _sha("%s|%s|%s|%d|id" % (semilla, tarea, clase, i))[:12]


def plan_conjunto(n, semilla=SEMILLA, tareas=TAREAS):
    """[{tarea, clase, tipo, cuadrante, variante, semilla, id}]: n errores y n controles por tarea.
    Errores: bloques barajados de todas las combinaciones tipo×cuadrante (equilibrio ±1 bloque).
    Controles de artefacto: mitad «limpio», mitad «marcado» (artefacto presente y contorneado)."""
    rnd = random.Random(semilla)
    plan = []
    for tarea in tareas:
        _tarea(tarea)
        combos = [(t, q) for t in TIPOS[tarea] for q in CUADRANTES]
        errores = []
        while len(errores) < n:
            bloque = combos[:]
            rnd.shuffle(bloque)
            errores += bloque
        for i, (t, q) in enumerate(errores[:n]):
            plan.append({"tarea": tarea, "clase": "error", "tipo": t, "cuadrante": q, "variante": None,
                         "semilla": _semilla_caso(semilla, tarea, "error", i), "id": _id_caso(semilla, tarea, "error", i)})
        marcados = []
        while len(marcados) < n:
            bloque = combos[:]
            rnd.shuffle(bloque)
            marcados += bloque
        for i in range(n):
            if tarea == "artefacto" and i % 2 == 1:
                t, q, var = marcados[i][0], marcados[i][1], "marcado"
            else:
                t, q, var = None, None, ("limpio" if tarea == "artefacto" else None)
            plan.append({"tarea": tarea, "clase": "control", "tipo": t, "cuadrante": q, "variante": var,
                         "semilla": _semilla_caso(semilla, tarea, "control", i),
                         "id": _id_caso(semilla, tarea, "control", i)})
    rnd.shuffle(plan)
    return plan


def _huella_conjunto(casos):
    return _sha(_canon(sorted([c["id"], c["sha256"], c["prompt"], c["verdad"]] for c in casos)))


def genera_conjunto(dir_, n=30, semilla=SEMILLA, lado=768, tareas=TAREAS, avisa=None):
    """Escribe el conjunto en `dir_` (que no puede tener ya un manifiesto). Devuelve el manifiesto."""
    import sinteticos as S
    S.valida_lado(lado)
    if n < 1:
        raise ValueError("n tiene que ser ≥1")
    if os.path.exists(os.path.join(dir_, "manifiesto.json")):
        raise FileExistsError("%s ya tiene un conjunto: usa otro directorio" % dir_)
    os.makedirs(os.path.join(dir_, "casos"), exist_ok=True)
    casos = []
    plan = plan_conjunto(n, semilla, tareas)
    for k, p in enumerate(plan, 1):
        for intento in range(20):                 # semilla sin sitio para el error: la siguiente, fija
            semilla_c = (p["semilla"] + intento * 7919) % (2 ** 32)
            try:
                c = S.genera_caso(p["tarea"], semilla_c, lado, p["tipo"], p["cuadrante"], p["variante"])
                break
            except RuntimeError:
                continue
        else:
            raise RuntimeError("caso %s (%s): ninguna de 20 semillas deja sitio al error" % (p["id"], p["tarea"]))
        datos = S.png_bytes(c["rgb"])
        ruta = os.path.join("casos", p["id"] + ".png")
        with open(os.path.join(dir_, ruta), "wb") as fh:
            fh.write(datos)
        verdad = dict(c["verdad"], semilla=semilla_c)
        casos.append({"id": p["id"], "tarea": p["tarea"], "png": ruta, "sha256": _sha(datos),
                      "prompt": pregunta(p["tarea"], c["contexto"]), "verdad": verdad})
        if avisa and (k % 25 == 0 or k == len(plan)):
            avisa("  %d/%d casos" % (k, len(plan)))
    with open(os.path.join(dir_, "preguntas.jsonl"), "w", encoding="utf-8") as fh:
        for c in casos:
            fh.write(_canon({"id": c["id"], "tarea": c["tarea"], "png": c["png"], "prompt": c["prompt"]}) + "\n")
    with open(os.path.join(dir_, "verdad.json"), "w", encoding="utf-8") as fh:
        json.dump({c["id"]: c["verdad"] for c in casos}, fh, ensure_ascii=False, indent=1, sort_keys=True)
    man = {"protocolo": PROTOCOLO, "huella_preguntas": huella_preguntas(), "semilla": semilla, "lado": lado,
           "n_por_clase": n, "tareas": list(tareas), "casos": {c["id"]: c["sha256"] for c in casos},
           "sha_conjunto": _huella_conjunto(casos), "creado": _ahora()}
    with open(os.path.join(dir_, "manifiesto.json"), "w", encoding="utf-8") as fh:
        json.dump(man, fh, ensure_ascii=False, indent=1, sort_keys=True)
    return man


def _ahora():
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _lee_jsonl(ruta):
    if not os.path.exists(ruta):
        return []
    out = []
    with open(ruta, encoding="utf-8") as fh:
        for ln in fh:
            ln = ln.strip()
            if ln:
                out.append(json.loads(ln))
    return out


def carga_conjunto(dir_, verifica_png=True):
    """(manifiesto, preguntas, verdades), comprobando que nada se ha tocado desde `generar`."""
    with open(os.path.join(dir_, "manifiesto.json"), encoding="utf-8") as fh:
        man = json.load(fh)
    preg = _lee_jsonl(os.path.join(dir_, "preguntas.jsonl"))
    with open(os.path.join(dir_, "verdad.json"), encoding="utf-8") as fh:
        verd = json.load(fh)
    if set(man["casos"]) != {p["id"] for p in preg} or set(verd) != set(man["casos"]):
        raise ValueError("conjunto incoherente: manifiesto, preguntas y verdad no tienen los mismos casos")
    if verifica_png:
        for p in preg:
            with open(os.path.join(dir_, p["png"]), "rb") as fh:
                if _sha(fh.read()) != man["casos"][p["id"]]:
                    raise ValueError("el PNG del caso %s no es el del manifiesto" % p["id"])
    casos = [{"id": p["id"], "sha256": man["casos"][p["id"]], "prompt": p["prompt"], "verdad": verd[p["id"]]}
             for p in preg]
    if _huella_conjunto(casos) != man["sha_conjunto"]:
        raise ValueError("la huella del conjunto no cuadra con el manifiesto (preguntas o verdad tocadas)")
    return man, preg, verd


# ── adaptadores ─────────────────────────────────────────────────────────────────────────────
class AdaptadorPendiente(NotImplementedError):
    """El proveedor no tiene adaptador por la boca autorizada (`vision_n1`). No se envía nada."""


class NoLocal(RuntimeError):
    """El destino no es local: este arnés no envía nada fuera del Mac por su cuenta."""


class SinVision(RuntimeError):
    """El modelo no declara entrada de imagen."""


class SinMemoria(RuntimeError):
    """Una hora esperando memoria libre (o a que se descargue otro modelo grande): se para."""


class SinTranscripcion(LookupError):
    """El caso no está en el fichero del evaluador: se salta (no se anota ni cuenta como nueva)."""


class ProveedorNoListo(AdaptadorPendiente):
    """El adaptador existe, pero una guarda de `vision_n1` (condiciones de la auditoría, confianza
    tecleada, Puerta…) no deja enviar. No se ha enviado nada; para la pasada entera."""


class FueraDeN1(AdaptadorPendiente):
    """La imagen no está en N1 (ni mapeada por `a-n1`): a un proveedor externo no va."""


PROVEEDORES_EXTERNOS = ("claude", "gemini")
CONECTADOS = ("claude", "gemini")   # con adaptador escrito en tools/vision_n1.py (claude: 8-oct-26)
MODELO_EXTERNO_DEFECTO = {"claude": "claude-opus-5-5", "gemini": "gemini-3.1-pro-preview"}
N1_MAPA = "n1.json"
_RE_MPP_PREGUNTA = re.compile(r"\bat ([0-9]+(?:\.[0-9]+)?) micrometres per pixel")
MODELOS_APAGADOS = {"gemini-3-pro-preview": "apagado el 9-mar-2026; el vigente es gemini-3.1-pro-preview",
                    "gemini-3-pro": "apagado el 9-mar-2026; el vigente es gemini-3.1-pro-preview"}
OLLAMA_DEFECTO = "http://127.0.0.1:11434"
_RE_NUBE = re.compile(r"(?:^|[-:/_.])cloud(?:$|[-:/_.])", re.I)
MEMORIA_LIBRE_MIN = 25              # % libre de `memory_pressure` antes de cada lote local
GRANDE_BYTES = 3 * 10 ** 9          # «modelo grande» cargado en Ollama: los 8-9B (llama3.2:3b, 2 GB, no)
ESPERA_S, ESPERA_MAX_S = 300, 3600
LOTE_OLLAMA = 10


def pct_libre(texto):
    """El «System-wide memory free percentage» de la salida de `memory_pressure`; SinMemoria si no está."""
    m = re.search(r"System-wide memory free percentage:\s*(\d+)\s*%", texto or "")
    if not m:
        raise SinMemoria("memory_pressure no da el porcentaje libre: no se sabe si cabe el modelo")
    return int(m.group(1))


def memoria_libre_pct():
    return pct_libre(subprocess.run(["memory_pressure"], capture_output=True, text=True, timeout=120).stdout)


def espera_recursos(otros_grandes, libre=memoria_libre_pct, minimo=MEMORIA_LIBRE_MIN, cada=ESPERA_S,
                    maximo=ESPERA_MAX_S, duerme=time.sleep, avisa=None):
    """Vuelve cuando hay ≥`minimo` % libre y `otros_grandes()` está vacío; si no, mira cada `cada` s
    y a los `maximo` s lanza SinMemoria. Devuelve {"libre_pct", "esperado_s"}."""
    esperado = 0
    while True:
        pct, grandes = libre(), list(otros_grandes())
        if pct >= minimo and not grandes:
            return {"libre_pct": pct, "esperado_s": esperado}
        motivo = "%d %% libre (mínimo %d)" % (pct, minimo) if pct < minimo else \
            "otro modelo grande cargado: %s" % ", ".join(grandes)
        if esperado >= maximo:
            raise SinMemoria("tras %d min sin recursos para el lote: %s" % (esperado // 60, motivo))
        if avisa:
            avisa("  ⏸️  %s: espero %d min (llevo %d)" % (motivo, cada // 60, esperado // 60))
        duerme(cada)
        esperado += cada


def host_local(url):
    """`url` normalizada si es http a una IP de loopback; NoLocal si no."""
    p = urllib.parse.urlsplit(url or "")
    if p.scheme != "http" or p.username or p.password or p.path not in ("", "/") or p.query or p.fragment:
        raise NoLocal("host %r: solo http://<loopback>:<puerto>" % (url,))
    h = p.hostname or ""
    if h == "localhost":
        h = "127.0.0.1"
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        raise NoLocal("host %r: no es una IP de loopback literal" % (url,)) from None
    if not ip.is_loopback:
        raise NoLocal("host %r: no es loopback" % (url,))
    try:
        puerto = p.port or 11434
    except ValueError:
        raise NoLocal("host %r: puerto inválido" % (url,)) from None
    return "http://%s:%d" % ("[%s]" % h if ip.version == 6 else h, puerto)


class Ollama:
    """Modelo de visión en Ollama local. `comprueba()` antes de nada; `responder()` = una imagen,
    una pregunta, sin historial, temperatura 0 y semilla fija, salida con el esquema cerrado."""
    local = True

    def __init__(self, modelo, host=OLLAMA_DEFECTO, think=False, timeout=600, temperatura=0.0, semilla=0,
                 num_ctx=None):
        if not modelo or not isinstance(modelo, str):
            raise ValueError("falta el modelo: ollama:<modelo>")
        if _RE_NUBE.search(modelo):
            raise NoLocal("%s es un modelo «cloud» de Ollama: sale a ollama.com" % modelo)
        if num_ctx is not None and (isinstance(num_ctx, bool) or not isinstance(num_ctx, int) or num_ctx < 512):
            raise ValueError("num_ctx %r: entero ≥512" % (num_ctx,))
        self.modelo = modelo
        self.base = host_local(host)
        self.think = bool(think)
        self.timeout = timeout
        self.nombre = "ollama:" + modelo
        self.config = {"adaptador": "ollama", "modelo": modelo, "think": self.think,
                       "temperatura": float(temperatura), "semilla": int(semilla)}
        if num_ctx is not None:              # explícito: otro contexto es otra calibración (otra clave)
            self.config["num_ctx"] = num_ctx
        self._abre = urllib.request.build_opener(urllib.request.ProxyHandler({})).open
        self._piensa = None

    def _pide(self, ruta, cuerpo=None, timeout=30):
        datos = None if cuerpo is None else json.dumps(cuerpo).encode("utf-8")
        req = urllib.request.Request(self.base + ruta, data=datos, headers={"Content-Type": "application/json"})
        with self._abre(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    def comprueba(self):
        info = self._pide("/api/show", {"model": self.modelo})
        remotos = sorted(k for k, v in info.items() if k.startswith("remote") and v)
        if remotos:
            raise NoLocal("%s: Ollama lo sirve desde fuera (%s)" % (self.modelo, ", ".join(remotos)))
        caps = info.get("capabilities") or []
        if "vision" not in caps:
            raise SinVision("%s no declara visión (capabilities: %s)" % (self.modelo, ", ".join(caps) or "—"))
        self._piensa = "thinking" in caps
        det = info.get("details") or {}
        etiquetas = self._pide("/api/tags").get("models") or []
        nombres = {self.modelo, self.modelo + ":latest"}
        digest = next((m.get("digest") for m in etiquetas if m.get("name") in nombres or m.get("model") in nombres), None)
        self.config.update(digest=digest, familia=det.get("family"), parametros=det.get("parameter_size"),
                           cuantizacion=det.get("quantization_level"))
        return dict(self.config)

    def responder(self, prompt, ruta_png, esquema):
        if self._piensa is None:
            self.comprueba()
        with open(ruta_png, "rb") as fh:
            img = base64.b64encode(fh.read()).decode("ascii")
        cuerpo = {"model": self.modelo, "stream": False, "format": esquema,
                  "messages": [{"role": "user", "content": prompt, "images": [img]}],
                  "options": {"temperature": self.config["temperatura"], "seed": self.config["semilla"]}}
        if "num_ctx" in self.config:
            cuerpo["options"]["num_ctx"] = self.config["num_ctx"]
        if self._piensa:
            cuerpo["think"] = self.think
        r = self._pide("/api/chat", cuerpo, timeout=self.timeout)
        return (r.get("message") or {}).get("content") or ""

    def _nombres(self):
        return {self.modelo, self.modelo + ":latest"}

    def _cargados(self):
        return self._pide("/api/ps").get("models") or []

    def cargado(self):
        """¿Está ESTE modelo en memoria?"""
        return any((m.get("name") or m.get("model")) in self._nombres() for m in self._cargados())

    def otros_grandes(self):
        """Modelos cargados en Ollama (≥GRANDE_BYTES) que no son este."""
        return ["%s (%.1f GB)" % (m.get("name"), (m.get("size") or 0) / 1e9) for m in self._cargados()
                if (m.get("name") or m.get("model")) not in self._nombres() and (m.get("size") or 0) >= GRANDE_BYTES]

    def descarga(self):
        """Saca el modelo de memoria (keep_alive 0) para no estorbar al siguiente."""
        self._pide("/api/generate", {"model": self.modelo, "keep_alive": 0}, timeout=60)

    @staticmethod
    def lista_local(host=OLLAMA_DEFECTO):
        """[(nombre, visión, remoto)] de lo que hay en el Ollama local."""
        o = Ollama("listado", host=host)
        out = []
        for m in o._pide("/api/tags").get("models") or []:
            nombre = m.get("name") or m.get("model")
            try:
                info = o._pide("/api/show", {"model": nombre})
            except Exception:  # noqa: BLE001 — un modelo roto no tumba el listado
                out.append((nombre, None, None))
                continue
            remoto = bool(_RE_NUBE.search(nombre or "")) or any(k.startswith("remote") and v for k, v in info.items())
            out.append((nombre, "vision" in (info.get("capabilities") or []), remoto))
        return out


def _tools_dir():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _auditoria_casa_base(proveedor):
    """La entrada de `proveedor` en `tools/panel_vision/auditorias.json` de CASA BASE (la misma que
    exige vision_n1), o None. Se lee sin importar vision_n1 (que fija el estado del proceso)."""
    raiz = os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode")
    try:
        with open(os.path.join(raiz, "tools", "panel_vision", "auditorias.json"), encoding="utf-8") as fh:
            ent = (json.load(fh) or {}).get(proveedor)
    except (OSError, ValueError, AttributeError):
        return None
    return ent if isinstance(ent, dict) else None


class Externo:
    """Claude o Gemini por la boca autorizada, `tools/vision_n1.py`; este arnés no habla con
    ninguna API. Los dos están conectados (Claude, desde el 8-oct-26, con su propia entrada de
    auditoría y su propio `trust-cloud vision-n1:claude`): `comprueba()` exige las condiciones de la auditoría (casa
    base) y `vision_n1.listo` (Puerta, confianza tecleada); `responder(prompt, ruta_png, esquema)`
    lleva la imagen a su fichero N1 (la ruta misma si ya vive en N1; si no, el mapa de `a-n1`) y
    delega en `vision_n1.enviar`, que vuelve a pasar TODAS las guardas, avisa del primer envío
    (`avisar`, opt-in: lo pone el CLI), sella y llama. Una guarda que cierra es `ProveedorNoListo`
    (para la pasada); un error del proveedor se anota como error del adaptador y se reintenta."""
    local = False

    def __init__(self, proveedor, modelo="", avisar=False, transporte=None):
        if proveedor not in PROVEEDORES_EXTERNOS:
            raise ValueError("proveedor desconocido: %r" % (proveedor,))
        modelo = modelo or MODELO_EXTERNO_DEFECTO.get(proveedor, "")
        if modelo in MODELOS_APAGADOS:
            raise ValueError("%s: %s" % (modelo, MODELOS_APAGADOS[modelo]))
        if proveedor in MODELO_EXTERNO_DEFECTO and modelo != MODELO_EXTERNO_DEFECTO[proveedor]:
            raise ValueError("%s: el modelo está fijado en %s (el de su adaptador en vision_n1)" % (
                proveedor, MODELO_EXTERNO_DEFECTO[proveedor]))
        self.proveedor, self.modelo = proveedor, modelo
        self.avisar, self.transporte = bool(avisar), transporte
        self.destino = "vision-n1:" + proveedor
        self.nombre = proveedor + (":" + modelo if modelo else "")
        self.config = {"adaptador": "vision_n1", "destino": self.destino, "modelo": self.modelo}
        self._v = None

    def _pendiente(self):
        return AdaptadorPendiente(
            "%s: tools/vision_n1.py no tiene adaptador para este proveedor, y este arnés no habla con "
            "ninguna API externa por su cuenta. Haría falta: el adaptador en vision_n1, su auditoría de "
            "acceso-herramientas y legal-burocracia (sin entrenar con los datos, API de pago, retención) "
            "y el paso 6 tecleado por {{TITULAR}}." % self.destino)

    def _vision(self):
        if self._v is None:
            if _tools_dir() not in sys.path:
                sys.path.insert(0, _tools_dir())
            import vision_n1                       # fija el estado de casa base del proceso
            self._v = vision_n1
        return self._v

    def comprueba(self):
        if self.proveedor not in CONECTADOS:
            raise self._pendiente()
        aud = _auditoria_casa_base(self.proveedor) or {}
        if str(aud.get("veredicto", "")).strip().lower() not in ("apto", "apto con condiciones") or \
                aud.get("modelo") != self.modelo or aud.get("condiciones_cumplidas") is not True:
            raise ProveedorNoListo(
                "%s: la auditoría de acceso-herramientas y legal-burocracia (tools/panel_vision/"
                "auditorias.json de casa base) no tiene condiciones_cumplidas: true para %s; vision_n1 "
                "no enviaría. No se envía nada." % (self.destino, self.modelo))
        v = self._vision()
        try:
            r = v.listo(self.destino, self.modelo)
        except v.PuertaCerrada as e:
            raise ProveedorNoListo("%s no está listo: %s" % (self.destino, e))
        self.config.update(r["config"])
        return dict(self.config)

    def _ruta_n1(self, ruta_png):
        """La ruta N1 de la imagen: ella misma si vive en N1; si no, la que dejó `a-n1`."""
        v = self._vision()
        real = os.path.realpath(ruta_png)
        n1 = v.puerta.n1_dir()
        if os.path.dirname(real) == n1:
            return real
        dir_ = os.path.dirname(os.path.dirname(real))
        try:
            with open(os.path.join(dir_, N1_MAPA), encoding="utf-8") as fh:
                mapa = json.load(fh)
        except (OSError, ValueError):
            mapa = {}
        ent = (mapa.get("casos") or {}).get(os.path.splitext(os.path.basename(real))[0]) \
            if isinstance(mapa, dict) and mapa.get("n1") == n1 else None
        if not isinstance(ent, dict):
            raise FueraDeN1("%s no está en N1: pasa el conjunto con `calibra.py a-n1 --dir %s` "
                            "(exporta_n1) antes de preguntar a %s" % (os.path.basename(real), dir_, self.destino))
        with open(real, "rb") as fh:
            if _sha(fh.read()) != ent.get("sha256_caso"):
                raise FueraDeN1("%s no es el PNG que se pasó a N1" % os.path.basename(real))
        man = v.puerta.cargar_manifiesto()["ficheros"].get(ent.get("n1")) or {}
        if man.get("sha256") != ent.get("sha256_n1"):
            raise FueraDeN1("%s: su fichero N1 no es el que dejó a-n1 (manifiesto distinto)" % os.path.basename(real))
        return os.path.join(n1, ent["n1"])

    def responder(self, prompt, ruta_png, esquema):
        if self.proveedor not in CONECTADOS:
            raise self._pendiente()
        v = self._vision()
        ruta = self._ruta_n1(ruta_png)
        try:
            return v.enviar(self.destino, self.modelo, prompt, [ruta], avisar=self.avisar, esquema=esquema,
                            transporte=self.transporte, verboso=False)
        except v.PuertaCerrada as e:
            raise ProveedorNoListo("vision_n1 no deja enviar %s: %s" % (os.path.basename(ruta), e))


def _opacos_cal_usados(man_n1):
    out = set()
    for nombre in (man_n1.get("ficheros") or {}):
        m = re.match(r"^CAL-(\d{5})__", nombre)
        if m:
            out.add(int(m.group(1)))
    return out


def a_n1(dir_, avisa=None):
    """Pasa los PNG de un conjunto de calibración a `~/Laminillas-N1/` por `exporta_n1.exporta_png`
    (la ÚNICA boca que escribe en N1: nombre opaco `CAL-<5 cifras>`, OCR + Puerta + trazos,
    manifiesto), comprueba que los píxeles exportados son los del caso y deja `<dir>/n1.json`
    {"n1", "sha_conjunto", "casos": {id: {"n1", "sha256_n1", "sha256_caso"}}}. Reanudable. El mpp
    del nombre es el que la pregunta le dice al modelo. Una imagen rechazada PARA (no se salta)."""
    from PIL import Image
    if _tools_dir() not in sys.path:
        sys.path.insert(0, _tools_dir())
    import exporta_n1
    man, preg, _verd = carga_conjunto(dir_)
    exporta_n1.puerta.exigir_diccionario()
    n1 = exporta_n1.puerta.n1_dir()
    ruta_mapa = os.path.join(dir_, N1_MAPA)
    try:
        with open(ruta_mapa, encoding="utf-8") as fh:
            mapa = json.load(fh)
    except (OSError, ValueError):
        mapa = {}
    if not isinstance(mapa, dict) or mapa.get("n1") != n1 or mapa.get("sha_conjunto") != man["sha_conjunto"]:
        mapa = {"n1": n1, "sha_conjunto": man["sha_conjunto"], "casos": {}}
    hechos = nuevos = 0
    for p in sorted(preg, key=lambda x: x["id"]):
        man_n1 = exporta_n1.puerta.cargar_manifiesto()
        ent = mapa["casos"].get(p["id"])
        if isinstance(ent, dict) and (man_n1["ficheros"].get(ent.get("n1")) or {}).get("sha256") == ent.get("sha256_n1") \
                and os.path.exists(os.path.join(n1, ent["n1"])):
            hechos += 1
            continue
        m = _RE_MPP_PREGUNTA.search(p["prompt"])
        if not m:
            raise ValueError("caso %s: la pregunta no dice el mpp" % p["id"])
        usados = _opacos_cal_usados(man_n1)
        opaco = "CAL-%05d" % ((max(usados) + 1) if usados else 1)
        src = os.path.join(dir_, p["png"])
        destino, ent_n1 = exporta_n1.exporta_png(src, opaco, "L0", float(m.group(1)))
        with Image.open(src) as a, Image.open(destino) as b:
            if a.convert("RGB").tobytes() != b.convert("RGB").tobytes():
                raise ValueError("caso %s: el PNG exportado a N1 no tiene los mismos píxeles" % p["id"])
        mapa["casos"][p["id"]] = {"n1": os.path.basename(destino), "sha256_n1": ent_n1["sha256"],
                                  "sha256_caso": man["casos"][p["id"]]}
        with open(ruta_mapa + ".tmp", "w", encoding="utf-8") as fh:
            json.dump(mapa, fh, ensure_ascii=False, indent=1, sort_keys=True)
        os.replace(ruta_mapa + ".tmp", ruta_mapa)
        nuevos += 1
        if avisa and nuevos % 25 == 0:
            avisa("  %d casos en N1" % nuevos)
    return {"n1": n1, "nuevos": nuevos, "ya_estaban": hechos, "mapa": ruta_mapa}


def estado_proveedores():
    """{nombre: estado} de los modelos no-Ollama del panel, sin enviar nada."""
    out = {}
    for p in PROVEEDORES_EXTERNOS:
        try:
            Externo(p).comprueba()
            out[p] = "listo (vision_n1)"
        except AdaptadorPendiente as e:
            out[p] = "pendiente: %s" % str(e)[:160]
    try:
        import medgemma_local
        est, motivo = medgemma_local.estado()
        out["medgemma"] = "%s: %s" % (est, motivo)
    except Exception as e:  # noqa: BLE001 — el listado no se cae por un módulo
        out["medgemma"] = "pendiente de acceso (%s)" % type(e).__name__
    return out


class Transcrito:
    """Respuestas que un evaluador escribió mirando los PNG fuera del arnés (JSONL {"id", "crudo"}).
    `responder` devuelve el crudo de ESE caso; si no está, SinTranscripcion (se salta). El nombre y
    `evaluador` (quién, cómo vio las imágenes y que no abrió verdad.json) quedan en la config."""
    local = True

    def __init__(self, nombre, respuestas, evaluador, ciega=False):
        if not nombre or not re.fullmatch(r"[A-Za-z0-9._-]+", nombre):
            raise ValueError("transcrito:<nombre> (letras, cifras, punto, guion)")
        if not evaluador or not evaluador.strip():
            raise ValueError("transcrito: falta --evaluador (quién respondió, cómo vio las imágenes, a ciegas)")
        if not respuestas or not os.path.exists(respuestas):
            raise ValueError("transcrito: no existe el fichero de respuestas %r" % (respuestas,))
        self.ruta = respuestas
        self.nombre = "transcrito:" + nombre
        self.config = {"adaptador": "transcrito", "nombre": nombre, "evaluador": evaluador.strip()}
        if ciega:                    # sin esto, la calibración de un transcrito no habilita (excluido())
            self.config["ciega"] = True

    def comprueba(self):
        self._crudos = {}
        for r in _lee_jsonl(self.ruta):
            if set(r) != {"id", "crudo"} or not isinstance(r["crudo"], str):
                raise ValueError("transcrito: cada línea es {\"id\", \"crudo\"} y nada más: %r" % (r,))
            self._crudos[r["id"]] = r["crudo"]            # la última de un id manda
        return dict(self.config)

    def responder(self, prompt, ruta_png, esquema):
        cid = os.path.splitext(os.path.basename(ruta_png))[0]
        if cid not in self._crudos:
            raise SinTranscripcion(cid)
        return self._crudos[cid]


def adaptador(spec, **kw):
    """`ollama:<modelo>` | `claude[:<modelo>]` | `gemini[:<modelo>]` (con avisar=, transporte=) |
    `medgemma[:<modelo>]` (local) | `transcrito:<nombre>` (con respuestas= y evaluador=)."""
    prov, _, modelo = (spec or "").partition(":")
    if prov == "ollama":
        return Ollama(modelo, **kw)
    if prov == "transcrito":
        return Transcrito(modelo, **kw)
    if prov in PROVEEDORES_EXTERNOS:
        return Externo(prov, modelo, **kw)
    if prov == "medgemma":
        import medgemma_local
        return medgemma_local.MedGemmaLocal(modelo, **kw)
    raise ValueError("modelo %r: usa ollama:<modelo>, transcrito:<nombre>, medgemma, %s" % (
        spec, ", ".join(p + ":<modelo>" for p in PROVEEDORES_EXTERNOS)))


def _clave_modelo(nombre, config):
    return "%s@%s" % (nombre, _sha(_canon(config))[:8])


def _fichero_respuestas(dir_, clave):
    seguro = re.sub(r"[^A-Za-z0-9._@-]+", "_", clave)
    return os.path.join(dir_, "respuestas", seguro + ".jsonl")


# ── correr y puntuar ────────────────────────────────────────────────────────────────────────
def correr(dir_, adapt, tareas=None, limite=None, avisa=None, max_seguidos=3, lote=None, recursos=None):
    """Pregunta al modelo los casos que le faltan (reanudable). Un error del adaptador se anota y se
    reintenta en la siguiente pasada; `max_seguidos` errores seguidos paran la pasada. Con `lote` y
    `recursos`, `recursos()` se llama antes de cada lote de `lote` casos (y puede lanzar SinMemoria:
    lo hecho queda anotado y la pasada se reanuda después)."""
    man, preg, _verd = carga_conjunto(dir_)
    if man.get("protocolo") != PROTOCOLO or man.get("huella_preguntas") != huella_preguntas():
        raise ValueError("el conjunto es de otro protocolo de pregunta: genera uno nuevo")
    for t in tareas or ():
        _tarea(t)
    config = adapt.comprueba()               # Externo lanza aquí, antes de escribir nada
    clave = _clave_modelo(adapt.nombre, config)
    ruta = _fichero_respuestas(dir_, clave)
    hechas = {r["id"] for r in _lee_jsonl(ruta)
              if r.get("sha_conjunto") == man["sha_conjunto"] and not r.get("error")}
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    nuevas = seguidos = 0
    intentadas_lote = -1
    with open(ruta, "a", encoding="utf-8") as fh:
        for p in preg:
            if (tareas and p["tarea"] not in tareas) or p["id"] in hechas:
                continue
            if limite is not None and nuevas >= limite:
                break
            if recursos is not None and lote and nuevas % lote == 0 and nuevas != intentadas_lote:
                r_ = recursos()
                intentadas_lote = nuevas
                if avisa:
                    avisa("  lote desde la %d: %s %% libre" % (nuevas + 1, r_.get("libre_pct")))
            reg = {"id": p["id"], "tarea": p["tarea"], "modelo": adapt.nombre, "clave": clave, "config": config,
                   "sha_conjunto": man["sha_conjunto"], "huella_preguntas": man["huella_preguntas"], "ts": _ahora()}
            t0 = time.monotonic()
            try:
                crudo = adapt.responder(p["prompt"], os.path.join(dir_, p["png"]), esquema_respuesta(p["tarea"]))
            except SinTranscripcion:
                continue
            except (AdaptadorPendiente, NoLocal, SinVision):
                raise
            except Exception as e:  # noqa: BLE001 — se anota y cuenta como fallo si no se repite
                reg["error"] = "%s: %s" % (type(e).__name__, str(e)[:300])
                seguidos += 1
            else:
                seguidos = 0
                resp, motivo = interpreta(crudo, p["tarea"])
                reg.update(crudo=crudo[:4000], resp=resp, motivo=motivo)
            reg["ms"] = int((time.monotonic() - t0) * 1000)
            fh.write(_canon(reg) + "\n")
            fh.flush()
            nuevas += 1
            if avisa:
                avisa("  %s %s %s" % (p["id"], p["tarea"], reg.get("error") or (reg.get("motivo") or "ok")))
            if seguidos >= max_seguidos:
                raise RuntimeError("%d errores seguidos del adaptador: paro (anotados en %s)" % (seguidos, ruta))
    return {"ruta": ruta, "clave": clave, "nuevas": nuevas}


def _respuestas_por_modelo(dir_, man):
    """{clave: (último registro, {id: registro vigente})} de respuestas/*.jsonl de ESTE conjunto."""
    out = {}
    dir_r = os.path.join(dir_, "respuestas")
    for f in sorted(os.listdir(dir_r)) if os.path.isdir(dir_r) else []:
        if not f.endswith(".jsonl"):
            continue
        regs = [r for r in _lee_jsonl(os.path.join(dir_r, f)) if r.get("sha_conjunto") == man["sha_conjunto"]]
        if not regs:
            continue
        por_id = {}
        for r in regs:                            # la última respuesta buena gana; un error no pisa una buena
            if r["id"] not in por_id or not r.get("error") or por_id[r["id"]].get("error"):
                por_id[r["id"]] = r
        out[regs[-1]["clave"]] = (regs[-1], por_id)
    return out


def no_ciegas_de(pares):
    """{prefijo: motivo} de ["<clave o nombre>=<motivo>", …] (`exportar --no-ciega`)."""
    out = {}
    for p in pares or ():
        clave, sep, motivo = str(p).partition("=")
        if not sep or not clave.strip() or not motivo.strip():
            raise ValueError("--no-ciega espera <clave o nombre>=<motivo>: %r" % (p,))
        out[clave.strip()] = motivo.strip()
    return out


def puntua_conjunto(dir_, umbral=None, escribe=True, no_ciegas=None):
    """resultados.json: por modelo (clave = nombre@config) y tarea, métricas y decisión; y `uso`.
    `no_ciegas` {clave o nombre: motivo} marca calibraciones que se informan pero no habilitan."""
    man, _preg, verd = carga_conjunto(dir_)
    res = {"protocolo": PROTOCOLO, "huella_preguntas": man["huella_preguntas"], "sha_conjunto": man["sha_conjunto"],
           "umbral": dict(UMBRAL, **(umbral or {})), "modelos": {}, "puntuado": _ahora()}
    tareas = sorted({v["tarea"] for v in verd.values()})
    for clave, (ultimo, por_id) in _respuestas_por_modelo(dir_, man).items():
        res["modelos"][clave] = {"modelo": ultimo["modelo"], "config": ultimo["config"],
                                 "tareas": {t: puntua(verd, por_id, t, umbral) for t in tareas}}
    for pref, motivo in (no_ciegas or {}).items():
        marcadas = [c for c, d in res["modelos"].items() if pref in (c, d["modelo"])]
        if not marcadas:
            raise ValueError("--no-ciega %s: ningún modelo de este conjunto se llama así" % pref)
        for c in marcadas:
            res["modelos"][c]["no_ciega"] = motivo
    res["uso"] = tabla_uso(res, umbral)
    if escribe:
        with open(os.path.join(dir_, "resultados.json"), "w", encoding="utf-8") as fh:
            json.dump(res, fh, ensure_ascii=False, indent=1, sort_keys=True)
    return res


SALIDA_DEFECTO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "calibracion_sintetica.json")
LIMITES = (
    "Sintético: mide si cada modelo ve ESTOS errores en ESTE tejido inventado; no sustituye a la "
    "calibración sobre capas N1 reales con errores sembrados (punto 5-bis (i) del plan, segunda parte).",
    "Un error como mucho por imagen y entero en un cuadrante; la sensibilidad exige el cuadrante.",
    "Una imagen por petición, sin historial; ollama a temperatura 0, semilla 0 y sin «thinking».",
    "Habilitar es por tarea: un modelo habilitado en una tarea no lo está en las demás.",
)


def _compacta(reg):
    if reg is None:
        return None
    if reg.get("error"):
        return {"error": reg["error"][:200]}
    if reg.get("resp") is None:
        return {"invalida": reg.get("motivo") or "?"}
    r = reg["resp"]
    return [r["hay_error"], r["tipo"], r["cuadrante"]]


LIMITES_N1 = (
    "Capas N1 REALES del piloto con errores sembrados (siembra_n1.py): mide si cada modelo ve ESTOS errores "
    "sembrados en NUESTRAS capas; un error real puede no parecerse a uno sembrado.",
    "Los casos salen de pocas capas (manifiesto: origen.capas e imagenes_distintas): no son independientes y el "
    "IC de Wilson, que supone que lo son, sobreestima la precisión.",
    "El control es la capa del piloto tal cual (registro: con un desplazamiento ≤20 µm); si la capa ya trae un "
    "error de verdad, el control lo hereda.",
) + LIMITES[1:]


def _resumen(res, umbral=None):
    out = []
    for clave, d in sorted(res["modelos"].items()):
        fuera = excluido(d)
        for t, m in sorted(d["tareas"].items()):
            dec = decide(m, umbral)
            out.append({"modelo": clave, "tarea": t, "sensibilidad": m["sensibilidad"],
                        "especificidad": m["especificidad"], "habilitado": dec["usar"] and not fuera,
                        "motivos": dec["motivos"] + ([fuera] if fuera else [])})
    return out


def exporta_calibracion(dir_, salida=SALIDA_DEFECTO, umbral=None, notas=(), no_ciegas=None):
    """calibracion_sintetica.json (o, de un conjunto de `sembrar-n1`, calibracion_n1.json): lo de
    `puntua_conjunto` más la verdad y la respuesta de cada modelo por caso (para recalcular sin el
    conjunto) y los límites. Devuelve el dict."""
    man, _preg, verd = carga_conjunto(dir_)
    res = puntua_conjunto(dir_, umbral, escribe=False, no_ciegas=no_ciegas)
    por_modelo = _respuestas_por_modelo(dir_, man)
    campos = ("tarea", "hay_error", "tipo", "cuadrante", "magnitud", "unidad", "variante", "semilla")
    n1 = (man.get("origen") or {}).get("tipo") == "capas-n1-sembradas"
    out = dict(res)
    out.update({
        "que": ("Calibración sobre CAPAS N1 REALES del piloto con errores sembrados (plan «laminillas DFCI», "
                "punto 5-bis (i), segunda parte): tipo, posición y magnitud conocidos." if n1 else
                "Calibración SINTÉTICA del panel de visión (plan «laminillas DFCI», actualización 3 y "
                "punto 5-bis (i)): errores sembrados de tipo, posición y magnitud conocidos; ningún dato clínico."),
        "semilla": man["semilla"], "lado": man["lado"], "n_por_clase": man["n_por_clase"], "tareas": man["tareas"],
        "regenerar": man.get("regenerar") or "calibra.py generar --n %d --lado %d --semilla %d (sha_conjunto lo "
                                             "comprueba)" % (man["n_por_clase"], man["lado"], man["semilla"]),
        "resumen": _resumen(res, umbral),
        "casos": {cid: {k: v.get(k) for k in campos} for cid, v in sorted(verd.items())},
        "respuestas": {clave: {cid: _compacta(por_id.get(cid)) for cid in sorted(verd)}
                       for clave, (_u, por_id) in sorted(por_modelo.items())},
        "limites": list(LIMITES_N1 if n1 else LIMITES) + [n for n in notas if n],
        "exportado": _ahora(),
    })
    if n1:
        out["origen"] = man["origen"]
    with open(salida, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1, sort_keys=True)
        fh.write("\n")
    return out


# ── revisión de las capas N1 del piloto (contrato de `laminillas_proc tribunal-listo`) ────────
N1_PANEL = "panel_vision"
CAPAS_PILOTO = "capas_piloto.json"
REVISIONES = "revisiones.jsonl"


def _carga(obj):
    if isinstance(obj, str):
        with open(obj, encoding="utf-8") as fh:
            return json.load(fh)
    return obj


def revisa_capas(n1, calibracion, adapt, tareas=None, avisa=None, max_seguidos=3):
    """Revisa con `adapt` las capas N1 del piloto de cada tarea en la que SU clave está habilitada por
    `calibracion` (la de capas N1 reales: ruta o dict con el formato de `exportar`). Contrato con
    `laminillas_proc.tribunal_listo` (punto 5-bis (ii)):
      · lee `<n1>/panel_vision/capas_piloto.json`: {"tareas": {tarea: [fichero N1, …]}, y, para
        las tareas que lo piden (registro: mpp; figura: la especificación), "contexto":
        {fichero: {…}}};
      · cada fichero vive en `<n1>/` y su sha256 tiene que casar con `<n1>/manifiesto.json`;
      · añade a `<n1>/panel_vision/revisiones.jsonl` una línea por (clave, tarea, sha256):
        {"clave", "tarea", "fichero", "sha256", "resp", "ts"}; inválida o fallo del adaptador =
        línea con "error" (no cuenta y se reintenta). Reanudable: no repite lo ya revisado.
    La clave (nombre@configuración) tiene que ser una de las calibradas: otra configuración (otro
    digest, otro evaluador) no está calibrada y no revisa."""
    cal = _carga(calibracion)
    if cal.get("protocolo") != PROTOCOLO or cal.get("huella_preguntas") != huella_preguntas():
        raise ValueError("la calibración es de otro protocolo de pregunta: no autoriza a nadie")
    config = adapt.comprueba()
    clave = _clave_modelo(adapt.nombre, config)
    if clave not in (cal.get("modelos") or {}):
        raise ValueError("%s no está en la calibración (otra configuración = otra calibración)" % clave)
    suyas = [t for t in (tareas or TAREAS) if clave in modelos_autorizados(cal, _tarea(t))]
    dir_p = os.path.join(n1, N1_PANEL)
    with open(os.path.join(dir_p, CAPAS_PILOTO), encoding="utf-8") as fh:
        capas = json.load(fh)
    with open(os.path.join(n1, "manifiesto.json"), encoding="utf-8") as fh:
        man = json.load(fh).get("ficheros") or {}
    trabajo = []
    for t in suyas:
        for fich in (capas.get("tareas") or {}).get(t) or []:
            if os.path.basename(fich) != fich or not (man.get(fich) or {}).get("sha256"):
                raise ValueError("%s (%s): no es un fichero N1 con sha256 en el manifiesto" % (fich, t))
            ctx = (capas.get("contexto") or {}).get(fich) or {}
            ctx = {k: ctx[k] for k in CONTEXTO[t] if k in ctx}
            if set(ctx) != set(CONTEXTO[t]):
                raise ValueError("%s (%s): falta contexto %s en %s" % (fich, t, sorted(set(CONTEXTO[t]) - set(ctx)),
                                                                       CAPAS_PILOTO))
            trabajo.append((t, fich, man[fich]["sha256"], ctx))
    ruta = os.path.join(dir_p, REVISIONES)
    hechas = {(r.get("clave"), r.get("tarea"), r.get("sha256")) for r in _lee_jsonl(ruta) if not r.get("error")}
    nuevas = seguidos = 0
    with open(ruta, "a", encoding="utf-8") as fh:
        for t, fich, sha, ctx in trabajo:
            if (clave, t, sha) in hechas:
                continue
            reg = {"clave": clave, "tarea": t, "fichero": fich, "sha256": sha, "ts": _ahora()}
            ruta_png = os.path.join(n1, fich)
            try:
                with open(ruta_png, "rb") as fp:
                    if _sha(fp.read()) != sha:
                        raise ValueError("sha256 distinto del manifiesto")
                crudo = adapt.responder(pregunta(t, ctx), ruta_png, esquema_respuesta(t))
            except (AdaptadorPendiente, NoLocal, SinVision):
                raise
            except SinTranscripcion:
                reg["error"] = "sin respuesta del evaluador para este fichero"
            except Exception as e:  # noqa: BLE001 — se anota como error: no cuenta, se reintenta
                reg["error"] = "%s: %s" % (type(e).__name__, str(e)[:300])
                seguidos += 1
            else:
                seguidos = 0
                resp, motivo = interpreta(crudo, t)
                reg.update(resp=resp, crudo=crudo[:4000])
                if resp is None:
                    reg["error"] = "respuesta inválida: %s" % motivo
            fh.write(_canon(reg) + "\n")
            fh.flush()
            nuevas += 1
            if avisa:
                avisa("  %s %s %s" % (t, fich, reg.get("error") or ("ERROR VISTO: %s %s" % (
                    reg["resp"]["tipo"], reg["resp"]["cuadrante"]) if reg["resp"]["hay_error"] else "sin error")))
            if seguidos >= max_seguidos:
                raise RuntimeError("%d errores seguidos del adaptador: paro (anotados en %s)" % (seguidos, ruta))
    return {"clave": clave, "tareas": suyas, "nuevas": nuevas, "ruta": ruta}


def excluido(d):
    """Motivo por el que la calibración de un modelo NO habilita aunque pase el criterio, o None:
      · marcada «no_ciega» al exportar (el evaluador conocía el generador o la verdad);
      · un `transcrito` que no se declaró a ciegas (`--ciega`): deny por defecto, porque quien
        transcribe puede haber visto sinteticos.py o verdad.json y eso no se puede comprobar."""
    if (d or {}).get("no_ciega"):
        return "no ciega: %s" % d["no_ciega"]
    cfg = (d or {}).get("config") or {}
    if cfg.get("adaptador") == "transcrito" and cfg.get("ciega") is not True:
        return "transcrito sin declararse a ciegas (--ciega): no habilita"
    return None


def tabla_uso(resultados, umbral=None):
    """{tarea: [claves de modelo que pasan]}, recalculado desde los recuentos; las calibraciones
    excluidas (`excluido`: no ciegas) no habilitan nada."""
    uso = {t: [] for t in TAREAS}
    for clave, d in sorted((resultados.get("modelos") or {}).items()):
        if excluido(d):
            continue
        for t, m in (d.get("tareas") or {}).items():
            if t in uso and decide(m, umbral)["usar"]:
                uso[t].append(clave)
    return uso


def modelos_autorizados(resultados, tarea):
    """Los modelos que el panel puede usar en `tarea`. Exige el protocolo y las preguntas VIGENTES y
    recalcula la decisión con el UMBRAL vigente (no se fía del «usar» escrito). Acepta la ruta de
    un resultados.json o el dict. Sin resultado para esa tarea: lista vacía."""
    _tarea(tarea)
    if isinstance(resultados, str):
        with open(resultados, encoding="utf-8") as fh:
            resultados = json.load(fh)
    if resultados.get("protocolo") != PROTOCOLO or resultados.get("huella_preguntas") != huella_preguntas():
        return []
    return tabla_uso(resultados)[tarea]


# ── CLI ─────────────────────────────────────────────────────────────────────────────────────
def _fmt(prop):
    if prop["p"] is None:
        return "   —   "
    return "%.2f [%.2f-%.2f]" % (prop["p"], prop["ic95"][0], prop["ic95"][1])


def _imprime(res):
    for clave, d in sorted(res["modelos"].items()):
        fuera = excluido(d)
        print("\n%s%s" % (clave, "   ⛔ %s" % fuera if fuera else ""))
        print("  %-10s %-18s %-18s %7s %6s  %s" % ("tarea", "sensibilidad", "especificidad", "n e/c", "resp.",
                                                   "uso"))
        for t, m in sorted(d["tareas"].items()):
            dec = decide(m)
            n = m["n_error"] + m["n_control"]
            print("  %-10s %-18s %-18s %3d/%-3d %6s  %s" % (
                t, _fmt(m["sensibilidad"]), _fmt(m["especificidad"]), m["n_error"], m["n_control"],
                "%d/%d" % (n - m["sin_respuesta"], n),
                ("no · excluido" if fuera else "SÍ") if dec["usar"] else "no · " + "; ".join(dec["motivos"][:2])))
    print("\nUso por tarea (solo lo demostrado):")
    for t, mods in res["uso"].items():
        print("  %-10s %s" % (t, ", ".join(mods) if mods else "ningún modelo"))


def recursos_ollama(adapt, avisa=None, pausa=3.0, duerme=time.sleep, **kw):
    """Antes de cada lote: descarga ESTE modelo (si lo cargó un lote anterior) y mide. Medido con él
    cargado, un 9B en 16 GB deja ~20 % libre y la regla del 25 % no se cumpliría nunca: lo que se
    exige es que el SISTEMA tenga margen y que no haya OTRO modelo grande, no que quepan dos."""
    if adapt.cargado():                  # descargar uno que no está lo cargaría para nada
        adapt.descarga()
        duerme(pausa)
    return espera_recursos(adapt.otros_grandes, avisa=avisa, duerme=duerme, **kw)


def _correr_cli(a):
    tareas = tuple(t for t in a.tareas.split(",") if t) or None
    if a.modelo.startswith("ollama:"):
        adapt = adaptador(a.modelo, think=a.think, host=a.host, num_ctx=a.num_ctx)
        lote = a.lote or LOTE_OLLAMA
        recursos = lambda: recursos_ollama(adapt, avisa=print)  # noqa: E731
        try:
            return correr(a.dir, adapt, tareas, a.limite, avisa=print, lote=lote, recursos=recursos)
        finally:
            if adapt._piensa is not None:          # solo si llegó a hablar con Ollama
                try:
                    adapt.descarga()
                except Exception as e:  # noqa: BLE001 — descargar es cortesía, no resultado
                    print("  (no pude descargar %s: %s)" % (adapt.modelo, type(e).__name__))
    if a.modelo.startswith("transcrito:"):
        return correr(a.dir, adaptador(a.modelo, respuestas=a.respuestas, evaluador=a.evaluador, ciega=a.ciega),
                      tareas, a.limite, avisa=print)
    return correr(a.dir, adaptador(a.modelo, **_kw_externo(a.modelo)), tareas, a.limite, avisa=print)


def _kw_externo(spec):
    """El CLI pone `avisar` a los externos (el aviso del primer envío es opt-in)."""
    return {"avisar": True} if (spec or "").partition(":")[0] in PROVEEDORES_EXTERNOS else {}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="calibra.py", description="Calibración del panel de visión (errores sembrados).")
    sub = ap.add_subparsers(dest="accion", required=True)
    g = sub.add_parser("generar")
    g.add_argument("--dir", required=True)
    g.add_argument("--n", type=int, default=UMBRAL["n_min"])
    g.add_argument("--lado", type=int, default=768)
    g.add_argument("--semilla", type=int, default=SEMILLA)
    g.add_argument("--tareas", default=",".join(TAREAS))
    c = sub.add_parser("correr")
    c.add_argument("--dir", required=True)
    c.add_argument("--modelo", required=True)
    c.add_argument("--tareas", default="")
    c.add_argument("--limite", type=int)
    c.add_argument("--lote", type=int, help="ollama: casos por lote (memoria comprobada antes de cada uno)")
    c.add_argument("--think", action="store_true")
    c.add_argument("--num-ctx", type=int, help="ollama: contexto explícito (entra en la configuración y en la clave)")
    c.add_argument("--host", default=OLLAMA_DEFECTO)
    c.add_argument("--respuestas", help="transcrito: JSONL {id, crudo}")
    c.add_argument("--evaluador", help="transcrito: quién respondió y cómo vio las imágenes")
    c.add_argument("--ciega", action="store_true",
                   help="transcrito: el evaluador declara que no conocía el generador ni abrió verdad.json "
                        "(sin esto, su calibración se informa pero no habilita)")
    s = sub.add_parser("puntuar")
    s.add_argument("--dir", required=True)
    e = sub.add_parser("exportar")
    e.add_argument("--dir", required=True)
    e.add_argument("--salida", default=SALIDA_DEFECTO)
    e.add_argument("--nota", action="append", default=[], help="límite o nota de esta calibración (repetible)")
    e.add_argument("--no-ciega", action="append", default=[], metavar="CLAVE_O_NOMBRE=MOTIVO",
                   help="se informa pero no habilita (repetible)")
    sn = sub.add_parser("sembrar-n1", help="conjunto con errores sembrados sobre las capas N1 del piloto "
                                           "(5-bis (i), segunda parte); solo dentro de <n1>/panel_vision/")
    sn.add_argument("--n1", default=os.path.expanduser("~/Laminillas-N1"))
    sn.add_argument("--dir", help="por defecto <n1>/panel_vision/conjunto_n1")
    sn.add_argument("--n", type=int, default=UMBRAL["n_min"])
    sn.add_argument("--lado", type=int, default=512, help="lado de la vista recortada (núcleos, ck19, artefacto)")
    sn.add_argument("--semilla", type=int, default=20261002)
    sn.add_argument("--tareas", default="")
    r = sub.add_parser("revisar", help="revisa las capas N1 del piloto (5-bis (ii)) con un modelo calibrado")
    r.add_argument("--n1", default=os.path.expanduser("~/Laminillas-N1"))
    r.add_argument("--calibracion", help="por defecto <n1>/panel_vision/calibracion_n1.json")
    r.add_argument("--modelo", required=True)
    r.add_argument("--tareas", default="")
    r.add_argument("--think", action="store_true")
    r.add_argument("--num-ctx", type=int)
    r.add_argument("--host", default=OLLAMA_DEFECTO)
    r.add_argument("--respuestas")
    r.add_argument("--evaluador")
    r.add_argument("--ciega", action="store_true")
    u = sub.add_parser("uso")
    du = u.add_mutually_exclusive_group(required=True)
    du.add_argument("--dir")
    du.add_argument("--fichero")
    an = sub.add_parser("a-n1", help="pasa los PNG de un conjunto a ~/Laminillas-N1/ por exporta_n1 "
                                     "(para preguntar a un proveedor externo por vision_n1)")
    an.add_argument("--dir", required=True)
    m = sub.add_parser("modelos")
    m.add_argument("--host", default=OLLAMA_DEFECTO)
    a = ap.parse_args(argv)
    try:
        if a.accion == "generar":
            tareas = tuple(t for t in a.tareas.split(",") if t)
            man = genera_conjunto(a.dir, a.n, a.semilla, a.lado, tareas, avisa=print)
            print("✅ %d casos en %s · conjunto %s" % (len(man["casos"]), a.dir, man["sha_conjunto"][:12]))
        elif a.accion == "correr":
            r = _correr_cli(a)
            print("✅ %d respuestas nuevas → %s" % (r["nuevas"], r["ruta"]))
        elif a.accion == "puntuar":
            _imprime(puntua_conjunto(a.dir))
        elif a.accion == "exportar":
            out = exporta_calibracion(a.dir, a.salida, notas=a.nota, no_ciegas=no_ciegas_de(a.no_ciega))
            _imprime(out)
            print("✅ %s" % a.salida)
        elif a.accion == "sembrar-n1":
            import siembra_n1
            man = siembra_n1.genera_conjunto_n1(a.n1, a.dir, a.n, a.semilla, a.lado,
                                                tuple(t for t in a.tareas.split(",") if t) or None, avisa=print)
            print("✅ %d casos sembrados sobre %d capas N1 · conjunto %s" % (
                len(man["casos"]), sum(len(v) for v in man["origen"]["capas"].values()), man["sha_conjunto"][:12]))
        elif a.accion == "revisar":
            if a.modelo.startswith("ollama:"):
                adapt = adaptador(a.modelo, think=a.think, host=a.host, num_ctx=a.num_ctx)
            elif a.modelo.startswith("transcrito:"):
                adapt = adaptador(a.modelo, respuestas=a.respuestas, evaluador=a.evaluador, ciega=a.ciega)
            else:
                adapt = adaptador(a.modelo, **_kw_externo(a.modelo))
            cal = a.calibracion or os.path.join(a.n1, N1_PANEL, "calibracion_n1.json")
            r = revisa_capas(a.n1, cal, adapt, tuple(t for t in a.tareas.split(",") if t) or None, avisa=print)
            print("✅ %d revisiones nuevas de %s (%s) → %s" % (r["nuevas"], r["clave"], ", ".join(r["tareas"]) or
                                                             "ninguna tarea habilitada", r["ruta"]))
        elif a.accion == "a-n1":
            r = a_n1(a.dir, avisa=print)
            print("✅ %d casos nuevos en N1 (%d ya estaban) · mapa %s" % (r["nuevos"], r["ya_estaban"], r["mapa"]))
        elif a.accion == "uso":
            ruta = a.fichero or os.path.join(a.dir, "resultados.json")
            for t in TAREAS:
                mods = modelos_autorizados(ruta, t)
                print("  %-10s %s" % (t, ", ".join(mods) if mods else "ningún modelo"))
        else:
            for nombre, vision, remoto in Ollama.lista_local(a.host):
                print("  %-28s visión=%s%s" % (nombre, {True: "sí", False: "no", None: "?"}[vision],
                                              "  ⛔ remoto" if remoto else ""))
            for p, est in estado_proveedores().items():
                print("  %-28s %s" % (p, est))
        return 0
    except (AdaptadorPendiente, NoLocal, SinVision) as e:
        print("🛑 %s" % e)
        return 3
    except SinMemoria as e:
        print("🛑 %s (lo hecho queda anotado; se reanuda con el mismo comando)" % e)
        return 4
    except (ValueError, FileExistsError) as e:
        print("⛔ %s" % e)
        return 2
    except FileNotFoundError as e:
        if a.accion not in ("sembrar-n1", "a-n1"):
            raise
        print("⛔ falta %s" % e.filename)
        return 2
    except RuntimeError as e:             # PuertaCerrada (la Puerta rechaza) o capas sin sitio para sembrar
        if a.accion not in ("sembrar-n1", "a-n1"):
            raise
        print("🛑 %s: %s (%s)" % (type(e).__name__, e, "sin manifiesto: no hay conjunto" if a.accion == "sembrar-n1"
                                  else "lo ya pasado queda en n1.json; se reanuda con el mismo comando"))
        return 3


if __name__ == "__main__":
    sys.exit(main())
