#!/usr/bin/env python3
"""tools/eval_triage.py — el SET DORADO del triage: con qué medir antes de creerse a nadie.

Por qué existe (20-sep-26): al evaluar Jev (TypeSafe AI) para el carril de triage, el cuello de
botella no era el acceso al modelo — era que **no teníamos con qué medir**. Sin un set de casos
etiquetados, cambiar de clasificador es cuestión de fe. Esto construye ese set, y sirve igual si
mañana el candidato es otro modelo, otra heurística o un regex mejor.

Qué hace:
  1. EXTRAE casos reales de dos sitios: la cola de dudosas (`tools/state/tareas/dudosas.json`)
     y el histórico de hilos (`tools/state/seguimiento.json`).
  2. DE-IDENTIFICA cada texto con `borde.de_identificar` y, ADEMÁS, tapa los nombres propios de
     la deny-list (médicos, familia) que `de_identificar` no cubre: solo redacta el nombre de
     {{TITULAR}}, y un set que va a salir hacia un tercero no puede llevar el de su oncóloga.
  3. VERIFICA fail-closed: cualquier caso que después de redactar siga disparando
     `borde.identificador_directo` NO entra al set y se cuenta aparte. Preferimos un set más
     pequeño que uno contaminado.

Muro: este fichero NO envía nada a ninguna parte. Produce un JSONL local. Enviarlo a un tercero
es una decisión aparte, con su gate y pasando por `tools/enruta.py`.

Las etiquetas:
  · `tarea` / `no`  — derivadas del estado real del hilo (lo que de verdad pasó después).
  · `PENDIENTE`     — las dudosas. Son los casos DIFÍCILES, los que de verdad importan, y no
                      tienen verdad automática: hay que etiquetarlas a mano. Es el trabajo real.

Uso:
  python3 tools/eval_triage.py extraer      # construye el set
  python3 tools/eval_triage.py estado       # cuántos hay, de qué clase, cuántos sin etiquetar
  python3 tools/eval_triage.py revisar      # imprime las PENDIENTE para etiquetarlas a mano
  python3 tools/eval_triage.py etiquetar <id> <tarea|no>
  python3 tools/eval_triage.py endurecer    # re-tapa el residuo (fechas, URLs, @, números) sin tocar etiquetas
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import borde

# Igual que triage_tareas.py: el estado vive en casa base, nunca en un worktree.
ROOT = os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode")
DUDOSAS = os.path.join(ROOT, "tools", "state", "tareas", "dudosas.json")
SEGUIMIENTO = os.path.join(ROOT, "tools", "state", "seguimiento.json")
SALIDA = os.path.join(ROOT, "tools", "state", "eval", "triage_golden.jsonl")
CORREO_CAT = os.path.join(ROOT, "tools", "state", "correo", "categorizacion_propuesta.json")

# Un hilo que sigue vivo o que se completó ERA una tarea real; uno descartado, no.
ESTADO_ES_TAREA = {"hecho", "en_curso", "esperando", "por_confirmar", "bloqueado", "pendiente"}
ESTADO_NO_ES_TAREA = {"descartado"}


def _nombres_deny():
    """Nombres propios a tapar además de lo que cubre `borde.de_identificar` (que solo redacta
    el de {{TITULAR}}). Salen de tools/nombres.local.json; si no está, el set no se construye:
    fail-closed, porque sin la lista no sabemos qué estamos dejando pasar."""
    p = os.path.join(ROOT, "tools", "nombres.local.json")
    if not os.path.exists(p):
        raise SystemExit(f"⛔ falta {p}: sin la deny-list de nombres no se construye el set")
    with open(p, encoding="utf-8") as f:
        d = json.load(f)
    nombres = d.get("nombres") or []
    if isinstance(nombres, dict):
        nombres = list(nombres) + [v for v in nombres.values() if isinstance(v, str)]
    # Cada palabra de cada nombre por separado: en el correo aparecen sueltos ("{{CONTACTO}}, {{CONTACTO}}").
    partes = set()
    for n in nombres:
        if not isinstance(n, str):
            continue
        for w in re.split(r"[\s,]+", n):
            if len(w) > 2:
                partes.add(w)
    return sorted(partes, key=len, reverse=True)


# Palabras que empiezan por mayúscula pero NO son un nombre propio de persona: se conservan
# porque son las que dan señal al clasificador («Correo», «Pedir», «Enviar»…). Todo lo demás
# que vaya en mayúscula se tapa. Es deliberadamente agresivo: se pierde «Zúrich» y «{{CENTRO}}», que
# al clasificador de "¿es esto una tarea?" no le aportan nada, y a cambio no depende de que una
# lista escrita a mano esté completa — que es justo por donde falló la primera versión.
_SEGURAS = {
    "correo", "email", "mail", "tarea", "tareas", "hilo", "hilos", "cita", "citas", "reserva",
    "vuelo", "tren", "traslado", "factura", "facturas", "informe", "informes", "nota", "notas",
    "pendiente", "pendientes", "seguir", "seguimos", "enviar", "pedir", "llamar", "escribir",
    "mandar", "confirmar", "revisar", "preparar", "cerrar", "abrir", "hacer", "posibles",
    "duplicados", "evento", "eventos", "gestion", "gestión", "gracias", "hola", "buenos",
    "buenas", "gestor", "recordatorio", "gestionar", "aviso", "avisos", "gestiones",
    "lunes", "martes", "miercoles", "miércoles", "jueves", "viernes", "sabado", "sábado",
    "domingo", "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
    "septiembre", "octubre", "noviembre", "diciembre", "re", "aw", "fwd", "rv", "extern",
    "el", "la", "los", "las", "un", "una", "de", "del", "al", "y", "o", "en", "que", "si",
    "no", "es", "se", "lo", "le", "por", "para", "con", "sin", "sobre",
}
_SEGURAS |= {"redactado", "nombre", "dominio", "fecha", "handle", "num"}  # los propios marcadores, para no anidarlos
_RE_PALABRA_MAYUS = re.compile(r"\b[A-ZÁÉÍÓÚÜÑ][\wÁÉÍÓÚÜÑáéíóúüñ'-]{2,}\b")
_RE_DOMINIO = re.compile(r"\b(?:https?://)?[\w.-]+\.(?:com|es|org|net|ai|io|dev|eu|ch)\b(?:/\S*)?", re.I)
_RE_PEGADO = re.compile(r"\b[\w-]*(?:titular|contacto)[\w-]*\b", re.I)

# Residuo que las pasadas de arriba dejaban pasar (21-sep-26): el set «de-identificado» llevaba
# una fecha con pinta de nacimiento junto a un correo del hospital, @handles de terceros y URLs
# de dominios fuera de la lista de `_RE_DOMINIO`. `borde.clasificar` lo daba por limpio. Una
# fecha no le hace falta a ningún clasificador de «¿es tarea?», así que se tapa sin más.
_RESIDUO = (
    (re.compile(r"\b\d{4}[/.-]\d{1,2}[/.-]\d{1,2}\b"), "[FECHA]"),  # ISO: 2026-07-13
    (re.compile(r"\b\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}\b"), "[FECHA]"),
    (re.compile(r"\bhttps?://\S+", re.I), "[DOMINIO]"),
    (re.compile(r"@\w+"), "[HANDLE]"),
    (re.compile(r"\d{5,}"), "[NUM]"),
)


def _tapar_residuo(texto):
    """(texto, n). Fechas, URLs de cualquier dominio, @handles y números de 5+ cifras."""
    n = 0
    for rx, marca in _RESIDUO:
        texto, k = rx.subn(marca, texto)
        n += k
    return texto, n


def _vocabulario_de_nombres(textos):
    """Toda palabra que aparezca EN MAYÚSCULA en algún punto del corpus y no sea segura.

    Existe por un fallo real: tapando solo las mayúsculas, «{{CONTACTO}}» desaparecía pero «contacto»
    en minúscula sobrevivía, porque el apellido de esa persona no estaba en la lista a mano.
    Recogiendo primero el vocabulario del corpus entero y tapándolo luego sin distinguir
    mayúsculas, un nombre que aparezca una sola vez bien escrito queda tapado en todas partes.
    """
    vocab = set()
    for t in textos:
        for m in _RE_PALABRA_MAYUS.finditer(t or ""):
            w = m.group(0)
            if w.lower() not in _SEGURAS:
                vocab.add(w.lower())
    return sorted(vocab, key=len, reverse=True)


def _redactar(texto, deny):
    """De-identifica y devuelve (texto, n_redacciones).

    Tres pasadas, de menos a más agresiva:
      1. `borde.de_identificar` — identificadores duros (email, DNI, teléfono, variantes…).
      2. La deny-list de nombres conocidos.
      3. Toda palabra en mayúscula que no esté en `_SEGURAS`.

    La 3 existe porque la 2 NO basta y lo comprobé: con solo 1+2, el primer set que generé
    dejaba pasar los apellidos de cinco médicos. La deny-list es una lista a mano y las listas
    a mano se quedan cortas; la regla estructural, no.
    """
    out, n = borde.de_identificar(texto or "")
    out, k = _tapar_residuo(out)
    n += k
    # Dominios y URLs: `helptitular.com` es público, pero lleva su nombre dentro y ata el corpus
    # entero a una persona concreta. El criterio no es «¿es secreto?» sino «¿identifica?».
    out, k = _RE_DOMINIO.subn("[DOMINIO]", out)
    n += k
    # Cualquier token que CONTENGA su nombre, aunque vaya pegado a otra cosa y en minúscula:
    # `helptitular` (sin el .com detrás) se coló por aquí en la tercera pasada de pruebas.
    out, k = _RE_PEGADO.subn("[NOMBRE]", out)
    n += k
    for nombre in deny:
        # Con `\b`: sin él, tapar «Sera» dentro de «reserva» deja «re[NOMBRE]va» y el caso
        # pierde justo la palabra que le daba sentido al clasificador.
        out, k = re.subn(rf"(?i)\b{re.escape(nombre)}\b", "[NOMBRE]", out)
        n += k

    tapadas = 0

    def _tapa(m):
        nonlocal tapadas
        if m.group(0).lower() in _SEGURAS:
            return m.group(0)
        tapadas += 1
        return "[NOMBRE]"

    out = _RE_PALABRA_MAYUS.sub(_tapa, out)
    return out, n + tapadas


# La cola de dudosas mezcla DOS preguntas que no son la misma decisión. Medirlas juntas da un
# número sin sentido, así que cada caso lleva de qué pregunta es y el set se parte al evaluar.
#   · es_tarea  — «¿esto que ha llegado es una tarea o es ruido?»
#   · dedup     — «¿estos dos eventos son el mismo y los fundimos?»
def _tipo_pregunta(texto):
    t = (texto or "").lower()
    if "posibles duplicados" in t and "fundimos" in t:
        return "dedup"
    return "es_tarea"


# Etiquetas de las dudosas, puestas a mano leyendo el texto ORIGINAL (local, sin egress). Es el
# trabajo que no se puede automatizar: son justo los casos que el clasificador no supo resolver.
# La clave es el texto original recortado, para no versionar el texto entero aquí.
#   · Reservas y correos de su equipo médico sin hilo abierto → SÍ eran tarea: todos acabaron
#     necesitando una acción suya o mía.
#   · Los «posibles duplicados» → NO se fundían en ninguno de los casos: el detector agrupaba por
#     una palabra suelta del título («semana», «recoge», «comité») y juntaba cosas sin relación.
_ETIQUETAS_DUDOSAS = {
    "tren 1": "tarea", "traslado zrh": "tarea", "traslado {{CENTRO}}": "tarea", "vuelo z": "tarea",
    "re: dicoms": "tarea", "information for material shipment": "tarea", "slides/block": "tarea",
    "procedimientos y anal": "tarea", "sample at {{CENTRO}}": "tarea", "actualizacion": "tarea",
    "shipment to": "tarea",
    "shipment to boston - updated": "tarea",
    "'constituci": "no", "'vuelca'": "no", "'abogado'": "no", "'comités'": "no",
    "'tareas'": "no", "'recoge'": "no", "'oncóloga'": "no", "'comité'": "no",
    "'semana'": "no", "'enfermer": "no",
}


def _etiqueta_dudosa(texto):
    """Busca la etiqueta a mano por fragmento del texto original. None si no la tengo puesta."""
    t = (texto or "").lower()
    for frag, et in _ETIQUETAS_DUDOSAS.items():
        if frag in t:
            return et
    return None


def _casos():
    """Genera los casos crudos (texto, etiqueta, fuente) antes de redactar."""
    if os.path.exists(DUDOSAS):
        with open(DUDOSAS, encoding="utf-8") as f:
            for d in json.load(f).get("dudosas", []):
                texto = d.get("texto", "")
                yield texto, _etiqueta_dudosa(texto) or "PENDIENTE", "dudosas"

    # El correo ya categorizado a mano/por reglas es la única fuente de NEGATIVOS reales que
    # tenemos: «Promos» (spam y newsletters) es ruido que nunca fue tarea. Sin esto el set tenía
    # 3 negativos y cualquier cifra de acierto era humo.
    if os.path.exists(CORREO_CAT):
        with open(CORREO_CAT, encoding="utf-8") as f:
            for pr in json.load(f).get("propuestas", []):
                cat = pr.get("categoria_propuesta")
                if cat == "Promos":
                    etiqueta = "no"
                elif cat == "NED/Médico":
                    etiqueta = "tarea"
                else:
                    continue  # «Admin» es genuinamente ambiguo: fuera, no inventamos verdad.
                texto = f"Correo de {pr.get('remitente', '?')}: «{pr.get('asunto', '')}»"
                yield texto, etiqueta, "correo"

    if os.path.exists(SEGUIMIENTO):
        with open(SEGUIMIENTO, encoding="utf-8") as f:
            for h in json.load(f).get("hilos", []):
                estado = (h.get("estado") or "").strip()
                if estado in ESTADO_ES_TAREA:
                    etiqueta = "tarea"
                elif estado in ESTADO_NO_ES_TAREA:
                    etiqueta = "no"
                else:
                    continue
                yield h.get("titulo", ""), etiqueta, f"hilo:{h.get('id', '?')}"


def extraer():
    os.makedirs(os.path.dirname(SALIDA), exist_ok=True)

    # Dos pasadas: primero se lee el corpus entero para saber qué palabras son nombres, y solo
    # después se redacta. Al revés se escapan las apariciones en minúscula.
    casos = list(_casos())
    deny = list(_nombres_deny()) + _vocabulario_de_nombres(t for t, _, _ in casos)

    filas, descartados, vistos = [], [], set()
    for texto, etiqueta, fuente in casos:
        if not texto or not texto.strip():
            continue
        red, n = _redactar(texto, deny)
        crudo, motivo = borde.identificador_directo(red)
        if crudo:
            # Fail-closed: si después de redactar sigue atando a una persona, fuera del set.
            descartados.append((fuente, motivo))
            continue
        clave = red.strip().lower()
        if clave in vistos:
            continue
        vistos.add(clave)
        filas.append({
            "id": f"t{len(filas):03d}",
            "texto": red.strip(),
            "etiqueta": etiqueta,
            # Solo el TIPO de origen, nunca el id: los ids de hilo son slugs del título y
            # llevaban dentro los apellidos de sus médicos. La trazabilidad al hilo concreto
            # no vale exponer a quién.
            "fuente": fuente.split(":", 1)[0],
            "pregunta": _tipo_pregunta(texto),
            "redacciones": n,
        })

    with open(SALIDA, "w", encoding="utf-8") as f:
        for fila in filas:
            f.write(json.dumps(fila, ensure_ascii=False) + "\n")

    print(f"✓ {len(filas)} casos → {SALIDA}")
    if descartados:
        print(f"⛔ {len(descartados)} descartados por seguir siendo identificables tras redactar:")
        for fuente, motivo in descartados[:5]:
            print(f"   · {fuente}: {motivo}")
        if len(descartados) > 5:
            print(f"   … y {len(descartados) - 5} más")
    _resumen(filas)
    return filas


def _cargar():
    if not os.path.exists(SALIDA):
        raise SystemExit(f"⛔ no existe {SALIDA}. Corre primero: eval_triage.py extraer")
    with open(SALIDA, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def _resumen(filas):
    from collections import Counter
    print("\n  pregunta     tarea    no  PEND")
    for preg in sorted({f.get("pregunta", "es_tarea") for f in filas}):
        c = Counter(f["etiqueta"] for f in filas if f.get("pregunta") == preg)
        print(f"  {preg:<11}{c.get('tarea', 0):>5}{c.get('no', 0):>6}{c.get('PENDIENTE', 0):>6}")
    c = Counter(f["etiqueta"] for f in filas)
    print(f"  {'TOTAL':<11}{c.get('tarea', 0):>5}{c.get('no', 0):>6}{c.get('PENDIENTE', 0):>6}")
    if c.get("no", 0) < 20:
        print(f"\n  ⚠️  Solo {c.get('no', 0)} negativos: el set está desequilibrado. Un clasificador")
        print("     que diga «tarea» a todo acertaría casi siempre. Hacen falta más ejemplos de")
        print("     ruido real antes de que una cifra de acierto signifique algo.")
    if c.get("PENDIENTE", 0):
        print(f"\n  → {c['PENDIENTE']} dudosas sin etiquetar (`revisar` para verlas). Son las que importan.")


def estado():
    _resumen(_cargar())


def revisar():
    for f in _cargar():
        if f["etiqueta"] == "PENDIENTE":
            print(f"\n[{f['id']}] {f['texto'][:300]}")
            print(f"      fuente: {f['fuente']}")
    print("\netiquetar:  python3 tools/eval_triage.py etiquetar <id> <tarea|no>")


def endurecer():
    """Aplica `_tapar_residuo` al set YA generado, en el sitio, sin tocar ids ni etiquetas.

    Por qué no `extraer` otra vez: las fuentes cambian (dudosas, Tablero) y `etiquetar` escribe
    en este fichero, así que regenerar puede pisar etiquetas puestas a mano. Idempotente."""
    filas = _cargar()
    total = 0
    for f in filas:
        f["texto"], n = _tapar_residuo(f["texto"])
        if n:
            f["redacciones"] = f.get("redacciones", 0) + n
            total += n
    with open(SALIDA, "w", encoding="utf-8") as fh:
        for f in filas:
            fh.write(json.dumps(f, ensure_ascii=False) + "\n")
    print(f"✓ {total} redacciones nuevas en {len(filas)} casos → {SALIDA}")
    return total


def etiquetar(tid, etiqueta):
    if etiqueta not in ("tarea", "no"):
        raise SystemExit("⛔ la etiqueta es 'tarea' o 'no'")
    filas = _cargar()
    for f in filas:
        if f["id"] == tid:
            f["etiqueta"] = etiqueta
            break
    else:
        raise SystemExit(f"⛔ no existe el caso {tid}")
    with open(SALIDA, "w", encoding="utf-8") as fh:
        for f in filas:
            fh.write(json.dumps(f, ensure_ascii=False) + "\n")
    print(f"✓ {tid} → {etiqueta}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "estado"
    if cmd == "extraer":
        extraer()
    elif cmd == "estado":
        estado()
    elif cmd == "revisar":
        revisar()
    elif cmd == "endurecer":
        endurecer()
    elif cmd == "etiquetar":
        etiquetar(sys.argv[2], sys.argv[3])
    else:
        raise SystemExit(__doc__)
