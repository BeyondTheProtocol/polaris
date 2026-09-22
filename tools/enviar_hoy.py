#!/usr/bin/env python3
"""Envía el PARTE de HOY de {{TITULAR}} a su Telegram. Sin dependencias (stdlib).

El parte se compone SIEMPRE de forma DETERMINISTA y GRATIS con
`seguimiento.construir_hoy(franja, 'telegram')` (el canal 'telegram' YA redacta los
nombres de terceros): NO depende del saldo ni de que el daemon LLM (`hoy-compose`) haya
corrido. Así el parte SALE igual aunque no haya crédito — el suelo de la escalera de
coste es $0 y nunca toca el suelo del silencio. (Antes este script extraía un bloque
'HOY EN 30 SECUNDOS' de HOY.md; ese formato murió al pasar al parte de 4 bloques, así
que el envío gratis estaba roto y mandaba "No encontré la sección". Ya no.)

La entrega NO la hace este script: la hace el choke-point único `tools/salida.py`
(report_to_titular). Así toda salida hacia fuera vive en un solo módulo auditado,
cortable por .HALT (B3 del muro).

CIERRE DEL CIRCULO (feat. upsert_evento):
Al construir el parte, si HOY.md existe (generado por el daemon hoy-compose), se
extrae su bloque TU AHORA y cada item que contenga una fecha ISO se persiste en
seguimiento.json via upsert_evento — de forma que lo que el LLM descubrio en HOY.md
llega al registro unico. Fail-soft: si falla, el parte se envia igual.

USO:
  python3 enviar_hoy.py            # envia el parte (franja deducida de la hora)
  python3 enviar_hoy.py noche      # fuerza una franja (manana|mediodia|tarde|noche)
  python3 enviar_hoy.py dry        # solo muestra lo que enviaria (sin tocar la red)
  python3 enviar_hoy.py noche dry  # franja explicita + dry
"""
import os
import re
import sys
from datetime import datetime, date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from salida import report_to_titular, aplazados_de_hoy, vaciar_aplazados_de_hoy  # noqa: E402
import seguimiento  # noqa: E402

FRANJAS = ("mañana", "mediodía", "tarde", "noche")


def _franja_por_hora():
    # Mismo reparto que el CLI de seguimiento.py (check): mañana<12 · mediodía<16 · tarde<21 · noche.
    h = datetime.now().hour
    return "mañana" if h < 12 else "mediodía" if h < 16 else "tarde" if h < 21 else "noche"


# ── Cierre del circulo: HOY.md → seguimiento.json ────────────────────────────
# Patrones conservadores para extraer fecha ISO de una linea de HOY.md.
_RE_ISO_HOY = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_RE_DMMES = re.compile(r"\b(\d{1,2})[/-](ene|feb|mar|abr|may|jun|jul|ago|sep|oct|nov|dic)\w*\b",
                        re.IGNORECASE)
_RE_DM = re.compile(r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](\d{4}))?\b")
_MESES_ES = {
    "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
    "jul": 7, "ago": 8, "sep": 9, "oct": 10, "nov": 11, "dic": 12,
}


def _fecha_linea(linea):
    """Extrae la primera fecha ISO de una linea de texto. Devuelve str YYYY-MM-DD o None."""
    m = _RE_ISO_HOY.search(linea)
    if m:
        return m.group(1)
    m2 = _RE_DMMES.search(linea)
    if m2:
        dd, mes_txt = int(m2.group(1)), m2.group(2).lower()[:3]
        mes = _MESES_ES.get(mes_txt)
        if mes:
            anio = date.today().year
            try:
                return date(anio, mes, dd).isoformat()
            except Exception:
                pass
    m3 = _RE_DM.search(linea)
    if m3:
        dd, mm = int(m3.group(1)), int(m3.group(2))
        anio = int(m3.group(3)) if m3.group(3) else date.today().year
        if 1 <= mm <= 12 and 1 <= dd <= 31:
            try:
                return date(anio, mm, dd).isoformat()
            except Exception:
                pass
    # "mañana" = hoy + 1
    if "mañana" in linea.lower() or "manana" in linea.lower():
        from datetime import timedelta
        return (date.today() + timedelta(days=1)).isoformat()
    return None


def _entidad_linea(linea):
    """Extrae el primer nombre propio (token de 4+ chars, inicial mayuscula) de una linea.
    Conservador: si no hay candidato claro, devuelve ''. NO usa NLP — puramente heuristico."""
    # Buscar patron "Nombre Apellido" precedido de — o seguido de (org)
    m = re.search(r"\b([A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})?)\b", linea)
    if m:
        candidato = m.group(1).lower().replace(" ", "-")
        # Descartar palabras que no son nombres propios de personas/orgs.
        _NO_ENT = {"lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo",
                   "google", "swiss", "brussels", "airlines", "nmbs", "{{CENTRO}}",
                   "telegram", "gmail", "amazon", "linkedin", "whatsapp", "instagram"}
        if candidato not in _NO_ENT and len(candidato) >= 4:
            return candidato
    return ""


def _persistir_hoy_md(hoy_md_path):
    """Lee HOY.md, extrae items del bloque TU AHORA con fecha, y los persiste
    en seguimiento.json via upsert_evento. Fail-soft: nunca lanza.

    Solo persiste items con fecha detectada (evita spam de hilos sin fecha).
    Solo el bloque TU AHORA (items que {{TITULAR}} debe gestionar; lo demas ya vive
    en seguimiento de otra forma o es informativo).

    Devuelve lista de ids persistidos (puede ser []).
    """
    persistidos = []
    try:
        with open(hoy_md_path, encoding="utf-8") as f:
            contenido = f.read()
    except Exception:
        return persistidos

    # Extraer el bloque TU AHORA.
    m = re.search(r"##[^#]*TU[^#\n]*\n(.*?)(?=\n##|\Z)", contenido,
                  re.IGNORECASE | re.DOTALL)
    if not m:
        # Intentar patron alternativo (con emojis).
        m = re.search(r"🔴[^\n]*\n(.*?)(?=\n##|🤖|✍️|\Z)", contenido, re.DOTALL)
    if not m:
        return persistidos

    bloque = m.group(1)
    # Items numerados: "1️⃣ **titulo** o "N. **titulo**" o "- **titulo**".
    for linea in bloque.splitlines():
        linea = linea.strip()
        if not linea or linea.startswith(("#", "---", ">")):
            continue
        # Limpiar numeracion y markdown.
        texto = re.sub(r"^[\d️⃣\-\*\.\s]+", "", linea).strip()
        texto = re.sub(r"\*\*([^*]+)\*\*", r"\1", texto)  # quitar **negrita**
        if not texto or len(texto) < 8:
            continue

        fecha = _fecha_linea(texto + " " + linea)
        if not fecha:
            continue  # sin fecha → no persistimos (demasiado ambiguo)

        entidad = _entidad_linea(texto)
        if not entidad:
            # Fallback: usar las primeras 3 palabras como slug de entidad.
            palabras = [p for p in re.split(r"\W+", texto.lower()) if len(p) >= 3]
            entidad = "-".join(palabras[:2]) if palabras else "item"

        # Titulo limpio: primera frase antes de un punto o salto.
        titulo_corto = re.split(r"[.;\n]", texto)[0].strip()
        titulo_corto = titulo_corto[:80] if len(titulo_corto) > 80 else titulo_corto
        if not titulo_corto:
            continue

        try:
            hid = seguimiento.upsert_evento(
                entidad=entidad,
                tipo="tarea",
                fecha_iso=fecha,
                titulo=titulo_corto,
                estado="por_confirmar",
                etiqueta="NED",
                categoria="otros",
                origen="enviar_hoy",
                prioridad="alta",
            )
            persistidos.append(hid)
        except Exception:
            pass  # fail-soft: el parte se envia de todas formas

    return persistidos


def _con_aplazados(text):
    """Añade al parte los avisos que el portero de ruido agrupó hoy (tools/salida.py
    _aplazar()). Es la promesa que le hace el mensaje "el resto lo agrupo en el parte":
    si no se incorporan aquí, se pierden para siempre (nadie más los lee)."""
    apl = aplazados_de_hoy()
    if not apl:
        return text
    lineas = ["", "📌 Avisos de hoy agrupados (%d):" % len(apl)]
    for a in apl:
        t = str(a.get("texto", "")).strip()
        if t:
            lineas.append("• " + t)
    return text + "\n".join(lineas)


def main():
    args = sys.argv[1:]
    dry = "dry" in args
    franja = next((a for a in args if a in FRANJAS), None) or _franja_por_hora()

    # Cierre del circulo: persistir items de HOY.md antes de construir el parte.
    # Fail-soft: si falla no rompe el envio. Solo si hay un HOY.md con fecha de hoy.
    hoy_md = seguimiento.HOY
    persistidos = _persistir_hoy_md(hoy_md)
    if persistidos and "--verbose" in args:
        print("📥 Persistidos desde HOY.md: %d item(s) → %s" % (len(persistidos), persistidos))

    text = _con_aplazados(seguimiento.construir_hoy(franja, "telegram"))
    if dry:
        print("── Lo que se enviaría a Telegram (%s · determinista, GRATIS) ──\n%s" % (franja, text))
        return 0
    # categoria="parte": EXENTO del cupo de avisos (tools/salida.py, portero de ruido).
    # El parte es el propio mecanismo de vaciado de lo aplazado — si cayera bajo el
    # mismo cupo, lo aplazado no saldría nunca y el cupo bloquearía el parte en sí.
    res = report_to_titular(text, categoria="parte")
    if res.get("delivered"):
        vaciar_aplazados_de_hoy()  # solo AHORA que sabemos que de verdad llegó
        print("✅ Parte de HOY enviado a Telegram (vía salida.py)")
        return 0
    # 'retenido' (silencio nocturno → se reenvía a las 08:00) y 'dry' NO son fallo.
    if res.get("retenido") or res.get("dry"):
        print("⏸️ Parte no enviado ahora (no es fallo): " + res.get("reason", "?"))
        return 0
    # Cualquier otra no-entrega (sin chat_id, red caída, sin saldo) SÍ es fallo:
    # rc=1 para que launchd lo registre y NO dé el parte por enviado en silencio.
    print("⚠️ NO se envió el parte de HOY: " + res.get("reason", "?") +
          (" · borrador: " + res["draft"] if res.get("draft") else ""))
    return 1


if __name__ == "__main__":
    sys.exit(main())
