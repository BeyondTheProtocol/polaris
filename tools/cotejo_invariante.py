#!/usr/bin/env python3
"""tools/cotejo_invariante.py — EL CORTAFUEGOS DE COTEJO OBLIGATORIO del Guardián-bisturí.

POR QUÉ EXISTE (el error que para, problema-primero)
----------------------------------------------------
El 27/6/26 el sistema afirmó un hecho clínico FALSO de {{TITULAR}} ("RB1 no está en su perfil")
razonando «por ausencia» desde la lista PÚBLICA de dianas, sin abrir el informe primario. La
verdad estaba escrita en su fuente real: una plataforma NO ve el marcador y otra posterior SÍ. El fallo fue de CLASE, no de caso: (a) colapsar dos estados temporales/plataformas
en uno, (b) razonar «no existe» desde una compilación incompleta, (c) no abrir la fuente primaria.

La procedencia PASIVA (un enlace por dato) NO habría parado el error: no obliga a seguir el enlace,
y una compilación incompleta da MÁS confianza en lo falso. Lo que PARA el error es un INVARIANTE
de cotejo OBLIGATORIO, fail-closed, en el MOMENTO de afirmar. Eso es esta capa.

QUÉ HACE (determinista, sin modelo — principio #0: lo certifica el CÓDIGO, no la palabra del modelo)
---------------------------------------------------------------------------------------------------
Sobre cada pieza VIVA del Dosier-JSONL cuya `afirmacion` afirme PRESENCIA o AUSENCIA de una
alteración (mutación / variante / pérdida / amplificación / marcador), exige y comprueba:

  1. COTEJO COMPLETO Y TIPADO. Debe traer un bloque `cotejo` con:
       · `fuente`     → ruta REAL a `_PRIVADO_CLINICO/` (no la lista pública, no un genérico).
       · `fecha`      → AAAA-MM-DD (de qué FECHA es esa fuente).
       · `plataforma` → de qué PLATAFORMA/ensayo ({{FUENTE_A}}, ctDNA Guardant 2026, ...).
       · `tipo_resultado` ∈ {presencia, ausencia, otro}.
     Falta cualquiera, o un sub-campo desconocido → BLOQUEA (fail-closed, allowlist cerrada).

  2. LA AUSENCIA NUNCA ES «NO EXISTE». Una afirmación de ausencia ("RB1 ausente / no presente /
     wild-type / sin RB1") SOLO pasa si su cotejo es un resultado NEGATIVO REAL de una fuente que
     pudo verlo (`tipo_resultado: "ausencia"` + fuente primaria con esa fecha/plataforma). Si el
     cotejo no lo respalda, la pieza se BLOQUEA con el enrutado canónico: NO afirmes «no existe»,
     afirma «no confirmado en la fuente que tengo» y enruta a abrir la fuente primaria / la otra
     plataforma. Razonar la ausencia desde la LISTA PÚBLICA de dianas = el error de hoy = se bloquea.

  3. CHEQUEO ADVERSARIAL DE FECHA/PLATAFORMA. Una alteración puede ESTAR en una plataforma/fecha y
     NO en otra (justo el caso RB1: {{FUENTE_A}} no, {{FUENTE_B}} sí). Por eso el cotejo es PER-FUENTE:
     la afirmación queda atada a SU fecha+plataforma. Colapsar dos estados (afirmar en absoluto
     "RB1 ausente" cuando una fuente posterior lo ve) lo cazan, además, los invariantes de id/
     contradicción de L1 (dos piezas vivas del mismo hecho).

QUÉ NO HACE
-----------
NO concluye clínica (equipa, no concluye). NO decide qué alteración liga con qué ensayo (eso es la
cuarentena de dominio, firma de {{CONTACTO}}). NO lee el contenido del informe: comprueba que el PUNTERO
sea a un informe que EXISTE en la bóveda (`fuente_clinica`, desde el 24-sep-26: antes bastaba la
forma, auditoría Gorgojo 1.6) y que la FORMA del cotejo sea completa y coherente con la afirmación.
La FIDELIDAD del puntero (que el informe diga de verdad lo que la pieza afirma) la valida {{TITULAR}}.

MURO: solo afirmaciones estructuradas + PUNTEROS a fuente (rutas), nunca secuencias/VCF/HLA crudos.
Pasa `muro.py`. La fuente es una RUTA a `_PRIVADO_CLINICO/` (local), nunca el contenido crudo.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fuente_clinica  # noqa: E402

# ── Léxico determinista (ES) ─────────────────────────────────────────────────
# Una afirmación es de PRESENCIA/AUSENCIA si menciona el verbo de estado de una alteración.
# La RED es deliberadamente amplia: ante la duda, EXIGE cotejo (default-careful). Que sobre-cubra
# es seguro (a lo sumo pide un cotejo de más); que infra-cubra es el error de hoy.
# Recalibración 27/6/26 (red-team de verificación): el detector NO puede ser la única puerta léxica;
# se cerró la CLASE — gen en minúscula, sinónimos de ausencia (descartado/excluido/no reportado),
# cuantificadores de negación (jamás/nunca/ningún) e inglés. Default INVERTIDO para ausencia.
_ALTERACION = (
    "mutación", "mutacion", "variante", "alteración", "alteracion",
    "pérdida", "perdida", "deleción", "delecion", "amplificación", "amplificacion",
    "ganancia",  # ganancia de nº de copia (CNV) = alteración: exige cotejo igual que amplificación
    "fusión", "fusion", "marcador", "expresión", "expresion", "wild-type", "wild type",
    "wildtype", "alterad", "mutad", "diana",
)
# Verbos/giros de AUSENCIA (ES+EN). Cubre la CLASE, no un caso: negaciones, sinónimos clínicos
# (descartado/excluido), giros de "no se informó/observó", cuantificadores, y inglés.
_AUSENCIA = (
    "no está", "no esta", "no aparece", "no presente", "ausente", "ausencia",
    "negativ", "sin ", "no se detect", "no detectad", "no hay", "no figura",
    "no consta", "wild-type", "wild type", "wildtype", "no porta", "no presenta",
    "no tiene", "no muestra", "fuera del perfil", "no está en el perfil",
    "no esta en el perfil", "no en el perfil", "no incluye", "no incluy",
    "descartad", "excluid", "no report", "no informad", "no observ", "no se observ",
    "no detect", "no se encontr", "no encontrad", "no relevante", "no se identific",
    "ningún hallazgo", "ningun hallazgo", "ningún", "ningun", "limpio", "normal para",
    "jamás detect", "jamas detect", "nunca se detect", "nunca detect",
    # inglés (por si una afirmación entra en EN)
    "not detected", "not present", "absent", "no evidence of", "negative for",
    "wild type", "not found", "not reported",
)
# Verbos/giros de PRESENCIA: "está", "presente", "positivo", "detectad", "porta", "alterad"...
# OJO: un verbo de presencia NEGADO ("no detectad", "no presenta", "jamás detectado") es AUSENCIA,
# no presencia. La detección de sentido (abajo) descuenta los verbos de presencia precedidos de
# CUALQUIER negación (no/sin/nunca/jamás/ningún) para no clasificar la ausencia como presencia.
_PRESENCIA = (
    "presente", "positiv", "detectad", "se detect", "porta", "presenta",
    "alterad", "mutad", "amplificad", "perdid", "delecionad", "fusionad",
    "confirmad", "hallazgo", "encontrad", "en el perfil", "consta", "ganancia",
)
# Cuantificadores/giros de NEGACIÓN que, antes de un verbo de presencia, lo vuelven ausencia.
_NEG_RE = re.compile(r"\b(no|sin|nunca|jam[aá]s|ning[uú]n|ning[uú]na|ni)\b\s*\S*$")
# Símbolo de gen: HGNC-like. Se casa SOBRE EL TEXTO ORIGINAL en \bMAYÚSCULAS\b, y TAMBIÉN sobre
# minúsculas para no dejar pasar «rb1 no está» (el bypass por capitalización del incidente).
_GEN_MAYUS_RE = re.compile(r"\b[A-Z][A-Z0-9]{1,9}\b")
_GEN_SHAPE_RE = re.compile(r"\b[a-zA-Z]{2,6}\d{1,3}[a-zA-Z]?\b")  # tipo rb1, tp53, fgfr1, pik3ca
# Siglas que NO son genes (imagen/logística/formato): reducen el sobre-bloqueo (P2) sin abrir hueco,
# porque solo descuentan el match en MAYÚSCULAS; si la afirmación trae un gen REAL aparte, igual casa.
# OJO: NO se excluyen biomarcadores reales (p. ej. TMB, marcador de carga mutacional, que es una
# diana pública del caso): ante la duda de si una sigla es diana, se DEJA dentro (default-careful).
_NO_GEN = {"RMN", "TAC", "TC", "PET", "PDF", "RM", "RX", "EKG", "ECG", "PCR",
           "ADN", "ARN", "DNA", "RNA", "MRI", "PNV", "HOY", "URL"}

# Punteros que NO valen como fuente primaria para una afirmación de presencia/ausencia.
# La lista PÚBLICA de dianas es el corazón del error de hoy: razonar "ausente" desde ella se bloquea.
_FUENTE_PROHIBIDA = (
    "dianas", "lista pública", "lista publica", "dianas-públicas", "dianas-publicas",
    "publico", "público", "publica", "pública", "genérico", "generico",
    "de memoria", "recuerdo", "creo que", "wiki",
)
# La fuente DEBE apuntar a la bóveda clínica local, como SEGMENTO de ruta real (no substring: así
# «algo_PRIVADO_CLINICO_inventado» o «..._fake/...» NO cuelan).
_RAIZ_CLINICA = "_PRIVADO_CLINICO"
_RAIZ_SEGMENTO_RE = re.compile(r"(^|[\\/])_PRIVADO_CLINICO([\\/])")
# Aun DENTRO de la bóveda, un fichero que huela a COMPILACIÓN/lista/resumen NO es un informe
# primario: razonar la ausencia desde una compilación interna es el error de hoy con otra ruta.
_NOMBRE_COMPILACION = ("resumen", "compilacion", "compilación", "lista", "listado", "notas",
                       "indice", "índice", "maestro", "perfil-molecular", "dianas", "checklist")
# Un informe primario tiene forma de DOCUMENTO/dato de informe (extensión reconocible).
_EXT_INFORME = (".pdf", ".docx", ".doc", ".xml", ".json", ".csv", ".tsv", ".txt", ".html", ".htm")

# Allowlist CERRADA del sub-bloque `cotejo` (fail-closed, mismo patrón que el schema de L1).
_COTEJO_CAMPOS = {"fuente", "fecha", "plataforma", "tipo_resultado"}
_COTEJO_REQUERIDOS = {"fuente", "fecha", "plataforma", "tipo_resultado"}
_TIPO_RESULTADO = {"presencia", "ausencia", "otro"}

_FECHA_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# Caveat canónico de enrutado (una sola vez por pieza): qué decir en vez de "no existe".
ENRUTAR = ("no afirmes «no existe» → di «no confirmado en la fuente que tengo» y enruta: "
           "abre la fuente primaria y coteja la otra fecha/plataforma antes de concluir ausencia")


def _norm(s):
    return (s or "").lower()


def _menciona_gen(afirmacion):
    """True si la afirmación nombra algo CON FORMA de símbolo de gen (RB1, TP53, FGFR1, pik3ca).

    Doble red: \\bMAYÚSCULAS\\b sobre el texto original (HGNC clásico) Y forma letras+dígitos en
    cualquier capitalización (caza «rb1» en minúscula — el bypass del incidente 27/6/26)."""
    s = afirmacion or ""
    for m in _GEN_MAYUS_RE.finditer(s):
        if m.group(0) not in _NO_GEN:
            return True
    # Forma letras+dígitos (rb1, tp53) — caza el gen en minúscula; las siglas-no-gen no traen dígito.
    return bool(_GEN_SHAPE_RE.search(s))


def afirma_estado_alteracion(afirmacion):
    """(es_estado, sentido). sentido ∈ {presencia, ausencia, ambiguo, None}.

    es_estado=True si la afirmación habla del ESTADO (presencia/ausencia) de una alteración.
    Determinista; DEFAULT-CAREFUL e INVERTIDO para ausencia: si nombra un gen y hay CUALQUIER señal
    de negación/ausencia, es estado-ausencia y exige cotejo (no se fía de una lista de verbos cerrada;
    el error de hoy fue justo infra-cubrir). Ante duda de sentido → 'ambiguo' (también exige cotejo).
    """
    t = _norm(afirmacion)
    tiene_gen = _menciona_gen(afirmacion)
    hay_ausencia = any(k in t for k in _AUSENCIA)
    hay_alteracion = any(k in t for k in _ALTERACION)
    # ¿hay CUALQUIER negación en el texto? (no/sin/nunca/jamás/ningún/ni) — señal amplia de ausencia.
    hay_negacion = bool(re.search(r"\b(no|sin|nunca|jam[aá]s|ning[uú]n|ning[uú]na|ni)\b", t))

    # Presencia NEGADA = ausencia: un verbo de presencia precedido de CUALQUIER negación NO cuenta
    # como presencia. Así «RB1 no se detecta», «RB1 jamás detectado», «ningún hallazgo de RB1» no
    # salen 'presencia' ni 'ambiguo', sino 'ausencia'.
    hay_presencia = False
    for k in _PRESENCIA:
        for m in re.finditer(re.escape(k), t):
            pre = t[max(0, m.start() - 12):m.start()]
            if _NEG_RE.search(pre):
                continue  # verbo de presencia negado → no es señal de presencia
            hay_presencia = True
            break
        if hay_presencia:
            break

    # Es afirmación de estado de alteración si:
    #  (a) menciona explícitamente una alteración (mutación/variante/pérdida/marcador…), O
    #  (b) nombra un GEN y hay señal de ausencia/negación (DEFAULT INVERTIDO: la ausencia de un gen
    #      no necesita un verbo del léxico — basta gen + negación), O
    #  (c) nombra un GEN y hay un verbo de presencia.
    es_estado = (hay_alteracion
                 or (tiene_gen and (hay_ausencia or hay_negacion))
                 or (tiene_gen and hay_presencia))
    if not es_estado:
        return False, None
    # Sentido. La negación pesa: si hay ausencia/negación y NO una presencia no-negada → ausencia.
    if (hay_ausencia or hay_negacion) and not hay_presencia:
        return True, "ausencia"
    if hay_presencia and not (hay_ausencia or hay_negacion):
        return True, "presencia"
    return True, "ambiguo"


def _validar_cotejo(cotejo):
    """Violaciones de FORMA del sub-bloque `cotejo` (allowlist cerrada). Lista de strings."""
    v = []
    if not isinstance(cotejo, dict):
        return ["el bloque `cotejo` debe ser un objeto JSON"]
    desconocidos = set(cotejo) - _COTEJO_CAMPOS
    if desconocidos:
        v.append("sub-campo(s) de `cotejo` DESCONOCIDO(s) %s → rechazado (allowlist cerrada)"
                 % sorted(desconocidos))
    faltan = _COTEJO_REQUERIDOS - set(cotejo)
    if faltan:
        v.append("`cotejo` incompleto: falta(n) %s (fuente+fecha+plataforma+tipo_resultado son "
                 "OBLIGATORIOS para afirmar presencia/ausencia)" % sorted(faltan))
    fuente = cotejo.get("fuente")
    if "fuente" in cotejo:
        if not isinstance(fuente, str) or not fuente.strip():
            v.append("`cotejo.fuente` vacía o no es texto")
        else:
            fl = fuente.lower()
            base = re.split(r"[\\/]", fl)[-1]  # nombre de fichero (último segmento)
            if any(p in fl for p in _FUENTE_PROHIBIDA):
                v.append("`cotejo.fuente` apunta a una fuente NO primaria (lista pública/genérico/"
                         "memoria): «%s». El estado de una alteración se coteja contra el INFORME "
                         "primario, no contra la lista de dianas (el error del 27/6/26)." % fuente)
            elif not _RAIZ_SEGMENTO_RE.search(fuente):
                # Debe ser un SEGMENTO de ruta real (_PRIVADO_CLINICO entre separadores), no substring.
                v.append("`cotejo.fuente` no apunta a la bóveda clínica local como ruta real "
                         "(%s/...): «%s» → fail-closed (sin puntero a la fuente real no se coteja)"
                         % (_RAIZ_CLINICA, fuente))
            elif any(c in base for c in _NOMBRE_COMPILACION):
                v.append("`cotejo.fuente` parece una COMPILACIÓN/lista/resumen, no un informe "
                         "primario: «%s». Razonar la ausencia desde una compilación interna es el "
                         "error del 27/6/26 con otra ruta → coteja contra el informe de origen." % fuente)
            elif not base.endswith(_EXT_INFORME):
                v.append("`cotejo.fuente` no apunta a un INFORME (extensión reconocible %s): «%s» "
                         "→ fail-closed (el puntero debe ser al documento primario, no a una carpeta)"
                         % ("/".join(_EXT_INFORME), fuente))
            else:
                # La FORMA no basta: el informe tiene que EXISTIR en la bóveda (auditoría Gorgojo
                # 1.1/1.6, 24-sep-26 — misma clase que el panel de alto riesgo, mismo resolvedor).
                # La fidelidad del contenido sigue siendo de {{TITULAR}}; que el puntero no apunte a
                # nada, ya no.
                r = fuente_clinica.cotejar(fuente, quien="cotejo_invariante")
                if not r["ok"]:
                    v.append("`cotejo.fuente` no es un informe real de la bóveda (%s): %s"
                             % (r["estado"], r["motivo"]))
    fecha = cotejo.get("fecha")
    if "fecha" in cotejo and not (isinstance(fecha, str) and _FECHA_RE.match(fecha)):
        v.append("`cotejo.fecha` no es AAAA-MM-DD: %r (hay que datar de qué fecha es la fuente)" % fecha)
    plat = cotejo.get("plataforma")
    if "plataforma" in cotejo and (not isinstance(plat, str) or not plat.strip()):
        v.append("`cotejo.plataforma` vacía (hay que decir de qué plataforma/ensayo es la fuente: "
                 "una alteración puede estar en una plataforma y no en otra)")
    tr = cotejo.get("tipo_resultado")
    if "tipo_resultado" in cotejo and tr not in _TIPO_RESULTADO:
        v.append("`cotejo.tipo_resultado` inválido: %r (válidos: %s)" % (tr, sorted(_TIPO_RESULTADO)))
    return v


def evaluar_cotejo(piezas, *, solo_vivas=True):
    """Cortafuegos de cotejo sobre una lista de piezas (dicts). Devuelve lista de bloqueos.

    Para cada pieza cuya afirmación afirme PRESENCIA/AUSENCIA de una alteración:
      · exige `cotejo` completo y bien formado (fuente clínica real + fecha + plataforma + tipo),
      · si afirma AUSENCIA, exige que el cotejo sea un negativo REAL (tipo_resultado='ausencia');
        si no, BLOQUEA con el enrutado canónico (no afirmar «no existe»).
    Fail-closed: cualquier violación → bloqueo (la pieza no es entregable).
    """
    bloqueos = []
    for p in piezas:
        if not isinstance(p, dict):
            continue  # las piezas no-objeto las caza el schema de L1
        if solo_vivas and p.get("status") != "vivo":
            continue
        pid = p.get("id") or "(sin id)"
        es_estado, sentido = afirma_estado_alteracion(p.get("afirmacion"))
        if not es_estado:
            continue  # no es una afirmación de estado de alteración → esta capa no aplica
        cotejo = p.get("cotejo")
        if cotejo is None:
            bloqueos.append("COTEJO OBLIGATORIO: «%s» afirma el estado (%s) de una alteración SIN "
                            "bloque `cotejo` (fuente+fecha+plataforma) → fail-closed. %s"
                            % (pid, sentido, ENRUTAR))
            continue
        vs = _validar_cotejo(cotejo)
        for x in vs:
            bloqueos.append("COTEJO de «%s»: %s" % (pid, x))
        if vs:
            continue  # cotejo mal formado: no seguimos a la coherencia de sentido
        # Coherencia AUSENCIA: una afirmación de ausencia exige un negativo REAL de la fuente.
        tr = cotejo.get("tipo_resultado")
        if sentido == "ausencia" and tr != "ausencia":
            bloqueos.append("AUSENCIA sin negativo real: «%s» afirma AUSENCIA pero su cotejo es "
                            "`tipo_resultado=%r` (no un resultado negativo de la fuente). %s"
                            % (pid, tr, ENRUTAR))
        if sentido == "presencia" and tr == "ausencia":
            bloqueos.append("INCOHERENTE: «%s» afirma PRESENCIA pero su cotejo dice ausencia "
                            "(`tipo_resultado=ausencia`) → revisa fuente/sentido" % pid)
        # 'ambiguo' (presencia y ausencia a la vez en el texto): exige cotejo tipado y se deja pasar
        # si el cotejo es coherente — el sentido lo desambigua el `tipo_resultado`, ya validado.
    return bloqueos


# ── CLI ──────────────────────────────────────────────────────────────────────
def main(argv):
    if not argv:
        print("uso: cotejo_invariante.py <dosier.jsonl>")
        return 2
    import frescura_dosier as fd
    piezas, errores = fd.cargar_jsonl(argv[0])
    if errores:
        for e in errores:
            print("  ✗ %s" % e)
        print("FORMATO INVÁLIDO → fail-closed")
        return 2
    bloqueos = evaluar_cotejo(piezas)
    print("=== Cortafuegos de cotejo obligatorio (determinista, sin modelo) ===")
    if not bloqueos:
        print("✅ sin afirmaciones de presencia/ausencia sin cotejar")
        return 0
    print("🛑 NO entregable (fail-closed):")
    for b in bloqueos:
        print("   - %s" % b)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
