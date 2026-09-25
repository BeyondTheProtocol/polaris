#!/usr/bin/env python3
"""tools/deid.py — De-identificador del NÚCLEO F3b (capa de CONTEXTO para el cerebro local/gratis).

Plan-motor: typed-swinging-wand / F3b. Objetivo: que el cerebro GRATIS/LOCAL (Ollama, egress-cero)
pueda responder CON contexto del caso SIN ver jamás clínico/PII en crudo. Esta capa convierte texto
del caso → versión DE-IDENTIFICADA sin los identificadores que estos patrones reconocen (PII + huella
clínica/genómica específica + términos vetados del muro). Lo que no reconocen pasa: ver HONESTIDAD.

REUSA, NO REINVENTA (SOLO LECTURA sobre `borde.py`):
  · Las MISMAS regex y deny-lists del borde (`borde._RE_*`, `_NOMBRE_TITULAR`, y los deny-lists de
    `seguimiento` que el borde ya consolida: `_TERMINOS_VETADOS`, `_NOMBRES_DENY`).
  · El MISMO normalizador (`borde._normalizar`: NFKD + quita combinantes + zero-width) para cerrar
    la evasión por homoglifo/acento falso/espaciado, ANTES de enmascarar.
  · La revalidación usa el propio juez del muro: tras de-identificar, `borde.clasificar()` sobre la
    salida no debe ver nada. Si ve algo, la de-id se considera fallida. OJO: es el MISMO detector
    que redacta, así que esto caza restos, no demuestra anonimato (auditoría 3.3, 24-sep-26).

POR QUÉ existe (y no basta `borde.de_identificar`): el `de_identificar` de F0 es "mínimo" — cubre un
subconjunto de las clases y NO los deny-lists ni variantes cortas/genotipos/citobandas/exones. Aquí se
enmascara **la UNIÓN de todo lo que `clasificar` detecta**, en orden de especificidad, de forma que la
salida pase el juez del muro. Es un superconjunto estricto de F0, no una copia divergente.

HONESTIDAD (defensa en profundidad, no garantía única): una de-id por patrones SIEMPRE es porosa
(notación nueva, ofuscación que el normalizador no prevé). Por eso F3b es DEFENSA EN PROFUNDIDAD: la
garantía REAL es ARQUITECTÓNICA — lo que necesita juicio clínico va a Claude (trusted), y el cerebro
gratis/local solo recibe contexto ya de-identificado Y revalidado por `borde.clasificar`. La de-id
reduce la superficie; el muro la cierra. Cada salida se REVALIDA: si algo escapa al patrón, el juez
del muro lo caza y el contexto se DESCARTA (fail-closed), no se envía a medio limpiar.

CLI:
  python3 tools/deid.py "<texto>"            # imprime el texto de-identificado + nº de redacciones
  python3 tools/deid.py --check "<texto>"    # ¿quedan identificadores detectables? (exit 0 no / 3 sí)
  python3 tools/deid.py --selftest
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import borde  # noqa: E402 — SOLO LECTURA: regex + deny-lists + normalizador + juez del muro
import seguimiento as seg  # noqa: E402 — deny-lists del muro (la misma fuente que usa el borde)

MARCA = "[REDACTADO]"

# Fechas: el borde NO las trata como sensibles (una fecha sola no identifica), pero en el CONTEXTO
# del caso una fecha concreta (cita, biopsia, viaje) SÍ es un identificador cuasi-único combinado con
# el resto. F3b las enmascara de más (defensa en profundidad propia de la capa de contexto; el borde
# queda intacto). Cubre ISO (2026-07-08), europeo (08/07/2026, 8-7-26) y "8 de julio[ de 2026]".
_MESES = "enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|octubre|noviembre|diciembre"
_RE_FECHA = re.compile(
    r"\b\d{4}-\d{1,2}-\d{1,2}\b"
    r"|\b\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}\b"
    r"|\b\d{1,2}\s+de\s+(?:%s)(?:\s+de\s+\d{2,4})?\b" % _MESES, re.I)

# Orden de enmascarado: de MÁS específico/largo a MÁS corto, para que un patrón amplio no se
# coma el contexto antes de que los específicos hagan su trabajo, y para no dejar restos. Cada
# entrada reusa una regex/deny-list YA definida en el borde (no se redefine ninguna aquí).
# Cubre la UNIÓN de detectores de borde.clasificar():
#   PII:        email, DNI/NIE, teléfono, nombre de {{TITULAR}}, nombres en deny-list
#   genómico:   HGVS (p./c.), HGVS corto (R175H), HLA, rsID, coord. cromosómica, cromosoma,
#               citobanda (del 17p), exón, genotipo VCF (0/1)
#   clínico:    marcador (TP53, Ki-67…), cifra clínica (ER 80%)
#   muro:       términos vetados (vacuna/contacto/neoantíg…)
_REGEX_ORDENADAS = (
    ("email", borde._RE_EMAIL),
    ("dni", borde._RE_DNI),
    ("telefono", borde._RE_PHONE),
    # NHC: el juez del muro lo caza desde el 2-sep-2026, pero si no se REDACTA aqui la de-id
    # sale «SUCIA» y no sirve. Detectar y enmascarar tienen que ir juntos o esto no cierra.
    ("nhc", borde._RE_NHC),
    ("hgvs", borde._RE_VAR),
    ("hla", borde._RE_HLA),
    ("rsid", borde._RE_RS),
    ("coordenada", borde._RE_CHR),
    ("cromosoma", borde._RE_CHR_W),
    ("citobanda", borde._RE_CITO),
    ("exon", borde._RE_EXON),
    ("cifra_clinica", borde._RE_CLIN_PCT),
    ("fecha", _RE_FECHA),
    ("genotipo", borde._RE_GT),
    ("marcador", borde._RE_CLIN),
    ("hgvs_corto", borde._RE_VAR_CORTO),
    ("nombre_titular", borde._NOMBRE_TITULAR),
)


def _enmascarar_deny(texto):
    """Enmascara nombres propios en deny-list y términos vetados del muro, palabra a palabra,
    sobre el texto YA normalizado (mismo criterio que `clasificar`). Reusa los deny-lists del muro
    vía `seguimiento` (la misma fuente que consolida el borde). Devuelve (texto, n)."""
    n = 0

    # nombres propios de la deny-list (match de palabra completa, case-insensitive)
    def _rep_palabra(m):
        nonlocal n
        w = m.group(0)
        if w.lower() in seg._NOMBRES_DENY:
            n += 1
            return MARCA
        return w

    texto = re.sub(r"[A-Za-zÁÉÍÓÚáéíóúÑñ]+", _rep_palabra, texto)

    # términos vetados del muro (substring, case-insensitive) — vacuna/contacto/neoantíg…
    for term in seg._TERMINOS_VETADOS:
        if not term:
            continue
        patt = re.compile(re.escape(term), re.I)
        texto, k = patt.subn(MARCA, texto)
        n += k
    return texto, n


def de_identificar(texto):
    """(texto_deid, n_redacciones). Normaliza igual que el borde y enmascara la UNIÓN de todas las
    clases que `borde.clasificar` detecta. NO envía nada: solo transforma. El llamante DEBE revalidar
    con `sin_identificadores_detectados()` antes de usar la salida (fail-closed)."""
    if not isinstance(texto, str) or not texto.strip():
        return ("" if not isinstance(texto, str) else texto), 0
    # MISMO normalizador que el borde: cierra homoglifo/acento falso/zero-width antes de enmascarar.
    norm, _low = borde._normalizar(texto)
    out = norm
    n = 0
    for _etq, rx in _REGEX_ORDENADAS:
        out, k = rx.subn(MARCA, out)
        n += k
    out, k = _enmascarar_deny(out)
    n += k
    # colapsa marcadores contiguos para que el contexto sea legible ("[REDACTADO] [REDACTADO]" → uno)
    out = re.sub(r"(?:%s)(?:\s+(?:%s))+" % (re.escape(MARCA), re.escape(MARCA)), MARCA, out)
    return out, n


def sin_identificadores_detectados(texto):
    """(ok, motivo) — ¿quedan identificadores QUE ESTOS PATRONES SEPAN VER? ok=True si ni el juez
    del muro `borde.clasificar` (PII + clínico/genómico + términos vetados) ni el detector de fechas
    de F3b encuentran nada. Si algo escapó, False y el contexto NO debe enviarse.

    ⚠️ NO es un certificado de anonimato (24-sep-26, auditoría externa, hallazgo 3.3). Redacta y
    verifica con los MISMOS patrones: un nombre, un domicilio o un diagnóstico que no reconocen
    pasan los dos pasos igual (reproducido: 0 redacciones y «limpio»). Por eso se llamaba `limpio`
    y ya no. Lo que decide si algo del caso puede salir es su PROCEDENCIA (`ia.ask(...,
    sensible_forzado=True)`, ver `contexto_caso`), no este veredicto."""
    sensible, motivo = borde.clasificar(texto)
    if sensible:
        return False, motivo
    if _RE_FECHA.search(texto):
        return False, "fecha superviviente (cuasi-identificador)"
    return True, "sin identificadores detectados"


# Alias DEPRECADO: el nombre prometía más de lo que el código sabe. No lo uses en código nuevo.
limpio = sin_identificadores_detectados


def de_identificar_verificado(texto, procedencia=None):
    """(texto_deid|None, n, ok, motivo). De-identifica Y revalida con los mismos patrones. Si queda
    algo que reconocen, devuelve texto=None (fail-closed): el contexto se DESCARTA, nunca se manda a
    medio redactar. `ok=True` significa «sin identificadores detectados», no «anónimo».

    `procedencia="N2"` (lo que sale del caso): el motivo lo dice. La redacción es la misma; lo que
    cambia es que quien llama NO puede tratar la salida como no sensible (ver `contexto_caso`)."""
    deid, n = de_identificar(texto)
    ok, motivo = sin_identificadores_detectados(deid)
    if ok and procedencia == "N2":
        motivo = "sin identificadores detectados — procedencia N2: sigue siendo sensible"
    return (deid if ok else None), n, ok, motivo


# ── CLI ──────────────────────────────────────────────────────────────────────────────────
def _selftest():
    import subprocess
    r = subprocess.run([sys.executable,
                        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                     "tests", "test_deid.py")])
    return r.returncode


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    if argv[0] == "--selftest":
        return _selftest()
    if argv[0] == "--check":
        deid, n, ok, motivo = de_identificar_verificado(" ".join(argv[1:]))
        print(("◻️ sin identificadores detectados (no certifica anonimato)" if ok else "🛑 quedan identificadores")
              + " — %s  (redacciones: %d)" % (motivo, n))
        if ok:
            print(deid)
        return 0 if ok else 3
    deid, n = de_identificar(" ".join(argv))
    ok, motivo = sin_identificadores_detectados(deid)
    print(deid)
    print("\n— %d redacciones · %s (%s)" %
          (n, "sin identificadores detectados" if ok else "quedan identificadores", motivo), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
