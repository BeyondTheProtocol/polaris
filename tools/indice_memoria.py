#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Genera el índice de la memoria durable (MEMORY.md) a partir del frontmatter.

EL PROBLEMA QUE RESUELVE (25-jul-26)
------------------------------------
Claude Code carga SOLO las primeras 200 líneas O 25KB de MEMORY.md, lo que llegue
antes. Lo que sobra **no se carga y nadie avisa**. Con 286 memorias, los slugs por
sí solos ya pesaban 11.5KB y el índice plano iba por 21.9KB: a ~40B por memoria
nueva, el corte silencioso estaba a menos de 100 memorias de distancia.

La doc oficial dice qué hacer: mantener MEMORY.md conciso y mover el detalle a
ficheros por tema. Eso es lo que hace esta tool, y además lo hace DETERMINISTA:
el índice se genera del frontmatter (`name`, `description`, `type`) de cada
memoria, así que no se desincroniza cuando otra sesión escribe una memoria nueva.

  MEMORY.md              índice de TEMAS + las críticas (⭐) + punteros. Se carga siempre.
  _indice-<tema>.md      una línea por memoria de ese tema. Se lee bajo demanda.

Uso:
  python3 tools/indice_memoria.py            # regenera (idempotente)
  python3 tools/indice_memoria.py --check    # solo comprueba tamaños; exit 1 si se pasa
  python3 tools/indice_memoria.py --dry-run  # enseña lo que escribiría

Egress cero, sin LLM, sin red. Fail-open: nunca borra una memoria.
"""
import argparse
import os
import re
import sys

MEM_DIR = os.path.expanduser(
    os.environ.get("BTP_MEMORY_DIR", "~/.claude/projects/-Users-polaris-claudecode/memory")
)
INDICE = "MEMORY.md"
PREFIJO_TEMA = "_indice-"

# Límites REALES del cargador (doc oficial de Claude Code): 200 líneas o 25KB.
# Nos dejamos margen de sobra: el índice debe caber holgado, no rozar el techo.
LIMITE_BYTES = 25 * 1024
LIMITE_LINEAS = 200
OBJETIVO_BYTES = 12 * 1024   # a partir de aquí, avisamos

# Temas: (clave, título visible, [palabras que lo detectan en slug+description]).
# El orden importa: gana el primero que casa. Editable a mano; es la única parte
# con criterio humano y vive aquí, no repartida por el índice.
TEMAS = [
    ("muro", "🧱 Muro, seguridad y privacidad",
     ["muro", "seguridad", "privacidad", "egress", "secreto", "inyeccion", "reidentificacion", "codigo-rojo", "proteger-goal", "clinico-en-historial",
      "censurar", "barrido", "verificacion"]),
    ("ned", "⭐ Estrella polar y estrategia hacia NED",
     ["ned", "estrella", "cadena-objetivos", "biopsia", "vacuna", "dianas", "ensayo",
      "clinical", "tablero-ensayos", "{{CIUDAD}}", "{{CENTRO}}", "contacto", "medicacion",
      "integrativo", "contacto", "screening", "orbita"]),
    ("persona", "🧑 {{TITULAR}}: quién es y cómo trabaja",
     ["user-titular", "altas-capacidades", "learning-style", "insights-trabajar",
      "idioma", "hablar-natural", "modos", "coach", "excelencia", "working-rules",
      "cuidado", "nucleo", "panico", "astral"]),
    ("proceso", "🧭 Cómo trabajo: plan, autonomía y entrega",
     ["plan-primero", "autonomia", "autonomo", "maxima-autonomia", "preguntar",
      "estres", "unknowns", "entregable", "cierre", "una-puerta", "segundo-plano",
      "off-screen", "interfaz", "arbol-decisiones", "destilar", "equipo-primero",
      "humanos-son-peers", "instalar", "copiar-y-mejorar", "modo-build",
      "comprobar", "verificar-efecto", "centralizar", "no-borrar"]),
    ("memoria", "🧠 Memoria, comités y auto-mejora",
     ["memoria", "auto-mejora", "automejora", "comite", "comites", "registro",
      "vara-calidad", "leccion", "minar", "archivar", "buscar-fuera", "notion",
      "reusar", "herramienta-ia-nueva", "cerrar-circulo", "auditoria-uso"]),
    ("voz", "✍️ Voz, escritura y formato",
     ["voz", "escribir", "em-dash", "tell-ia", "rollo-builder", "formato",
      "tabla", "disclaimers", "mensajes-claros", "recordatorios", "casa-estilo",
      "criptico", "comentarios-siempre"]),
    ("web", "🎨 Web, marca y diseño",
     ["web", "marca", "diseno", "copy", "hero", "preview", "netlify", "rebranding",
      "nombres-siguen", "tipograf", "helptitular", "stitch", "puerta-independiente"]),
    ("redes", "📣 Redes, prensa y contactos",
     ["redes", "prensa", "contacto", "contactos", "dossier", "dm", "instagram",
      "linkedin", "twitter", "x-mcp", "guardado", "hilos", "post", "reel",
      "colaboracion", "comunidad", "contacto", "contacto", "contacto", "meta-cuentas",
      "realtitular", "bernardo", "contacto", "contacto", "contacto", "contacto"]),
    ("correo", "📧 Correo y mensajería",
     ["correo", "mail", "gmail", "imap", "outbox", "smtp", "whatsapp", "wa-",
      "wp-", "telegram", "lazo", "acuse", "reaccion", "aviso", "avisos"]),
    ("tareas", "🗂️ Vega, tareas y agenda",
     ["vega", "asistente", "tarea", "tareas", "tarjeta", "tablero", "seguimiento",
      "agenda", "calendario", "fuente-unica", "gestion-tareas", "conserje",
      "reservas", "carrito", "sincronizar"]),
    ("git", "🌱 Git, ramas y trabajo en paralelo",
     ["git", "rama", "ramas", "worktree", "worktrees", "paralelo", "casa-base",
      "fusion", "reconciliacion", "test-all", "cola", "serializar", "estado-vivo",
      "deploy", "release"]),
    ("coste", "💸 Coste, modelos y herramientas de IA",
     ["coste", "gasto", "token", "tokens", "credito", "modelo", "ultracode",
      "deep-research", "nvidia", "perplexity", "grok", "consensus", "scite",
      "claude-code", "proveedor", "colab", "agent-browser", "openwebui", "jaula",
      "desacoplar", "herramientas-externas", "elicit", "contacto", "fugu", "helmcode",
      "workflow", "loop-redteam", "apis", "evidencia", "citas", "convergencia",
      "no-confiar", "sobreingenierizar", "overhead", "scraping", "copiar-apps",
      "mantente-al-dia", "capacidades", "proveedores-no-entrenar"]),
    ("polaris", "⚙️ Polaris: la máquina y sus subsistemas",
     ["polaris", "daemon", "launchd", "observatorio", "staging", "backup",
      "cronica", "diario", "constelacion", "caja", "monitor", "scibot", "vpn",
      "consola", "colima", "nord", "computer-use", "bash-icloud", "mac",
      "salud", "auto-detectar", "arreglar-errores", "healthcheck", "espejo",
      "aparcados", "parked", "inventario", "clon-voz", "pdf", "postdicom",
      "egarante", "drive", "ruflo", "beyond-the-protocol", "visibilidad"]),
    ("dinero", "💳 Dinero, compras y legal",
     ["pago", "pagos", "compra", "compras", "donacion", "donaciones", "factura",
      "legal", "asociacion", "precios", "amazon", "gadgets"]),
]
TEMA_OTROS = ("otros", "📎 Otras")

# Las que se enlazan SIEMPRE desde el índice raíz, aunque el resto de su tema no
# esté cargado. Lista curada a mano (hereda las ⭐ del índice viejo + las reglas
# inquebrantables): detectarlo por heurística daba falsos positivos, y esto es
# justo lo que no puede fallar.
CRITICAS = [
    "feedback-codigo-rojo",
    "feedback-proteger-goal-incluso-de-titular",
    "project-estrella-polar-ned-vs-ruta",
    "feedback-cadena-objetivos-hacia-ned",
    "feedback-todo-acerca-a-ned-no-solo-clinico",
    "feedback-comite-valida-sugerencias",
    "feedback-titular-valida-tecnico-contacto-clinico",
    "feedback-honestidad-limites-avisar-no-inventar",
    "feedback-verificar-identidad-paciente-en-informe",
    "feedback-muro-egress-no-nacionalidad",
    "feedback-plan-primero-luego-ejecutar",
    "feedback-modo-maxima-autonomia",
    "feedback-autonomo-saber-estado-no-preguntar",
    "feedback-equipo-primero-titular-ultimo-recurso",
    "feedback-humanos-son-peers",
    "feedback-copiar-y-mejorar-sin-preguntar-instalar-si",
    "feedback-mentalidad-excelencia-descartes",
    "feedback-coste-nunca-corta-ned-pide-aprobacion",
    "feedback-ultracode-para-lo-ultraimportante",
    "feedback-no-confiar-de-mas-en-polaris-examinar-externo",
    "feedback-fuente-unica-exhaustiva-no-slice",
    "feedback-entregable-no-vive-solo-en-chat",
    "feedback-no-tocar-copy-web-sin-ok",
    "feedback-auto-detectar-resolver-problemas",
    "feedback-comprobar-si-lo-hizo-solo",
    "feedback-ahorrar-tokens-no-sobreexplicar",
]


def _frontmatter(texto):
    """Devuelve (dict_frontmatter, cuerpo). Tolerante: si no hay, devuelve ({}, texto)."""
    if not texto.startswith("---"):
        return {}, texto
    fin = texto.find("\n---", 3)
    if fin == -1:
        return {}, texto
    cabecera, cuerpo = texto[3:fin], texto[fin + 4:]
    campos = {}
    for linea in cabecera.splitlines():
        m = re.match(r"^([a-zA-Z_]+):\s*(.*)$", linea)
        if m:
            campos[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return campos, cuerpo


def _titulo_desde_slug(slug):
    """`feedback-no-tocar-copy-web-sin-ok` → `No tocar copy web sin ok`."""
    partes = slug.split("-")
    if partes and partes[0] in ("feedback", "project", "reference", "user", "insights"):
        partes = partes[1:]
    return " ".join(partes).capitalize() if partes else slug


def _tema_de(slug, descripcion):
    heno = (slug + " " + descripcion).lower()
    for clave, _titulo, palabras in TEMAS:
        for palabra in palabras:
            if palabra in heno:
                return clave
    return TEMA_OTROS[0]


def cargar_memorias():
    """Lee todas las memorias (menos el índice y los índices temáticos)."""
    memorias = []
    for nombre in sorted(os.listdir(MEM_DIR)):
        if not nombre.endswith(".md"):
            continue
        if nombre == INDICE or nombre.startswith(PREFIJO_TEMA):
            continue
        ruta = os.path.join(MEM_DIR, nombre)
        try:
            with open(ruta, encoding="utf-8") as fh:
                texto = fh.read()
        except OSError:
            continue
        campos, cuerpo = _frontmatter(texto)
        slug = campos.get("name") or nombre[:-3]
        desc = campos.get("description") or re.sub(r"\s+", " ", cuerpo).strip()[:160]
        memorias.append({
            "fichero": nombre,
            "slug": slug,
            "desc": desc,
            "tipo": campos.get("type", ""),
            "critica": slug in CRITICAS,
            "tema": _tema_de(slug, desc),
        })
    return memorias


def _linea_indice(m):
    return "- [%s](%s) — %s" % (_titulo_desde_slug(m["slug"]), m["fichero"], m["desc"])


def construir(memorias):
    """Devuelve {fichero: contenido} con el índice raíz y los índices temáticos."""
    por_tema = {}
    for m in memorias:
        por_tema.setdefault(m["tema"], []).append(m)

    salida = {}
    titulos = {c: t for c, t, _ in TEMAS}
    titulos[TEMA_OTROS[0]] = TEMA_OTROS[1]

    # Índices temáticos: una línea por memoria, con su description entera.
    for clave, lista in por_tema.items():
        cuerpo = ["# %s" % titulos.get(clave, clave),
                  "",
                  "Índice temático de la memoria durable. Lo genera `tools/indice_memoria.py`:",
                  "no lo edites a mano, edita la memoria y regenera.",
                  ""]
        for m in sorted(lista, key=lambda x: x["slug"]):
            cuerpo.append(_linea_indice(m))
        cuerpo.append("")
        salida["%s%s.md" % (PREFIJO_TEMA, clave)] = "\n".join(cuerpo)

    # Índice raíz: temas + las críticas. Es lo ÚNICO que se carga en cada sesión.
    raiz = [
        "# Memoria — índice raíz",
        "",
        "Generado por `tools/indice_memoria.py` (no editar a mano: edita la memoria y regenera).",
        "Se cargan solo las primeras 200 líneas o 25KB de este fichero, así que aquí van los",
        "**temas** y las **críticas**. El detalle de cada tema está a un Read de distancia, y el",
        "hook de recall trae la memoria relevante sola en el momento en que hace falta.",
        "",
        "## Temas",
        "",
    ]
    orden = [c for c, _t, _p in TEMAS] + [TEMA_OTROS[0]]
    for clave in orden:
        lista = por_tema.get(clave)
        if not lista:
            continue
        raiz.append("- **[%s](%s%s.md)** — %d %s" % (
            titulos.get(clave, clave), PREFIJO_TEMA, clave, len(lista),
            "memoria" if len(lista) == 1 else "memorias"))
    raiz += ["", "## Las que no se olvidan nunca", ""]
    # Indexado por slug Y por nombre de fichero: hay memorias cuyo `name:` no lleva
    # el prefijo del tipo (p. ej. fichero `feedback-modo-maxima-autonomia.md` con
    # `name: modo-maxima-autonomia`), y una crítica no puede caerse por eso.
    por_slug = {}
    for m in memorias:
        por_slug.setdefault(m["slug"], m)
        por_slug.setdefault(m["fichero"][:-3], m)
    faltan = [s for s in CRITICAS if s not in por_slug]
    for slug in CRITICAS:
        m = por_slug.get(slug)
        if m:
            raiz.append(_linea_indice(m))
    if faltan:
        # Una crítica que ya no existe es un aviso, no un silencio.
        raiz.append("")
        raiz.append("> ⚠️ Críticas listadas que ya no existen como fichero: %s" % ", ".join(faltan))
    raiz += [
        "",
        "## Cómo se usa",
        "",
        "- Buscar una memoria por tema: abre su `_indice-<tema>.md`.",
        "- Buscar por texto libre: `python3 tools/memoria_radar.py resucitar --query \"…\" --dias 0`.",
        "- Añadir una memoria: escribe el fichero con su frontmatter y corre `python3 tools/indice_memoria.py`.",
        "- Reglas de mantenimiento y límites: `.claude/rules/memoria-sistema.md`.",
        "",
    ]
    salida[INDICE] = "\n".join(raiz)
    return salida


def comprobar(texto):
    """(ok, mensajes) contra los límites reales del cargador."""
    nbytes = len(texto.encode("utf-8"))
    nlineas = len(texto.splitlines())
    msgs = []
    ok = True
    if nbytes > LIMITE_BYTES:
        ok = False
        msgs.append("❌ MEMORY.md %d B > límite %d B: lo que sobra NO se carga" % (nbytes, LIMITE_BYTES))
    elif nbytes > OBJETIVO_BYTES:
        msgs.append("⚠️  MEMORY.md %d B (objetivo < %d B, techo %d B)" % (nbytes, OBJETIVO_BYTES, LIMITE_BYTES))
    if nlineas > LIMITE_LINEAS:
        ok = False
        msgs.append("❌ MEMORY.md %d líneas > límite %d" % (nlineas, LIMITE_LINEAS))
    if ok and not msgs:
        msgs.append("✅ MEMORY.md %d B / %d líneas (límites: %d B / %d líneas)" % (
            nbytes, nlineas, LIMITE_BYTES, LIMITE_LINEAS))
    return ok, msgs


def main(argv=None):
    ap = argparse.ArgumentParser(description="Genera el índice de la memoria durable.")
    ap.add_argument("--check", action="store_true", help="solo comprobar tamaños (exit 1 si se pasa)")
    ap.add_argument("--dry-run", action="store_true", help="enseñar sin escribir")
    args = ap.parse_args(argv)

    if not os.path.isdir(MEM_DIR):
        print("ℹ️  No hay directorio de memoria (%s): nada que hacer." % MEM_DIR)
        return 0

    memorias = cargar_memorias()
    ficheros = construir(memorias)
    ok, msgs = comprobar(ficheros[INDICE])

    if args.check:
        ruta = os.path.join(MEM_DIR, INDICE)
        actual = open(ruta, encoding="utf-8").read() if os.path.exists(ruta) else ""
        ok_actual, msgs_actual = comprobar(actual)
        for m in msgs_actual:
            print(m)
        return 0 if ok_actual else 1

    print("%d memorias en %d temas" % (len(memorias), len({m['tema'] for m in memorias})))
    for m in msgs:
        print(m)

    if args.dry_run:
        print("\n--- %s ---\n%s" % (INDICE, ficheros[INDICE]))
        return 0

    # Retirar índices temáticos huérfanos (un tema que se ha quedado sin memorias).
    vigentes = set(ficheros)
    for nombre in os.listdir(MEM_DIR):
        if nombre.startswith(PREFIJO_TEMA) and nombre not in vigentes:
            os.remove(os.path.join(MEM_DIR, nombre))
            print("🧹 retirado índice vacío: %s" % nombre)

    for nombre, contenido in sorted(ficheros.items()):
        ruta = os.path.join(MEM_DIR, nombre)
        tmp = ruta + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(contenido)
        os.replace(tmp, ruta)
    print("✅ escritos %d ficheros de índice en %s" % (len(ficheros), MEM_DIR))
    # Que el aviso le llegue a QUIEN ESCRIBE la memoria, no al siguiente que corra la batería.
    # No se escribe en tools/normas.json desde aquí: está versionado, y hacerlo dejaría casa base
    # sin commitear cada vez que alguien guarda una memoria.
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import normas as _normas
        pend, venc = _normas.sin_clasificar()
        for m in pend + venc:
            print("📝 %s no está en tools/normas.json: clasifícala (clase, mecanismo) antes de %d h, "
                  "o test_normas_registro se pondrá ROJO." % (m, _normas.GRACIA_SIN_CLASIFICAR_S // 3600))
    except Exception:            # noqa: BLE001 — el índice no puede caerse por el aviso
        pass
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
