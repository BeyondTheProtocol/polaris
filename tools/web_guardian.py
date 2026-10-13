#!/usr/bin/env python3
"""web_guardian.py — detecta que la web se ha quedado atrás o tiene algo roto. Solo detecta.

POR QUÉ EXISTE (13-oct-2026, plan «guardián de la web», aprobado por {{TITULAR}}). Ella: *«cada vez
hay más información… no quiero ser yo el cuello de botella»*. Al mirar salió que nadie vigilaba
la web: la cronología llevaba 23 días parada mientras prometía actualizarse cada semana, el enlace
«Lo último» del hero apuntaba a un ancla que no existía en producción, el sitemap enlazaba una
página que daba 404, y `/datos` era más viejo que sus analíticas. Todo eso se sabía solo cuando
alguien tropezaba y abría una deuda a mano.

QUÉ MIRA (todo determinista, sin IA, fuera de la cola)
  · cronología: días desde la última entrada, y si ES y EN van descuadradas;
  · informes del caso llegados después de la última entrada (se CUENTAN; su descripción no se
    copia aquí, ni siquiera de-identificada);
  · `/datos`: si es más viejo que sus fuentes, y si su copia de la cronología diverge del YAML;
  · en vivo, con `curl`: que el ancla «Lo último» exista y que el sitemap no enlace ningún 404;
  · las deudas de contenido web que siguen abiertas en el libro;
  · prensa: piezas en el registro de cobertura que no están en la web.

QUÉ NO MIRA, Y POR QUÉ
  · redes, correo y DMs: es texto que escribe cualquiera. Con una vía hasta una web pública que
    lleva su nombre, una frase plantada acabaría publicada;
  · la crónica interna (`cronica.py`): del 20-sep al 11-oct registró 829 commits y 32 nombres de
    fichero, y ningún hito del caso. No sirve de «qué ha pasado».

LA ESCALERA. Cada hallazgo lleva su peldaño: 1 mecánico (viaja en el lote sin leer), 2 texto
visible (se mira), 3 lo entiende y lo firma (hero, clínico nuevo, voz). Lo que nace de una fuente
externa nunca baja de 2, lo diga quien lo diga: está en `hallazgo()`, no en la buena voluntad.

MODO SOMBRA por defecto, como `acciones_datadas.py`: apunta lo que habría propuesto en
`state/web_guardian/sombra.jsonl` y no toca el buzón de Vega ni el parte. Se activa con
{"modo": "activo"} en `state/web_guardian/modo.json`. En activo mantiene UNA propuesta viva en el
buzón (se actualiza, no se suma otra) y da una línea al parte solo si hay algo que ella tenga que
mirar, y solo cuando cambia o ha pasado una semana.

Lee la web publicada por HTTP (el repo es público), no del checkout local: ese árbol es
compartido, suele estar en otra rama y esto no tiene por qué tocarlo.

Uso:
  python3 tools/web_guardian.py            # una pasada: detecta y apunta
  python3 tools/web_guardian.py --seco     # detecta e imprime, sin escribir nada
  python3 tools/web_guardian.py estado     # lo último que apuntó
"""
import datetime
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _casa  # noqa: E402

SITIO = "https://helptitular.com"
CRUDO = "https://raw.githubusercontent.com/BeyondTheProtocol/titular-{{APELLIDO}}-case/main"
DIR = os.path.join(_casa.state_dir(), "web_guardian")
BUZON = os.path.join(_casa.state_dir(), "vega", "propuestas_hilos.jsonl")
ORIGEN = "web-guardian"
DIAS_PARADA = 14          # la web promete «semanalmente»; a las dos semanas ya es mentira
TOPE_SITEMAP = 150        # URLs que se comprueban por pasada
RE_DEUDA_CONTENIDO = re.compile(r"^(?:web-|ciencia-|llms-|biopsia-|bc-ned)")
RE_NO_ES_CONTENIDO = re.compile(r"test|flaky|ras-a-three|visor3d")


# ── la escalera ──────────────────────────────────────────────────────────────────────────────

def hallazgo(clave, peldano, titulo, detalle, origen="interno"):
    """Un hallazgo. Lo externo no baja del peldaño 2: no hay forma de crearlo en el 1."""
    if origen != "interno":
        peldano = max(int(peldano), 2)
    return {"clave": clave, "peldano": int(peldano), "titulo": titulo, "detalle": detalle,
            "origen": origen}


# ── leer la cronología sin un parser de YAML (el repo no lleva dependencias) ─────────────────

def entradas(texto):
    """[{date, tag, title, highlight}] de un `timeline.yml`, en el orden del fichero."""
    out = []
    for bloque in re.split(r"\n(?=  - date:)", texto or "")[1:]:
        def campo(nombre):
            m = re.search(r"^\s+(?:- )?%s:\s*(.+?)\s*$" % nombre, bloque, re.M)
            return m.group(1).strip().strip("'\"") if m else ""
        out.append({"date": campo("date"), "tag": campo("tag"), "title": campo("title"),
                    "highlight": campo("highlight").lower() == "true"})
    return out


def _fin(fecha_texto):
    """El último día que cubre una fecha humana del fichero («8–9 abr 2026» → 9 abr), o None."""
    import caso_publico
    f = caso_publico.leer_fecha(fecha_texto)
    return datetime.date.fromisoformat(f["hasta"]) if f else None


def ultima_fecha(ent):
    fechas = [f for f in (_fin(e["date"]) for e in ent) if f]
    return max(fechas) if fechas else None


# ── las señales: cada una recibe datos ya leídos y devuelve hallazgos ────────────────────────

def senal_cronologia_parada(ent, hoy, umbral=DIAS_PARADA):
    if not ent:
        return []
    fin = _fin(ent[-1]["date"])
    if fin is None:
        return [hallazgo("cronologia-fecha-ilegible", 1, "No sé leer la fecha de la última entrada",
                         "«%s» no se puede interpretar; el «hace N días» del hero tampoco sabrá."
                         % ent[-1]["date"])]
    dias = (hoy - fin).days
    if dias < umbral:
        return []
    return [hallazgo("cronologia-parada", 3, "La cronología lleva %d días sin entrada" % dias,
                     "Última entrada: %s (%d días). La página dice que se actualiza cada semana."
                     % (ent[-1]["date"], dias))]


def senal_paridad(es, en):
    if len(es) == len(en):
        return []
    return [hallazgo("cronologia-en-atrasada", 2, "La cronología en inglés va descuadrada",
                     "ES tiene %d entradas y EN %d. El inglés se escribe, no se traduce."
                     % (len(es), len(en)))]


def senal_en_vivo(lee):
    """`lee(url)` → (código HTTP | None, cuerpo). None = no se pudo mirar."""
    codigo, cuerpo = lee(SITIO + "/timeline")
    if codigo is None:
        return [hallazgo("web-no-se-pudo-mirar", 1, "No he podido abrir la web",
                         "La pasada de hoy no pudo leer %s/timeline: lo de «en vivo» queda SIN "
                         "comprobar, que no es lo mismo que bien." % SITIO)]
    out = []
    if codigo != 200:
        out.append(hallazgo("cronologia-caida", 1, "/timeline no responde 200",
                            "Responde %s." % codigo))
    elif 'id="lo-ultimo"' not in cuerpo:
        out.append(hallazgo("ancla-lo-ultimo-rota", 1, "El enlace «Lo último» no lleva a ningún sitio",
                            "/timeline no tiene id=\"lo-ultimo\": el enlace del hero y el de la "
                            "barra aterrizan arriba, sin marca."))
    _, indice = lee(SITIO + "/sitemap_index.xml")
    urls = []
    for mapa in re.findall(r"<loc>([^<]+\.xml)</loc>", indice or ""):
        _, xml = lee(mapa)
        urls += [u for u in re.findall(r"<loc>([^<]+)</loc>", xml or "") if not u.endswith(".xml")]
    rotas = [u for u in urls[:TOPE_SITEMAP] if lee(u)[0] == 404]
    if rotas:
        out.append(hallazgo("sitemap-con-404", 1, "El sitemap enlaza %d página(s) que no existen"
                            % len(rotas),
                            ", ".join(u.replace(SITIO, "") for u in rotas[:5])))
    return out


def senal_datos_viejos(generado, fuentes):
    """`fuentes`: {nombre: ISO de su última actualización}. Las analíticas ya selladas se
    regeneran como peldaño 1 (aprobado el 24-sep-26); el perfil curado a mano es clínico nuevo."""
    viejas = sorted(n for n, ts in fuentes.items() if ts and generado and ts[:19] > generado[:19])
    if not viejas:
        return []
    peldano = 3 if any("perfil" in n for n in viejas) else 1
    return [hallazgo("datos-atrasados", peldano, "/datos es más viejo que sus fuentes",
                     "Generado el %s; desde entonces han cambiado: %s."
                     % (generado[:10], ", ".join(viejas)))]


def senal_panel_vs_cronologia(es, eventos):
    en_panel = {(e.get("fecha_texto"), (e.get("titulo") or {}).get("es")) for e in eventos or []}
    faltan = [e for e in es if (e["date"], e["title"]) not in en_panel]
    if not faltan:
        return []
    return [hallazgo("panel-sin-la-ultima-entrada", 1, "/datos no lleva toda la cronología",
                     "%d entrada(s) del YAML no están en el panel. Se arregla con "
                     "`caso_publico.py eventos`." % len(faltan))]


def senal_informes(lineas, desde):
    """`lineas` son las de `vega_vision._informes()` («· AAAA-MM-DD · carpeta · …»). Solo se
    cuentan los posteriores a `desde`."""
    n = 0
    for linea in lineas or []:
        m = re.match(r"·\s*(\d{4}-\d{2}-\d{2})\s*·", linea)
        if m and desde and datetime.date.fromisoformat(m.group(1)) > desde:
            n += 1
    if not n:
        return []
    return [hallazgo("informes-sin-reflejar", 3,
                     "Han llegado informes después de la última entrada pública",
                     "%d informe%s desde el %s. Qué se cuenta de ellos, y cómo, lo decide ella."
                     % (n, "" if n == 1 else "s", desde.isoformat()))]


def senal_deudas(libro):
    abiertas = sorted(k for k, v in (libro or {}).items()
                      if isinstance(v, dict) and v.get("estado") in ("abierto", "escalado", "intermitente")
                      and RE_DEUDA_CONTENIDO.match(k) and not RE_NO_ES_CONTENIDO.search(k))
    if not abiertas:
        return []
    return [hallazgo("deudas-de-contenido", 3, "Hay contenido de la web apuntado como desfasado",
                     "%d deudas abiertas: %s." % (len(abiertas), ", ".join(abiertas)))]


def senal_prensa(en_registro, en_web):
    if en_registro is None or en_web is None or en_registro <= en_web:
        return []
    return [hallazgo("prensa-sin-subir", 2, "Hay prensa en el registro que no está en la web",
                     "%d piezas en el registro de cobertura, %d en la sala de prensa. Cada una, "
                     "como cita literal y con su URL abierta." % (en_registro, en_web),
                     origen="externo")]


# ── estado, sombra y la única propuesta viva ─────────────────────────────────────────────────

def modo():
    try:
        with open(os.path.join(DIR, "modo.json"), encoding="utf-8") as f:
            return "activo" if json.load(f).get("modo") == "activo" else "sombra"
    except (OSError, ValueError):
        return "sombra"


def _escribe(ruta, datos):
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    tmp = ruta + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, indent=1)
    os.replace(tmp, ruta)


def guardar(hallazgos, hoy):
    m = modo()
    _escribe(os.path.join(DIR, "hallazgos.json"),
             {"fecha": hoy.isoformat(), "modo": m, "hallazgos": hallazgos})
    registro = os.path.join(DIR, "sombra.jsonl")
    ya = set()
    try:
        with open(registro, encoding="utf-8") as f:
            ya = {json.loads(x).get("clave") for x in f if x.strip()}
    except OSError:
        pass
    with open(registro, "a", encoding="utf-8") as f:
        for h in hallazgos:
            if h["clave"] not in ya:
                f.write(json.dumps(dict(h, ts=hoy.isoformat(), modo=m), ensure_ascii=False) + "\n")
    if m == "activo":
        _propuesta_viva(hallazgos, hoy)
    return m


def _propuesta_viva(hallazgos, hoy):
    """UNA línea mía sin resolver en el buzón de Vega, que se actualiza. El buzón tiene más de
    cien propuestas sin resolver: sumar una por pasada sería convertir al guardián en ruido."""
    import _lock
    para_ella = sum(1 for h in hallazgos if h["peldano"] >= 2)
    mia = {"ts": hoy.isoformat(), "origen": ORIGEN,
           "titulo": "Web: %d hallazgos del guardián (%d para mirar)" % (len(hallazgos), para_ella),
           "nota": "; ".join(h["titulo"] for h in hallazgos)[:600],
           "ruta": "tools/state/web_guardian/hallazgos.json"}
    with _lock.lock("vega-propuestas", timeout=20.0):
        try:
            with open(BUZON, encoding="utf-8") as f:
                lineas = [x for x in f.read().splitlines() if x.strip()]
        except OSError:
            lineas = []
        resto = []
        for x in lineas:
            try:
                d = json.loads(x)
            except ValueError:
                resto.append(x)
                continue
            if d.get("origen") == ORIGEN and not d.get("estado"):
                continue                               # la mía de antes: se sustituye
            resto.append(x)
        if hallazgos:
            resto.append(json.dumps(mia, ensure_ascii=False))
        os.makedirs(os.path.dirname(BUZON), exist_ok=True)
        tmp = BUZON + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("\n".join(resto) + ("\n" if resto else ""))
        os.replace(tmp, BUZON)


def resumen_hoy(hoy=None):
    """Líneas para el parte de la mañana. [] casi siempre: en sombra, sin nada que ella tenga que
    mirar, o si ya se lo dije y no ha cambiado ni ha pasado una semana. Nunca lanza."""
    try:
        if modo() != "activo":
            return []
        hoy = hoy or datetime.date.today()
        with open(os.path.join(DIR, "hallazgos.json"), encoding="utf-8") as f:
            hall = json.load(f).get("hallazgos") or []
        suyos = sorted(h["clave"] for h in hall if h.get("peldano", 1) >= 2)
        return _linea_de_cambios(hall, suyos, hoy) + _linea_del_lunes(hall, hoy)
    except Exception:
        return []


def _linea_del_lunes(hall, hoy):
    """Los lunes, si hay algo detectado, el parte le da la frase que arranca la pasada semanal.

    Sustituye a una rutina programada en la app (14-oct-26): esas viven en su ordenador, una
    sesión que corre en Polaris no puede crearlas, y el plan no debe depender de que ella monte
    nada. Con esto le basta abrir una sesión y pegar la frase."""
    if hoy.weekday() != 0 or not hall:
        return []
    return ["· 🌐 Lunes de web: %d hallazgo%s para el lote. Abre una sesión y di «haz la pasada "
            "semanal del guardián de la web»." % (len(hall), "" if len(hall) == 1 else "s")]


def _linea_de_cambios(hall, suyos, hoy):
    """Lo que ella tiene que mirar, solo cuando cambia o ha pasado una semana desde que se lo dije."""
    if not suyos:
        return []
    ruta = os.path.join(DIR, "parte_visto.json")
    try:
        with open(ruta, encoding="utf-8") as f:
            visto = json.load(f)
    except (OSError, ValueError):
        visto = {}
    misma = visto.get("claves") == suyos
    if misma and visto.get("fecha") != hoy.isoformat():
        if (hoy - datetime.date.fromisoformat(visto["fecha"])).days < 7:
            return []
    if not misma or visto.get("fecha") != hoy.isoformat():
        _escribe(ruta, {"claves": suyos, "fecha": hoy.isoformat()})
    return ["· 🌐 Web: %d cosa%s que mirar (%s). Detalle: `python3 tools/web_guardian.py estado`"
            % (len(suyos), "" if len(suyos) == 1 else "s",
               "; ".join(h["titulo"] for h in hall if h.get("peldano", 1) >= 2)[:220])]


# ── la pasada real: aquí, y solo aquí, se lee de fuera ───────────────────────────────────────

def _curl(url):
    try:
        r = subprocess.run(["curl", "-sL", "--max-time", "25", "-w", "\n%{http_code}", url],
                           capture_output=True, text=True, timeout=40)
    except (OSError, subprocess.TimeoutExpired):
        return None, ""
    if r.returncode != 0:
        return None, ""
    cuerpo, _, codigo = r.stdout.rpartition("\n")
    return (int(codigo) if codigo.isdigit() else None), cuerpo


def _iso_mtime(ruta):
    try:
        return datetime.datetime.fromtimestamp(os.path.getmtime(ruta)).isoformat(timespec="seconds")
    except OSError:
        return None


def _json(ruta):
    try:
        with open(ruta, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _prensa_en_registro():
    """Filas de la sección «En la web» del registro de cobertura, o None si no se puede leer."""
    ruta = os.path.join(_casa.casa_base(), "00_FUENTE-DE-VERDAD", "07 · Marca", "Prensa",
                        "Cobertura-log.md")
    try:
        with open(ruta, encoding="utf-8") as f:
            texto = f.read()
    except OSError:
        return None
    m = re.search(r"^#+[^\n]*En la web[^\n]*\n(.*?)(?=^#+ |\Z)", texto, re.M | re.S)
    if not m:
        return None
    filas = [x for x in m.group(1).splitlines() if x.startswith("|") and "http" in x]
    return len(filas) or None


def pasada(hoy=None, lee=_curl):
    """Todos los hallazgos de hoy. Una señal que falla no calla a las demás: se convierte en un
    hallazgo «no pude mirar X», porque un detector mudo se lee como «todo bien»."""
    hoy = hoy or datetime.date.today()
    out, es, eventos = [], [], []

    def intenta(nombre, f):
        try:
            out.extend(f() or [])
        except Exception as e:
            out.append(hallazgo("guardian-fallo-" + nombre, 1,
                                "El guardián no pudo mirar: " + nombre, repr(e)[:200]))

    c_es, t_es = lee(CRUDO + "/content/es/timeline.yml")
    c_en, t_en = lee(CRUDO + "/content/en/timeline.yml")
    if c_es != 200:
        out.append(hallazgo("guardian-fallo-cronologia", 1, "El guardián no pudo leer la cronología",
                            "GitHub respondió %s al pedir timeline.yml." % c_es))
    else:
        es = entradas(t_es)
        intenta("cronologia", lambda: senal_cronologia_parada(es, hoy))
        if c_en == 200:
            intenta("paridad", lambda: senal_paridad(es, entradas(t_en)))

    c_caso, t_caso = lee(CRUDO + "/app/data/caso.json")
    if c_caso == 200:
        def datos():
            caso = json.loads(t_caso)
            eventos[:] = caso.get("eventos") or []
            bio = _json(os.path.join(_casa.state_dir(), "biomarcadores", "timeline.json")) or {}
            fuente = os.path.join(_casa.casa_base(), "00_FUENTE-DE-VERDAD", "01 · Tratamiento",
                                  "caso", "fuente.json")
            return (senal_datos_viejos(caso.get("generado"),
                                       {"analíticas": bio.get("generado"),
                                        "perfil curado": _iso_mtime(fuente)})
                    + (senal_panel_vs_cronologia(es, eventos) if es else []))
        intenta("datos", datos)

    if es:
        def informes():
            import vega_vision
            return senal_informes(vega_vision._informes(hoy), ultima_fecha(es))
        intenta("informes", informes)

    intenta("en-vivo", lambda: senal_en_vivo(lee))
    intenta("deudas", lambda: senal_deudas(_json(os.path.join(_casa.state_dir(), "deuda.json"))))

    c_pr, t_pr = lee(CRUDO + "/content/es/press.yml")
    if c_pr == 200:
        intenta("prensa", lambda: senal_prensa(_prensa_en_registro(),
                                               len(re.findall(r"^\s+- outlet:", t_pr, re.M))))
    return out


def _pinta(hallazgos):
    if not hallazgos:
        print("la web está al día: ningún hallazgo.")
    for h in sorted(hallazgos, key=lambda x: -x["peldano"]):
        print("  [peldaño %d%s] %s\n      %s" % (h["peldano"], "" if h["origen"] == "interno"
                                                 else ", de fuera", h["titulo"], h["detalle"]))


def main(argv):
    if argv and argv[0] == "estado":
        d = _json(os.path.join(DIR, "hallazgos.json"))
        if not d:
            print("el guardián aún no ha hecho ninguna pasada.")
            return 0
        print("última pasada: %s · modo %s" % (d.get("fecha"), d.get("modo")))
        _pinta(d.get("hallazgos") or [])
        return 0
    if argv and argv[0] not in ("--seco",):
        print(__doc__)
        return 2
    hoy = datetime.date.today()
    hallazgos = pasada(hoy)
    if "--seco" in argv:
        print("(seco) no se escribe nada")
    else:
        print("modo %s · %d hallazgos apuntados" % (guardar(hallazgos, hoy), len(hallazgos)))
    _pinta(hallazgos)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
