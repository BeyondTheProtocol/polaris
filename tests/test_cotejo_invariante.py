#!/usr/bin/env python3
"""Tests del cortafuegos de cotejo obligatorio (tools/cotejo_invariante.py).

CRITERIO DE ACEPTACIÓN DE ESTA ENTREGA — el test de regresión del error del 27/6/26:
  · Una afirmación tipo «RB1 ausente» citando SOLO la lista pública / {{FUENTE_A}} queda BLOQUEADA.
  · «RB1 alterado» citando la fuente {{FUENTE_B}} (Guardant) PASA.
Si este test no es verde, la entrega no está terminada.

Determinista, sin red, sin modelo. Demuestra el EFECTO (qué bloquea / qué deja pasar), no solo
que el código corre. Las B2 (dianas) van como FIXTURES en BORRADOR: el mecanismo se verifica aquí;
la FIDELIDAD de cada cotejo vs el informe primario la valida {{TITULAR}} (no la da por buena este test).
"""
import os
import sys
from datetime import datetime, timezone

TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
sys.path.insert(0, TOOLS)
import cotejo_invariante as cot       # noqa: E402 — la capa bajo prueba
import dosier_invariantes as di       # noqa: E402 — cableado al veredicto único

AHORA = datetime(2026, 6, 27, tzinfo=timezone.utc)
fallos = 0
total = 0


def check(nombre, cond):
    global fallos, total
    total += 1
    print(("  ✓ " if cond else "  ✗ ") + nombre)
    if not cond:
        fallos += 1


def pieza(afirmacion, cotejo=None, **kw):
    """Pieza VÁLIDA y FRESCA por defecto (para que solo el cotejo decida el veredicto)."""
    p = {"id": kw.pop("id", "X"), "bloque": kw.pop("bloque", "B2"), "afirmacion": afirmacion,
         "fuentes": ["src"], "confianza": "media", "status": "vivo",
         "confirmado_en": "2026-06-25T00:00:00Z", "ttl_dias": 30}
    if cotejo is not None:
        p["cotejo"] = cotejo
    p.update(kw)
    return p


# ════════════════════════════════════════════════════════════════════════════
#  EL TEST DE REGRESIÓN DEL ERROR DE HOY (RB1) — criterio de aceptación
# ════════════════════════════════════════════════════════════════════════════
# El error: afirmar «RB1 no está en el perfil» razonando por AUSENCIA desde la lista pública /
# {{FUENTE_A}}, sin abrir la fuente primaria; cuando la verdad es que {{FUENTE_B}} (Guardant) SÍ
# lo ve. Tres formas del error deben quedar BLOQUEADAS; la afirmación correcta debe PASAR.

# 1) «RB1 ausente» citando SOLO la lista pública de dianas → BLOQUEADO (el corazón del error).
rb1_lista_publica = pieza(
    "RB1 no está en el perfil molecular de la paciente",
    cotejo={"fuente": "00_FUENTE-DE-VERDAD/dianas-públicas.md", "fecha": "2026-06-25",
            "plataforma": "lista pública", "tipo_resultado": "ausencia"})
bloq = cot.evaluar_cotejo([rb1_lista_publica])
check("«RB1 ausente» citando la LISTA PÚBLICA → BLOQUEADO",
      any("no primaria" in b.lower() or "lista de dianas" in b.lower() for b in bloq))

# 2) «RB1 ausente» SIN bloque cotejo (afirmación pelada, como el error) → BLOQUEADO + enruta.
rb1_pelado = pieza("RB1 ausente; no figura en el perfil")
bloq = cot.evaluar_cotejo([rb1_pelado])
check("«RB1 ausente» sin cotejo → BLOQUEADO con enrutado (no afirmar «no existe»)",
      any("COTEJO OBLIGATORIO" in b for b in bloq) and any("no confirmado" in b for b in bloq))

# 3) «RB1 ausente» citando {{FUENTE_A}} pero marcándolo como PRESENCIA (sentido incoherente con
#    el resultado) — o como ausencia SIN ser un negativo real → BLOQUEADO.
#    Caso fino: afirma ausencia, cotejo dice tipo_resultado='otro' (no un negativo real).
rb1_tejido_sin_negativo = pieza(
    "RB1 no se detecta en la paciente",
    cotejo={"fuente": "_PRIVADO_CLINICO/biopsia-{{FUENTE_A}}/informe.pdf", "fecha": "2024-03-10",
            "plataforma": "panel tejido 2024", "tipo_resultado": "otro"})
bloq = cot.evaluar_cotejo([rb1_tejido_sin_negativo])
check("«RB1 ausente» con cotejo que NO es un negativo real (tipo='otro') → BLOQUEADO",
      any("AUSENCIA sin negativo real" in b for b in bloq))

# 4) ✅ LA AFIRMACIÓN CORRECTA: «RB1 alterado» citando la fuente {{FUENTE_B}} (Guardant) → PASA.
#    (Presencia, con su fuente primaria local + fecha + plataforma.)
rb1_guardant = pieza(
    "RB1 alterado (variantes detectadas) en la paciente",
    cotejo={"fuente": "_PRIVADO_CLINICO/{{FUENTE_B}}/guardant-informe.pdf", "fecha": "2026-04-15",
            "plataforma": "ctDNA Guardant360 2026", "tipo_resultado": "presencia"})
bloq = cot.evaluar_cotejo([rb1_guardant])
check("✅ «RB1 alterado» citando {{FUENTE_B}} (Guardant), fuente primaria → PASA (sin bloqueo)",
      bloq == [])

# 5) AUSENCIA LEGÍTIMA con negativo real: «RB1 no detectado en {{FUENTE_A}}» con un cotejo que
#    SÍ es un resultado negativo de esa plataforma → PASA (la ausencia atada a SU fecha/plataforma
#    es legítima; lo que NO vale es colapsarla a «no existe» en absoluto).
rb1_tejido_negativo = pieza(
    "RB1 no detectado en el panel de tejido de 2024",
    cotejo={"fuente": "_PRIVADO_CLINICO/biopsia-{{FUENTE_A}}/informe.pdf", "fecha": "2024-03-10",
            "plataforma": "panel tejido 2024", "tipo_resultado": "ausencia"})
bloq = cot.evaluar_cotejo([rb1_tejido_negativo])
check("ausencia LEGÍTIMA (negativo real de {{FUENTE_A}}, atada a su plataforma) → PASA", bloq == [])

# 6) La CLASE completa, cableada al veredicto ÚNICO del Guardián (dosier_invariantes.evaluar):
#    un dosier con la pieza-error (RB1 ausente desde lista pública) NO es entregable.
_, entregable, bloq = di.evaluar([rb1_lista_publica], AHORA)
check("[cableado] dosier con «RB1 ausente»(lista pública) → NO entregable (veredicto único)",
      (not entregable))

# 7) [cableado] el dosier con la afirmación CORRECTA (Guardant) sí pasa el cotejo (el resto del
#    veredicto único puede tener otros requisitos, pero NINGÚN bloqueo es de cotejo).
_, _, bloq = di.evaluar([rb1_guardant], AHORA)
check("[cableado] «RB1 alterado»(Guardant) → sin bloqueo de COTEJO en el veredicto único",
      not any(b.startswith("COTEJO") or "AUSENCIA sin" in b for b in bloq))


# ════════════════════════════════════════════════════════════════════════════
#  Cobertura de la CLASE (no solo el caso RB1) — el cortafuegos generaliza
# ════════════════════════════════════════════════════════════════════════════
# 8) AUSENCIA con fuente de MEMORIA → bloqueado (razonar «de memoria» = el error de hoy).
p = pieza("TP53 no presente", cotejo={"fuente": "lo recuerdo de memoria", "fecha": "2026-06-01",
          "plataforma": "x", "tipo_resultado": "ausencia"})
check("ausencia con fuente 'de memoria' → BLOQUEADO",
      any("no primaria" in b.lower() for b in cot.evaluar_cotejo([p])))

# 9) presencia con fuente fuera de la bóveda clínica (no _PRIVADO_CLINICO) → bloqueado.
p = pieza("PIK3CA mutado", cotejo={"fuente": "notas/algun-resumen.md", "fecha": "2026-06-01",
          "plataforma": "x", "tipo_resultado": "presencia"})
check("presencia con fuente fuera de _PRIVADO_CLINICO → BLOQUEADO",
      any("bóveda clínica" in b for b in cot.evaluar_cotejo([p])))

# 10) cotejo con sub-campo DESCONOCIDO (allowlist cerrada) → bloqueado (fail-closed).
p = pieza("FGFR1 amplificado", cotejo={"fuente": "_PRIVADO_CLINICO/x.pdf", "fecha": "2026-06-01",
          "plataforma": "x", "tipo_resultado": "presencia", "intruso": 1})
check("cotejo con sub-campo desconocido → BLOQUEADO (allowlist cerrada)",
      any("DESCONOCIDO" in b for b in cot.evaluar_cotejo([p])))

# 11) fecha mal formada → bloqueado (hay que datar la fuente).
p = pieza("CDK2 amplificado", cotejo={"fuente": "_PRIVADO_CLINICO/x.pdf", "fecha": "junio 2026",
          "plataforma": "x", "tipo_resultado": "presencia"})
check("fecha no AAAA-MM-DD → BLOQUEADO", any("fecha" in b for b in cot.evaluar_cotejo([p])))

# 12) plataforma vacía → bloqueado (una alteración puede estar en una plataforma y no en otra).
p = pieza("{{DIANA}} expresión positiva", cotejo={"fuente": "_PRIVADO_CLINICO/x.pdf",
          "fecha": "2026-06-01", "plataforma": "", "tipo_resultado": "presencia"})
check("plataforma vacía → BLOQUEADO", any("plataforma" in b for b in cot.evaluar_cotejo([p])))

# 13) NO sobre-bloquea: una afirmación que NO es de presencia/ausencia de una alteración
#     (p. ej. logística) no exige cotejo.
p = pieza("la biopsia se programó para la semana que viene")
check("afirmación NO de estado de alteración → NO exige cotejo (no sobre-bloquea)",
      cot.evaluar_cotejo([p]) == [])

# 14) detector de sentido: presencia / ausencia / ambiguo.
es, s = cot.afirma_estado_alteracion("RB1 alterado y confirmado")
check("detector: 'RB1 alterado' → presencia", es and s == "presencia")
es, s = cot.afirma_estado_alteracion("RB1 ausente, no figura")
check("detector: 'RB1 ausente' → ausencia", es and s == "ausencia")
es, s = cot.afirma_estado_alteracion("se programó la cita del martes")
check("detector: logística → no es estado de alteración", not es)


# ════════════════════════════════════════════════════════════════════════════
#  REGRESIÓN: «ganancia» de nº de copia (CNV) — hueco hallado el 27/6 validando la B2 real.
#  Una GANANCIA es una alteración: debe exigir cotejo igual que una amplificación.
# ════════════════════════════════════════════════════════════════════════════
es, s = cot.afirma_estado_alteracion("ganancias de CCND1 y FGFR1 en ctDNA")
check("detector: 'ganancias de CCND1/FGFR1' → estado de alteración (presencia)", es and s == "presencia")

gan_sin_cotejo = pieza("ganancias de CCND1 y FGFR1 en ctDNA", id="GAN-sincotejo")
check("ganancia SIN cotejo → COTEJO OBLIGATORIO (BLOQUEADO; antes se colaba)",
      bool(cot.evaluar_cotejo([gan_sin_cotejo])))

gan_lista = pieza("ganancia de CCND1", id="GAN-lista",
                  cotejo={"fuente": "dianas-publicas", "fecha": "2026-04-01",
                          "plataforma": "lista pública", "tipo_resultado": "presencia"})
check("ganancia citando lista pública → BLOQUEADO", bool(cot.evaluar_cotejo([gan_lista])))

gan_ok = pieza("ganancia de CCND1 (ctDNA)", id="GAN-ok",
               cotejo={"fuente": "_PRIVADO_CLINICO/{{FUENTE_B}}/guardant360-informe.pdf",
                       "fecha": "2026-04-01", "plataforma": "ctDNA Guardant360 2026",
                       "tipo_resultado": "presencia"})
check("ganancia con informe primario (Guardant) → PASA", not cot.evaluar_cotejo([gan_ok]))


# ════════════════════════════════════════════════════════════════════════════
#  MURO: la pieza estructurada + punteros pasa muro.py (nada de crudo)
# ════════════════════════════════════════════════════════════════════════════
import json  # noqa: E402
sys.path.insert(0, os.path.join(os.path.dirname(TOOLS), "pipeline", "bin"))
try:
    import muro  # noqa: E402
    payload = json.dumps([rb1_guardant, rb1_tejido_negativo], ensure_ascii=False)
    crudo = any(p.search(payload) for p in muro._PROHIBIDO)
    check("MURO: las piezas de cotejo (afirmaciones + punteros) NO traen patrón de crudo/PII",
          not crudo)
except Exception as e:
    check("MURO: muro.py importable para el chequeo", False)
    sys.stderr.write("muro no importable: %r\n" % (e,))


print("RESULTADO cotejo_invariante: %d OK, %d fallos" % (total - fallos, fallos))
print("✅ CORTAFUEGOS DE COTEJO EN VERDE" if not fallos else "❌ revisar fallos")
sys.exit(0 if not fallos else 1)
