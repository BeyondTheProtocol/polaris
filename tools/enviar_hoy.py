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
from datetime import datetime, date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from salida import report_to_titular, aplazados_pendientes, vaciar_aplazados  # noqa: E402
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


# ── Lo aplazado entra en el parte (24-sep-26) ────────────────────────────────
# Antes solo entraba lo aplazado HOY, y como el parte sale a las 08:12 y casi todo se
# aplaza después, no entraba nada: 855 avisos perdidos en dos meses. Ahora entra todo
# lo pendiente. Lo de hoy y ayer va aviso a aviso, en una línea cada uno; lo más viejo
# (un parte que no salió, o el atasco del 27-jul al 24-sep) va resumido por tipo con
# los plazos que siguen vivos. El texto completo no se pierde: salida.vaciar_aplazados
# lo archiva en tools/state/aplazados/entregados/.
TOPE_LINEAS_APLAZADOS = 15
LARGO_LINEA_APLAZADO = 200
_ETIQUETA_FUENTE = {
    "healthcheck": "de salud del sistema",
    "pendientes": "de correos que esperan tu respuesta",
    "centinela": "de plazos y correos importantes",
    "polaris-estado": "de estado de Polaris",
    "observatorio-parte": "de estado de Polaris",
    "correo-imap": "de correo nuevo",
    "cost_guard": "de gasto",
    "vigia": "del vigía",
}
_RE_PLAZO_APLAZADO = re.compile(
    r"Plazo (T-\d+) — «(.+?)» se acerca: es (HOY|manana|en \d+ dias \((\d{4}-\d{2}-\d{2})\))")
_MES_CORTO = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")


def _dia_corto(iso):
    try:
        d = date.fromisoformat(iso[:10])
        return "%d-%s" % (d.day, _MES_CORTO[d.month - 1])
    except Exception:
        return iso


def _en_una_linea(texto):
    t = " · ".join(p.strip(" -•") for p in str(texto).splitlines() if p.strip(" -•"))
    return t if len(t) <= LARGO_LINEA_APLAZADO else t[:LARGO_LINEA_APLAZADO - 1].rstrip() + "…"


def _piezas(texto):
    """El aviso de salud («🩺 Revisión de salud del lazo:») trae varios hallazgos en viñetas y
    cambia de una revisión a otra: cada viñeta es una pieza, para que el CI en rojo no quede
    enterrado en el bloque. Cualquier otro aviso es UNA pieza entera, cabecera incluida: sus
    viñetas suelen ser el detalle de la primera frase, y sin ella no se entiende el porqué."""
    lineas = [l.strip() for l in str(texto).splitlines() if l.strip()]
    if lineas and lineas[0].startswith("🩺"):
        vinetas = []
        for l in lineas[1:]:
            if l.startswith("- "):
                vinetas.append(l[2:].strip())
            elif vinetas:
                vinetas[-1] += " " + l      # continuación de la viñeta: suele ser lo accionable
        if vinetas:
            return vinetas
    return [" · ".join(l[2:].strip() if l.startswith("- ") else l for l in lineas)]


# Lo que cambia entre dos versiones del MISMO aviso y no lo hace otro distinto: «(5 seguidas)» y
# «(30 seguidas)», «hace 3h», «23 sesiones», «$85 de $60», el nº de ejecución del CI. Solo esto se
# ignora al juntar repetidos. Quitar TODAS las cifras fundía «HTTP 429» con «HTTP 500» y el
# daemon «job-1» con «job-2» (verificacion, 24-sep).
_RE_VOLATIL = re.compile(
    r"\(\d+ seguidas?\)|hace \d+ ?\w*|\b\d+(?:[.,]\d+)? ?(?:h|min|GB|%|sesiones|seguidas|encargo\(s\))(?!\w)"
    r"|\$\d+(?:[.,]\d+)?|\d+(?:[.,]\d+)?\$|runs/\d+|job [0-9a-f]{6,}")


def _cuando(ts, hoy):
    """«ayer 19:32» / «hoy 08:05»: lo aplazado ayer se lee hoy, y un «hoy vence» o un «es mañana»
    sin fecha llegaría un día tarde."""
    try:
        d = date.fromisoformat(ts[:10])
    except Exception:
        return ""
    hora = ts[11:16]
    if d == hoy:
        return "hoy " + hora
    if d == hoy - timedelta(days=1):
        return "ayer " + hora
    return _dia_corto(ts[:10]) + " " + hora


def _bloque_recientes(avisos, hoy=None):
    # Se muestra la versión más reciente de cada aviso, con su hora y cuántas veces salió.
    # Primero los plazos, luego lo más nuevo.
    hoy = hoy or date.today()
    vistos = {}   # clave → [línea más reciente, veces, ts, es_plazo]
    for a in avisos:
        ts = str(a.get("ts", ""))
        for pieza in _piezas(a.get("texto", "")):
            linea = _en_una_linea(pieza)
            if not linea:
                continue
            clave = _RE_VOLATIL.sub("#", linea)
            v = vistos.setdefault(clave, [linea, 0, "", a.get("fuente") == "centinela"])
            v[1] += 1
            if ts >= v[2]:
                v[0], v[2] = linea, ts
    if not vistos:
        return []
    out = ["", "📌 Avisos agrupados desde el último parte (%d):" % len(avisos)]
    items = sorted(vistos.values(), key=lambda v: v[2], reverse=True)
    items = [v for v in items if v[3]] + [v for v in items if not v[3]]
    for linea, veces, ts, _ in items[:TOPE_LINEAS_APLAZADOS]:
        cuando = _cuando(ts, hoy)
        out.append("• " + (cuando + " · " if cuando else "") + linea
                   + (" (×%d)" % veces if veces > 1 else ""))
    if len(items) > TOPE_LINEAS_APLAZADOS:
        out.append("• …y %d más. El texto completo queda en tools/state/aplazados/entregados/."
                   % (len(items) - TOPE_LINEAS_APLAZADOS))
    return out


def _plazos_vivos(avisos, hoy):
    """Plazos avisados por el centinela cuya fecha aún no ha pasado. Por título, el último."""
    vivos = {}
    for a in avisos:
        m = _RE_PLAZO_APLAZADO.search(str(a.get("texto", "")))
        if not m:
            continue
        try:
            base = date.fromisoformat(str(a.get("ts", ""))[:10])
        except Exception:
            continue
        cuando = m.group(3)
        if cuando == "HOY":
            fecha = base
        elif cuando == "manana":
            fecha = base + timedelta(days=1)
        else:
            try:
                fecha = date.fromisoformat(m.group(4))
            except Exception:
                continue
        if fecha >= hoy:
            vivos[m.group(2)] = fecha
    return sorted(vivos.items(), key=lambda kv: kv[1])


def _bloque_rezagados(avisos, hoy):
    if not avisos:
        return []
    dias = sorted(a["dia"] for a in avisos)
    por_tipo = {}
    for a in avisos:
        etiqueta = _ETIQUETA_FUENTE.get(a.get("fuente") or "", "de otro tipo")
        por_tipo[etiqueta] = por_tipo.get(etiqueta, 0) + 1
    out = ["", "🗃️ Además hay %d avisos de días anteriores (%s a %s) que no llegaron a salir. "
               "No te los mando uno a uno: quedan archivados en tools/state/aplazados/entregados/. "
               "Por tipo:" % (len(avisos), _dia_corto(dias[0]), _dia_corto(dias[-1]))]
    for etiqueta, n in sorted(por_tipo.items(), key=lambda kv: -kv[1]):
        out.append("• %d %s" % (n, etiqueta))
    vivos = _plazos_vivos(avisos, hoy)
    if vivos:
        out.append("Plazos de esos avisos que siguen vivos:")
        for titulo, fecha in vivos:
            out.append("• «%s»: %s" % (titulo, _dia_corto(fecha.isoformat())))
    return out


def _con_aplazados(text, lote=None, hoy=None):
    """Añade al parte lo que el portero de ruido aplazó (tools/salida.py _aplazar()). Es
    la promesa del mensaje «el resto lo agrupo en el parte»: si no entra aquí, nadie más
    lo lee. `lote` sale de salida.aplazados_pendientes(); su recibo ("leidos") es lo que
    main() pasa a vaciar_aplazados() cuando el parte se entrega."""
    lote = aplazados_pendientes() if lote is None else lote
    avisos = lote.get("avisos") or []
    if not avisos:
        return text
    hoy = hoy or date.today()
    desde = (hoy - timedelta(days=1)).isoformat()
    recientes = [a for a in avisos if a.get("dia", "") >= desde]
    rezagados = [a for a in avisos if a.get("dia", "") < desde]
    lineas = []
    # Cada bloque falla por su cuenta: si uno no se puede componer, el otro sale igual y se dice.
    for nombre, bloque, grupo in (("de hoy y ayer", _bloque_recientes, recientes),
                                  ("de días anteriores", _bloque_rezagados, rezagados)):
        try:
            lineas += bloque(grupo, hoy)
        except Exception as e:
            lote["fallo"] = True        # main() no vacía: lo que no salió no se archiva como entregado
            lineas += ["", "⚠️ No pude componer los avisos agrupados %s (%s). Siguen guardados."
                       % (nombre, type(e).__name__)]
    return text + "\n".join(lineas) if lineas else text


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

    base = seguimiento.construir_hoy(franja, "telegram")
    # El parte sale SIEMPRE: si lo aplazado no se puede leer o componer, va el cuerpo solo, lo dice,
    # y no se vacía nada (lote vacío), así que lo aplazado espera al siguiente parte.
    try:
        lote = aplazados_pendientes()
        text = _con_aplazados(base, lote)
    except Exception as e:
        lote = {"avisos": [], "leidos": {}}
        text = base + "\n\n⚠️ No pude añadir los avisos agrupados (%s). Siguen guardados." % type(e).__name__
    if dry:
        print("── Lo que se enviaría a Telegram (%s · determinista, GRATIS) ──\n%s" % (franja, text))
        return 0
    # categoria="parte": EXENTO del cupo de avisos (tools/salida.py, portero de ruido).
    # El parte es el propio mecanismo de vaciado de lo aplazado — si cayera bajo el
    # mismo cupo, lo aplazado no saldría nunca y el cupo bloquearía el parte en sí.
    res = report_to_titular(text, categoria="parte")
    if res.get("delivered"):
        if not lote.get("fallo"):
            vaciar_aplazados(lote["leidos"])  # solo AHORA que sabemos que de verdad llegó
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
