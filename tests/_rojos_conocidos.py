#!/usr/bin/env python3
"""tests/_rojos_conocidos.py — el semáforo de fusión es «sin rojos NUEVOS», no «cero rojos».

POR QUÉ EXISTE (10-oct-26). `test_all.sh` sale con rc≠0 SIEMPRE (rojos permanentes en casa base:
`test_deuda_escalada` es rojo por diseño mientras haya deuda escalada, otros leen estado vivo del
Mac). Con una suite que nunca está en verde, «la suite pasó» deja de significar nada y nadie puede
usarla como puerta. Esto separa lo CONOCIDO (con deuda, dueño y caducidad) de lo NUEVO, sin
esconder nada: el resumen sigue nombrando cada rojo conocido.

CONDICIONES (consejero-arquitectura, 10-oct-26). Una entrada de `tests/rojos_conocidos.json` solo
cuenta como conocida si:
  · lleva deuda ABIERTA en el libro (`tools/deuda.py`), dueño, `desde` y `caduca`;
  · caduca como mucho 14 días después de `desde`, y todavía no ha caducado;
  · su `firma` (las líneas de fallo normalizadas) coincide EXACTAMENTE con la del log de hoy: si la
    batería falla por otra cosa, es un rojo NUEVO dentro de una batería conocida;
  · si la batería es del núcleo del muro (`_MURO_PREFIJOS`) lleva además `ok_titular` con fecha y su
    cita literal. Sin eso, VETADA. Un test fija que el fichero real no la incumple.
Cualquier cosa rara (fichero ilegible, entrada mal formada) hace que los rojos sigan siendo NUEVOS:
falla cerrado.

LÍMITE HONESTO: un test no puede probar que el OK de {{TITULAR}} es auténtico. Lo que hace es exigir que
exista, que se vea en el diff y que el fichero obligue a la suite COMPLETA al editarlo
(`tools/tests_afectados.py`, `_PUERTA_COMPLETA_FIJOS`).

Uso:
  python3 tests/_rojos_conocidos.py clasifica <carpeta-de-rojos> [--completa]
      imprime cada rojo (CONOCIDO / NUEVO + motivo) y una última línea
      `RESUMEN conocidos=K nuevos=M`; rc = M (acotado a 100).
  python3 tests/_rojos_conocidos.py proponer <carpeta-de-rojos>
      imprime el JSON de entradas candidatas (con su firma) para pegar DESPUÉS de abrir su deuda.
"""
import datetime
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(RAIZ, "tools"))

FICHERO = os.environ.get("BTP_ROJOS_CONOCIDOS") or os.path.join(HERE, "rojos_conocidos.json")
MAX_DIAS = 14
CAMPOS = ("bateria", "deuda", "dueno", "desde", "caduca", "firma")
_MURO_PREFIJOS_RESPALDO = ("test_muro", "test_salida_guard", "test_ok_envio", "test_permiso", "test_gate",
                           "test_fuga", "test_halt", "test_clinico", "test_casa_base", "test_singleton",
                           "test_rama_vista", "test_launch_loopback", "test_copy_web", "test_token_rotacion",
                           "test_regla_en_accion", "test_entrada_guard", "test_canario_muro",
                           "test_worktree_guard", "test_enrutado")


def prefijos_muro():
    """Los mismos que usa la puerta. Si no se pueden importar, la copia: nunca MENOS vetos."""
    try:
        import tests_afectados
        return tuple(tests_afectados._MURO_PREFIJOS)
    except Exception:
        return _MURO_PREFIJOS_RESPALDO


def es_muro(bateria):
    return str(bateria).startswith(prefijos_muro())


def _norm_linea(l):
    l = re.sub(r"/(?:private/)?(?:var/folders|tmp)/\S+", "<TMP>", l)
    l = re.sub(r"\[\d+ chars\]", "[N chars]", l)
    l = re.sub(r"\d+", "N", l)
    return re.sub(r"\s+", " ", l).strip()[:160]


def firma_de_log(texto):
    """Líneas de FALLO del log, normalizadas (números, rutas temporales) y ordenadas.
    Entran: `FAIL:`/`ERROR:` de unittest, `AssertionError…`, y los `❌ …` propios de cada batería.
    No entran las líneas de detalle sangradas (p. ej. la lista de hallazgos de deuda_escalada,
    que cambia cada día): el CASO que falla y su mensaje de cabeza sí."""
    out = set()
    for raw in str(texto or "").splitlines():
        if raw.startswith(("FAIL:", "ERROR:", "AssertionError", "❌", "  ❌", "🔴")):
            out.add(_norm_linea(raw))
    return sorted(out)


def _fecha(s):
    return datetime.date.fromisoformat(str(s))


def deudas_abiertas():
    """Claves abiertas del libro de deuda (casa base, como `tools/deuda.py`)."""
    casa = os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode")
    estado = os.environ.get("BTP_STATE_DIR") or os.path.join(casa, "tools", "state")
    try:
        with open(os.path.join(estado, "deuda.json"), encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return set()
    norm = lambda c: re.sub(r"\s+", " ", str(c or "").replace("`", "")).strip()
    return {norm(k) for k, v in d.items() if isinstance(v, dict) and v.get("estado") != "cerrado"}


def baterias_de_la_suite(raiz=RAIZ):
    try:
        t = open(os.path.join(raiz, "tests", "test_all.sh"), encoding="utf-8").read()
    except Exception:
        return set()
    return {f for f in os.listdir(os.path.join(raiz, "tests"))
            if f.startswith("test_") and f.endswith((".py", ".sh")) and f in t}


def problemas_de_entrada(e, hoy, deudas, baterias):
    """[] si la entrada es válida; si no, por qué NO cuenta."""
    if not isinstance(e, dict):
        return ["entrada mal formada"]
    p = []
    for c in CAMPOS:
        if not e.get(c):
            p.append("falta «%s»" % c)
    if p:
        return p
    if not isinstance(e["firma"], list) or not all(isinstance(x, str) for x in e["firma"]):
        return ["«firma» tiene que ser la lista de líneas de fallo"]
    if e["bateria"] not in baterias:
        p.append("la batería no existe en test_all.sh")
    try:
        desde, caduca = _fecha(e["desde"]), _fecha(e["caduca"])
    except Exception:
        return p + ["fechas no ISO (AAAA-MM-DD)"]
    if (caduca - desde).days > MAX_DIAS:
        p.append("caduca %d días después de «desde» (máximo %d)" % ((caduca - desde).days, MAX_DIAS))
    if caduca < hoy:
        p.append("CADUCADA el %s" % e["caduca"])
    norm = re.sub(r"\s+", " ", str(e["deuda"]).replace("`", "")).strip()
    if norm not in deudas:
        p.append("la deuda «%s» no está abierta en tools/deuda.py" % e["deuda"])
    if es_muro(e["bateria"]):
        ok = e.get("ok_titular")
        if not (isinstance(ok, dict) and ok.get("fecha") and len(str(ok.get("cita", "")).strip()) >= 10):
            p.append("VETADA: es del núcleo del muro y no lleva `ok_titular` {fecha, cita} con su OK explícito")
    return p


def carga_entradas(fichero=FICHERO):
    """(entradas, error). Un fichero ilegible NO es «ninguna»: se devuelve el error."""
    if not os.path.exists(fichero):
        return [], None
    try:
        with open(fichero, encoding="utf-8") as f:
            d = json.load(f)
        ent = d.get("entradas", [])
        if not isinstance(ent, list):
            return [], "«entradas» no es una lista"
        return ent, None
    except Exception as ex:   # noqa: BLE001
        return [], "fichero ilegible: %s" % ex


def clasifica(rojo_dir, fichero=FICHERO, hoy=None, deudas=None, baterias=None, completa=False):
    """{'conocidos': [...], 'nuevos': [(nombre, motivo)], 'curados': [...], 'aviso': str|None}"""
    hoy = hoy or datetime.date.today()
    deudas = deudas_abiertas() if deudas is None else deudas
    baterias = baterias_de_la_suite() if baterias is None else baterias
    entradas, error = carga_entradas(fichero)
    por_bat = {}
    for e in entradas:
        if isinstance(e, dict):
            por_bat.setdefault(e.get("bateria"), []).append(e)
    res = {"conocidos": [], "nuevos": [], "curados": [], "aviso": error}
    rojos = set()
    logs = sorted(f for f in os.listdir(rojo_dir) if f.startswith("rojo-") and f.endswith(".log")) if os.path.isdir(rojo_dir) else []
    for f in logs:
        nombre = f[len("rojo-"):-len(".log")]
        rojos.add(nombre)
        if error:
            res["nuevos"].append((nombre, "rojos_conocidos.json ilegible: todo es nuevo"))
            continue
        cands = por_bat.get(nombre)
        if not cands:
            res["nuevos"].append((nombre, "no está en la lista de conocidos"))
            continue
        try:
            firma = firma_de_log(open(os.path.join(rojo_dir, f), encoding="utf-8", errors="replace").read())
        except Exception as ex:   # noqa: BLE001
            res["nuevos"].append((nombre, "log ilegible: %s" % ex))
            continue
        motivo = None
        for e in cands:
            pr = problemas_de_entrada(e, hoy, deudas, baterias)
            if pr:
                motivo = "entrada no válida: " + "; ".join(pr)
                continue
            if sorted(_norm_linea(x) for x in e["firma"]) == firma:
                res["conocidos"].append((nombre, e["deuda"]))
                motivo = None
                break
            extra = sorted(set(firma) - {_norm_linea(x) for x in e["firma"]})
            motivo = "falla por OTRA cosa que la registrada (nuevo: %s)" % (extra[:2] or "falta alguna línea registrada")
        if motivo:
            res["nuevos"].append((nombre, motivo))
    if completa and not error:
        for nombre, es in sorted(por_bat.items()):
            if nombre not in rojos and not any(isinstance(e, dict) and e.get("intermitente") for e in es):
                res["curados"].append(nombre)
    return res


def main(argv):
    if len(argv) < 2 or argv[0] not in ("clasifica", "proponer"):
        print(__doc__)
        return 2
    rojo_dir = argv[1]
    if argv[0] == "proponer":
        hoy = datetime.date.today()
        out = []
        for f in sorted(os.listdir(rojo_dir)):
            if f.startswith("rojo-") and f.endswith(".log"):
                n = f[5:-4]
                out.append({"bateria": n, "deuda": "<clave de deuda.py abierta>", "dueno": "<quién>",
                            "desde": hoy.isoformat(), "caduca": (hoy + datetime.timedelta(days=MAX_DIAS)).isoformat(),
                            "firma": firma_de_log(open(os.path.join(rojo_dir, f), encoding="utf-8", errors="replace").read()),
                            **({"ok_titular": {"fecha": "", "cita": "<su OK literal>"}} if es_muro(n) else {})})
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0
    r = clasifica(rojo_dir, completa="--completa" in argv)
    if r["aviso"]:
        print("⚠️  %s" % r["aviso"])
    for n, d in r["conocidos"]:
        print("   🟠 CONOCIDO: %s (deuda: %s)" % (n, d))
    for n, m in r["nuevos"]:
        print("   🔴 NUEVO: %s — %s" % (n, m))
    for n in r["curados"]:
        print("   🩹 ¿CURADO?: %s está en la lista y no falló (puede haber saltado): revisa si hay que quitarla" % n)
    print("RESUMEN conocidos=%d nuevos=%d" % (len(r["conocidos"]), len(r["nuevos"])))
    return min(len(r["nuevos"]), 100)


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as e:   # noqa: BLE001  FALLA CERRADA: sin RESUMEN, el llamador deja todo como rojo nuevo
        print("⚠️  clasificador roto: %s" % e)
        sys.exit(101)
