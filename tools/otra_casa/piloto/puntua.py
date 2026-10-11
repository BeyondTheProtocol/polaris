#!/usr/bin/env python3
"""tools/otra_casa/piloto/puntua.py — la puntuación del piloto de aceptación de Grok como revisor de
otra casa. UNA pasada, determinista y sin red. Aplica las definiciones y el umbral de PROTOCOLO.md
(al lado, escrito y fechado el 10-oct-2026 antes de correr nada): aquí no se decide nada nuevo.

  python3 tools/otra_casa/piloto/puntua.py --corrida ID

Lee `_clave/clave.json` (es el único que la lee) y las respuestas de Claude y de Grok de la corrida,
imprime la tabla por problema y por clase, los incrementales en los dos sentidos y el veredicto, y
escribe `puntuacion.json` en la corrida.

SE NIEGA si:
  · la corrida ya tiene `puntuacion.json` (una pasada es una pasada);
  · falta la respuesta de Claude a algún problema, o el pase de Claude mezcla modelos;
  · falta la de Grok a algún problema y el pase de Grok no acabó en una parada que bloquea el texto;
  · el protocolo, la instrucción o algún problema cambiaron desde que se abrió la corrida, o un
    registro se hizo con otra entrada;
  · la clave no es la del corpus (30 problemas, 20 con error y 10 limpios).

El veredicto, en este orden (gana el primero que se cumple):
  1. negativa o corte del proveedor en un problema del corpus → NO USAR
  2. más de 3 JSON rotos de 30 en algún modelo → INVÁLIDO
  3. incremental de Grok ≤ 1, o falsas alarmas de Grok ≥ 4 → NO USAR
  4. estricto de Grok ≥ 12 y falsas alarmas ≤ 2 e incremental ≥ 3 → USAR
  5. lo demás → ZONA GRIS

Lo que dicen los modelos es dato NO confiable: solo se lee como un JSON de tres claves y nunca se
imprime tal cual (ni la explicación, ni una clase que no sea del vocabulario).

Salida: 0 puntuado · 2 se niega.
"""
import argparse
import hashlib
import json
import os
import re
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))       # el corpus y la clave son de este árbol
sys.path.insert(0, AQUI)
import corre_piloto as CP  # noqa: E402 — rutas y formato de los registros; no importa la boca
from corre_piloto import Negado  # noqa: E402

CLASES = ("no-pareado", "multiplicidad", "se-por-sd", "tasa-base", "simpson", "grados-libertad",
          "ic-probabilidad", "redondeo-unidades")
CLAVES = frozenset({"hay_error", "clase", "explicacion"})
N_CON_ERROR, N_LIMPIOS = 20, 10

# El umbral pre-registrado (PROTOCOLO.md, apartado 7). El test compara estas cifras con el protocolo.
USAR_ESTRICTO_MIN = 12
USAR_FALSAS_MAX = 2
USAR_INCREMENTAL_MIN = 3
NO_USAR_INCREMENTAL_MAX = 1
NO_USAR_FALSAS_MIN = 4
MAX_ROTOS = 3

USAR, NO_USAR, ZONA_GRIS, INVALIDO = "USAR", "NO USAR", "ZONA GRIS", "INVÁLIDO"
# Un único bloque de código, «con o sin la palabra json» (PROTOCOLO.md, apartado 6): la palabra en
# cualquier caja, con espacios alrededor, y saltos de línea LF o CRLF. Otra palabra no casa.
RE_BLOQUE = re.compile(r"\A```[ \t]*(?:json)?[ \t]*\r?\n(.*?)\r?\n?[ \t]*```\Z", re.S | re.I)


# ── Una respuesta ──────────────────────────────────────────────────────────────────────────
def _sin_repetidas(pares):
    d = {}
    for k, v in pares:
        if k in d:
            raise ValueError("clave repetida")
        d[k] = v
    return d


def interpreta(texto):
    """El objeto de una respuesta VÁLIDA, o None si es un JSON roto (PROTOCOLO.md, apartado 6)."""
    t = texto.strip()
    m = RE_BLOQUE.match(t)
    if m:
        t = m.group(1).strip()
        if "```" in t:
            return None
    try:
        obj = json.loads(t, object_pairs_hook=_sin_repetidas)
    except ValueError:
        return None
    if not isinstance(obj, dict) or set(obj) != CLAVES:
        return None
    if not isinstance(obj["hay_error"], bool) or not isinstance(obj["explicacion"], str):
        return None
    if obj["clase"] is not None and not isinstance(obj["clase"], str):
        return None
    return obj


def juzga(texto, sembrada):
    """El juicio de una respuesta contra la clave de su problema (`sembrada`: la clase, o None si es
    limpio). Devuelve {"roto", "dice_error", "clase", "estricto", "laxo", "falsa", "incoherente"};
    `clase` es la del modelo si es del vocabulario, «otra» si no, None si no dio ninguna."""
    obj = interpreta(texto)
    roto = obj is None
    dice = bool(obj and obj["hay_error"])
    clase = None if roto or obj["clase"] is None else (obj["clase"] if obj["clase"] in CLASES else "otra")
    con_error = sembrada is not None
    return {"roto": roto, "dice_error": dice, "clase": clase,
            "estricto": con_error and dice and clase == sembrada,
            "laxo": con_error and dice,
            "falsa": (not con_error) and (dice or roto),
            "incoherente": (not roto) and (dice != (obj["clase"] is not None))}


# ── La clave y la corrida ──────────────────────────────────────────────────────────────────
def lee_clave(ruta):
    """{pNN: clase sembrada o None}. Negado si no es la clave del corpus."""
    try:
        with open(ruta, encoding="utf-8") as fh:
            crudo = json.load(fh)
    except (OSError, ValueError):
        raise Negado("clave ilegible")
    if not isinstance(crudo, dict) or sorted(crudo) != sorted(CP.IDS):
        raise Negado("la clave no tiene exactamente los problemas p01 a p%02d" % CP.N_PROBLEMAS)
    clave = {}
    for ident, ent in crudo.items():
        if not isinstance(ent, dict) or not isinstance(ent.get("tiene_error"), bool):
            raise Negado("clave de %s sin «tiene_error» booleano" % ident)
        clase = ent.get("clase")
        if ent["tiene_error"] != (clase in CLASES) or (not ent["tiene_error"] and clase is not None):
            raise Negado("clave de %s incoherente (error sin clase del vocabulario, o limpio con clase)" % ident)
        clave[ident] = clase
    con_error = sum(1 for c in clave.values() if c is not None)
    if con_error != N_CON_ERROR or len(clave) - con_error != N_LIMPIOS:
        raise Negado("la clave no trae %d con error y %d limpios: el umbral no es para ella" % (N_CON_ERROR, N_LIMPIOS))
    return clave


def _sha_fichero(ruta):
    with open(ruta, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _cuenta(juicios, clave):
    """Los totales de un modelo sobre los problemas que tiene juzgados."""
    con = [i for i in juicios if clave[i] is not None]
    sin = [i for i in juicios if clave[i] is None]
    return {"estricto": sum(juicios[i]["estricto"] for i in con),
            "laxo": sum(juicios[i]["laxo"] for i in con),
            "falsas": sum(juicios[i]["falsa"] for i in sin),
            "rotos": sum(juicios[i]["roto"] for i in juicios),
            "rotos_en": sorted(i for i in juicios if juicios[i]["roto"]),
            "incoherentes": sorted(i for i in juicios if juicios[i]["incoherente"]),
            "respondidos": len(juicios)}


def veredicto(grok, claude, incremental, cortes):
    """(veredicto, motivo) con la precedencia del protocolo."""
    if cortes:
        return NO_USAR, "negativa o corte del proveedor en %s" % ", ".join(cortes)
    for nombre, t in (("Claude", claude), ("Grok", grok)):
        if t["rotos"] > MAX_ROTOS:
            return INVALIDO, "%s tiene %d JSON rotos (más de %d): su pase no vale" % (nombre, t["rotos"], MAX_ROTOS)
    if incremental <= NO_USAR_INCREMENTAL_MAX:
        return NO_USAR, "Grok caza en estricto %d que Claude no caza (%d o menos)" % (incremental, NO_USAR_INCREMENTAL_MAX)
    if grok["falsas"] >= NO_USAR_FALSAS_MIN:
        return NO_USAR, "Grok da %d falsas alarmas (%d o más)" % (grok["falsas"], NO_USAR_FALSAS_MIN)
    if (grok["estricto"] >= USAR_ESTRICTO_MIN and grok["falsas"] <= USAR_FALSAS_MAX
            and incremental >= USAR_INCREMENTAL_MIN):
        return USAR, "estricto %d (≥ %d), falsas alarmas %d (≤ %d), incremental %d (≥ %d)" % (
            grok["estricto"], USAR_ESTRICTO_MIN, grok["falsas"], USAR_FALSAS_MAX, incremental, USAR_INCREMENTAL_MIN)
    return ZONA_GRIS, "estricto %d, falsas alarmas %d, incremental %d: ni USAR ni NO USAR" % (
        grok["estricto"], grok["falsas"], incremental)


def puntuar(corrida, ruta_clave=None):
    """Puntúa la corrida y devuelve el resultado SIN escribirlo. Negado si no se puede puntuar."""
    d = CP.dir_corrida(corrida)
    if os.path.exists(os.path.join(d, "puntuacion.json")):
        raise Negado("la corrida %s ya está puntuada: una pasada es una pasada" % corrida)
    CP.exigir_manifiesto(corrida)
    ruta_clave = ruta_clave or os.path.join(AQUI, "_clave", "clave.json")
    clave = lee_clave(ruta_clave)
    faltan = CP.faltan_de_claude(corrida)
    if faltan:
        raise Negado("faltan %d respuestas de Claude (%s…): no se puntúa" % (len(faltan), faltan[0]))
    if len(CP.modelos_de_claude(corrida)) != 1:
        raise Negado("el pase de Claude mezcla modelos (%d): tenía que ser uno para los 30; no se puntúa"
                     % len(CP.modelos_de_claude(corrida)))
    textos = {"claude": {i: CP.lee_registro(corrida, "claude", i)["texto"] for i in CP.IDS}, "grok": {}}
    cortes, sin_grok = [], []
    for ident in CP.IDS:
        r = CP.lee_registro(corrida, "grok", ident)
        if r is not None:
            textos["grok"][ident] = r["texto"]
        elif CP.corte(corrida, ident):
            cortes.append(ident)
        else:
            sin_grok.append(ident)
    if sin_grok and not cortes:
        raise Negado("faltan %d respuestas de Grok (%s…) y el pase no acabó en un corte: no se puntúa"
                     % (len(sin_grok), sin_grok[0]))
    juicios = {m: {i: juzga(t, clave[i]) for i, t in textos[m].items()} for m in CP.MODELOS}
    tot = {m: _cuenta(juicios[m], clave) for m in CP.MODELOS}
    con_error = [i for i in CP.IDS if clave[i] is not None]

    def caza(m, i, cual):
        return i in juicios[m] and juicios[m][i][cual]
    inc = {"grok_estricto": [i for i in con_error if caza("grok", i, "estricto") and not caza("claude", i, "estricto")],
           "claude_estricto": [i for i in con_error if caza("claude", i, "estricto") and not caza("grok", i, "estricto")],
           "grok_laxo": [i for i in con_error if caza("grok", i, "laxo") and not caza("claude", i, "laxo")],
           "claude_laxo": [i for i in con_error if caza("claude", i, "laxo") and not caza("grok", i, "laxo")]}
    v, motivo = veredicto(tot["grok"], tot["claude"], len(inc["grok_estricto"]), cortes)
    por_clase = {c: {"n": sum(1 for i in con_error if clave[i] == c),
                     "claude": sum(1 for i in con_error if clave[i] == c and caza("claude", i, "estricto")),
                     "grok": sum(1 for i in con_error if clave[i] == c and caza("grok", i, "estricto"))}
                 for c in CLASES}
    return {"formato": CP.FORMATO, "corrida": corrida, "veredicto": v, "motivo": motivo,
            "totales": tot, "incrementales": inc, "cortes": cortes, "sin_respuesta_de_grok": sin_grok,
            "por_clase": por_clase,
            "por_problema": {i: {"sembrada": clave[i], "claude": juicios["claude"][i],
                                 "grok": juicios["grok"].get(i)} for i in CP.IDS},
            "umbral": {"usar_estricto_min": USAR_ESTRICTO_MIN, "usar_falsas_max": USAR_FALSAS_MAX,
                       "usar_incremental_min": USAR_INCREMENTAL_MIN,
                       "no_usar_incremental_max": NO_USAR_INCREMENTAL_MAX,
                       "no_usar_falsas_min": NO_USAR_FALSAS_MIN, "max_rotos": MAX_ROTOS},
            "sha256": {"PROTOCOLO.md": _sha_fichero(os.path.join(AQUI, "PROTOCOLO.md")),
                       "puntua.py": _sha_fichero(os.path.abspath(__file__)),
                       "clave.json": _sha_fichero(ruta_clave)}}


# ── La tabla ───────────────────────────────────────────────────────────────────────────────
def _celda(j, con_error):
    if j is None:
        return "sin respuesta"
    if j["roto"]:
        return "JSON ROTO"
    if not j["dice_error"]:
        return "sin error" if con_error else "limpio, bien"
    marca = ("ESTRICTO" if j["estricto"] else "laxo") if con_error else "FALSA ALARMA"
    return "%s (%s)" % (marca, j["clase"] or "sin clase")


def informe(r):
    """Las líneas del informe. Nada de lo que escribieron los modelos sale tal cual."""
    out = ["Piloto de Grok como revisor de otra casa · corrida %s" % r["corrida"], "",
           "%-5s %-18s %-32s %-32s" % ("", "sembrada", "Claude", "Grok")]
    for ident in CP.IDS:
        p = r["por_problema"][ident]
        con = p["sembrada"] is not None
        out.append("%-5s %-18s %-32s %-32s" % (ident, p["sembrada"] or "(limpio)", _celda(p["claude"], con),
                                              _celda(p["grok"], con)))
    out += ["", "%-18s %3s %7s %5s" % ("clase", "n", "Claude", "Grok")]
    for c in CLASES:
        k = r["por_clase"][c]
        out.append("%-18s %3d %7d %5d" % (c, k["n"], k["claude"], k["grok"]))
    out += ["", "%-34s %7s %5s" % ("", "Claude", "Grok")]
    t = r["totales"]
    for etiqueta, campo in (("aciertos ESTRICTOS (de %d)" % N_CON_ERROR, "estricto"),
                            ("aciertos laxos (de %d, no decide)" % N_CON_ERROR, "laxo"),
                            ("falsas alarmas (de %d)" % N_LIMPIOS, "falsas"),
                            ("JSON rotos (de %d)" % CP.N_PROBLEMAS, "rotos"),
                            ("problemas respondidos", "respondidos")):
        out.append("%-34s %7d %5d" % (etiqueta, t["claude"][campo], t["grok"][campo]))
    i = r["incrementales"]
    out += ["",
            "Grok caza en estricto y Claude no: %d %s" % (len(i["grok_estricto"]), " ".join(i["grok_estricto"])),
            "Claude caza en estricto y Grok no: %d %s" % (len(i["claude_estricto"]), " ".join(i["claude_estricto"])),
            "En laxo (dato, no decide): Grok y no Claude %d · Claude y no Grok %d"
            % (len(i["grok_laxo"]), len(i["claude_laxo"]))]
    for m in CP.MODELOS:
        if t[m]["rotos_en"]:
            out.append("JSON rotos de %s: %s" % (m, " ".join(t[m]["rotos_en"])))
        if t[m]["incoherentes"]:
            out.append("Respuestas incoherentes de %s (hay_error y clase no casan): %s" % (m, " ".join(t[m]["incoherentes"])))
    if r["cortes"]:
        out.append("Negativa o corte del proveedor en: %s · sin enviar después: %d"
                   % (" ".join(r["cortes"]), len(r["sin_respuesta_de_grok"])))
    out += ["", "VEREDICTO: %s" % r["veredicto"], "Motivo: %s" % r["motivo"],
            "Pasar el piloto es condición necesaria y no suficiente: lo que diga Grok sigue siendo una "
            "pista sin cotejar."]
    return out


def main(argv):
    ap = argparse.ArgumentParser(prog="puntua.py")
    ap.add_argument("--corrida", required=True)
    a = ap.parse_args(argv)
    try:
        r = puntuar(a.corrida)
        ruta = os.path.join(CP.dir_corrida(a.corrida), "puntuacion.json")
        try:
            CP._escribe_nuevo(ruta, r)
        except FileExistsError:
            raise Negado("la corrida %s ya está puntuada: una pasada es una pasada" % a.corrida)
    except Negado as e:
        print("🛑 %s" % e)
        return 2
    print("\n".join(informe(r)))
    print("Escrito en %s" % ruta)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
