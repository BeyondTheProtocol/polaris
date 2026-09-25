#!/usr/bin/env python3
"""tools/deuda.py — el libro de HALLAZGOS: detectar no basta, hay que CERRAR.

POR QUÉ EXISTE (25-jul-2026, plan «que quede arreglado»). {{TITULAR}}: *«necesito que esta vez se quede
arreglado, no es la primera vez que lo intentamos»*. Al buscar por qué fallan los intentos salió el
diagnóstico de verdad, y no era la memoria:

  · La CAPTURA funciona: `cosecha_correcciones.py` encuentra 1 sola corrección suya sin codificar.
  · El CIERRE no: el bug `Task`/`Agent` del muro se detectó y re-reportó **14 veces en 8 días**
    («reconfirmación 12ª», «reconfirmación 14ª» en `Mejoras-log.md`, 19→24-jul) y no pasó nada hasta
    que se arregló por casualidad. Un hallazgo repetido 14 veces no es un hallazgo: es el sistema
    avisando de que nadie escucha.
  · Mismo patrón la misma semana: backup sin cubrir la memoria desde el 12-jul, logs del barrido de
    seguridad congelados desde el 14-jul, informe de código rojo sin la evidencia del check rojo.

Polaris DETECTA excelente y CIERRA fatal. Cada intento anterior añadió un DETECTOR nuevo (radar,
cosecha, evals de drift, healthcheck) y ninguno creó OBLIGACIÓN. Esto es la obligación, en 3 reglas:

  **R1 — «cerrado» significa que hay un TEST.** `cerrar` EXIGE la ruta de un test que exista y esté
  enganchado en `tests/test_all.sh`. Sin eso no se cierra. Es lo que convierte «arreglado» en «no
  puede volver sin que se entere alguien».
  **R2 — repetir ESCALA y escuece.** Cada `visto` incrementa el contador. Al llegar al umbral
  (3 veces; **2** si toca muro/clínico → filtro NED) el hallazgo pasa a `escalado` y
  `tests/test_deuda_escalada.py` se pone ROJO: mientras haya deuda escalada sin cerrar, NADIE puede
  decir «todo en verde», porque no lo está.
  **R3 — quien vigila también tiene latido.** Escribe heartbeat para que `healthcheck` lo vigile
  (dead-man). Si el auditor deja de correr, salta aviso.
  **R4 — lo que se va, se calla; lo que va y viene, no (29-jul-2026).** El libro sabía escalar y no
  sabía des-escalar: los detectores llaman `visto` en cada pasada mientras la condición se cumpla, y
  nadie llamaba nunca a lo contrario. Con 12 escalados de los que 5 ya no correspondían a nada
  (`enviar-hoy` 26x con la última pasada en verde), la batería llevaba días roja por alarmas
  caducadas — y una suite que siempre escuece deja de escocer, así que la alarma 13, la de verdad,
  tampoco se lee. Ahora `remitir` deja de gritar cuando la condición DESAPARECE, sin cerrar nada
  (sigue sin test, sigue en el libro). El freno para que esto no sea una puerta trasera: a la 3ª vez
  que un hallazgo se calla y vuelve pasa a `intermitente` y ya no se puede volver a callar, porque
  algo que va y viene ES un bug. R1 queda intacto: `cerrar` sigue exigiendo test.

Modelo copiado de `tools/salud.py` (claves estables, estado, acuse) + los tres campos que allí no
existen: `veces`, `test`, `impacto_ned`.

CLI:
  python3 tools/deuda.py abrir <clave> "<qué es>" [--ned alto|medio|bajo] [--muro] [--dueño X]
  python3 tools/deuda.py visto <clave> ["nota"]      # lo volvió a detectar alguien → escala
  python3 tools/deuda.py nota <clave> "contexto"     # añade contexto SIN tocar el contador
  python3 tools/deuda.py remitir <clave> "motivo"    # la condición desapareció → deja de gritar
  python3 tools/deuda.py cerrar <clave> --test tests/test_x.py ["nota"]
  python3 tools/deuda.py list [--todo]               # abierta por defecto
  python3 tools/deuda.py numero                      # la línea de una sola ojeada
  python3 tools/deuda.py escalada --json             # lo que pone rojo la suite

Determinista, stdlib, local. No habla con Telegram (eso es `salida.py` vía healthcheck).
"""
import json
import os
import re as _re
import subprocess
import sys
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("BTP_REPO") or os.path.dirname(HERE)
# El LIBRO vive en casa base, siempre. Resolverlo desde `REPO` daba un FALSO VERDE (30-jul-26):
# `tools/state/` no está versionado, así que desde un worktree no existe y `_cargar()` devolvía un
# libro vacío — `test_deuda_escalada.py` salía en verde en la rama y rojo en casa base, y cualquier
# sesión que validara en su worktree fusionaba creyendo que no había deuda escalada. Se detectó
# fusionando los KPIs de NED, con dos hallazgos escalados que la rama no veía. Mismo criterio que
# `audit_comites.py` y `kpi_ned.py`.
# `REPO` (y por tanto `TEST_ALL`) NO cambia: al cerrar un hallazgo, el test que se exige es el del
# árbol en el que estás trabajando, que es donde lo acabas de escribir.
CASA = os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode")
STATE = os.environ.get("BTP_STATE_DIR") or os.path.join(CASA, "tools", "state")
LIBRO = os.path.join(STATE, "deuda.json")
HB = os.path.join(STATE, "heartbeat", "deuda.json")
TEST_ALL = os.path.join(REPO, "tests", "test_all.sh")
# Cerrar una deuda es raro; que tarde unos segundos en comprobarse es barato al lado
# de declarar «arreglado» algo que nadie ejecutó.
TEST_TIMEOUT = int(os.environ.get("BTP_DEUDA_TEST_TIMEOUT") or 180)
# Cuántas verificaciones pueden anidarse (ver el freno en `_test_valido`). 2 deja que el test de
# este módulo cierre deudas de mentira y corta en seco un test que se cierra a sí mismo.
PROFUNDIDAD_MAX = 2


def _matar_grupo(p):
    """Mata el GRUPO de procesos del test (hijo y nietos) y recoge al hijo. Nunca lanza."""
    import signal
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except Exception:
        pass
    try:
        p.communicate(timeout=5)
    except Exception:
        pass

UMBRAL = 3          # a la 3ª repetición, la suite se pone roja
UMBRAL_MURO = 2     # lo que toca muro/clínico escala antes (filtro NED)
REMISIONES_MAX = 3  # R4: a la 3ª vez que se calla y vuelve, ya no se puede callar más

# R5 (12-sep-2026) — la DISPONIBILIDAD de un tercero no es un bug nuestro.
#
# El 12-sep la suite llevaba semanas roja con 24 escalados, y 8 eran `ia_caida_*`: Claude, Grok,
# OpenAI, Gemini, GLM, NVIDIA, Perplexity y Undermind, todas con el mismo detalle («nodename nor
# servname»), o sea UNA pasada del healthcheck sin red. Ese día `ia_health` daba HTTP 200 en las
# nueve. Peor: R4 las había marcado `intermitente` («se calló y volvió 3 veces»), que es terminal,
# así que iban a teñir la suite de rojo PARA SIEMPRE. Y una suite que siempre escuece deja de
# escocer, que es justo lo que este libro existe para evitar.
#
# El error era de categoría. R4 está pensada para un bug propio que va y viene, y ahí acierta: eso
# es un bug con disfraz. Pero una API de un tercero que cae y vuelve no va disfrazada de nada — es
# su comportamiento normal, y no lo arregla ningún commit nuestro. Lo que sí podemos garantizar, y
# es lo único que importa para NED, es que la caída NO nos pare ni nos abra: eso ya lo fija
# `tests/test_enruta.py` (un proveedor caído no se propone; si no queda ninguno de confianza se
# BLOQUEA en vez de degradar; una caída nunca abre la puerta a un tercero).
#
# Así que R1 se mantiene entera —para cerrar sigue haciendo falta un test— pero el test que
# corresponde aquí es el de la DEGRADACIÓN, no el de la disponibilidad ajena. Y una vez cerrado,
# que el proveedor vuelva a caerse se cuenta (`caidas_tras_cierre`) y no reabre.
#
# El freno contra la puerta trasera: esto NO es un flag que se pueda pasar. Es una lista cerrada de
# prefijos, escrita aquí, que solo cubre condiciones cuyo dueño está fuera de este repo.
#
# `red_sin_dns` (13-sep-2026): el Mac se quedó sin DNS del 11-sep 23:34 al 12-sep 10:34 y el
# healthcheck lo apuntó 21 veces como `daemon_bot-telegram_parado` (11.645 gaierror en
# bot-telegram.err, el bot sano). La red de casa (router, operador, wifi) no la arregla ningún
# commit, y cae y vuelve por naturaleza: sin R5 acabaría `intermitente` para siempre. Va la clave
# EXACTA y no un prefijo `red_`, para que no quepa nada más. El test de degradación que la cierra:
# `tests/test_healthcheck.py` (sin DNS no se culpa al daemon ni se gasta el kickstart).
DISPONIBILIDAD = ("ia_caida_", "red_sin_dns")


def _es_disponibilidad(clave):
    """¿La condición de este hallazgo la controla un TERCERO y no nuestro código? (R5)"""
    return str(clave or "").startswith(DISPONIBILIDAD)
NED = ("alto", "medio", "bajo")
ESTADOS = ("abierto", "escalado", "remitido", "intermitente", "cerrado")

# Lo que pone ROJA la suite. `remitido` no está: se calló porque su condición se fue.
# `intermitente` sí: se fue y volvió demasiadas veces, y eso es un bug con disfraz.
GRITAN = ("escalado", "intermitente")

# Emojis de alarma que la gente escribe DENTRO del texto de un hallazgo. El estado lo pone el
# render (🔴 escalado, 💤 remitido…), así que uno metido en la prosa solo puede mentir: sobrevive
# al triaje y viaja en cualquier lectura parcial —un grep, un digest, un resumen a Telegram— sin
# la marca que lo desmiente. Pasó el 20-sep-2026: un aviso de saldo REMITIDO del 18-sep, con
# «🔴 … el núcleo se para hasta que recargues» en presente, se leyó como una emergencia de ahora
# y estuvo a punto de acabar en «recarga la cuenta» sin motivo. Ya había pasado algo parecido el
# 13-sep (deuda estimador-saldo-contradice-senal-real).
_EMOJI_ALARMA = ("🔴", "🚨", "⛔", "‼️", "❗", "⚠️", "💳")


def texto(v):
    """El `que` de un hallazgo, listo para mostrar. Si su estado NO grita, se le quitan los
    emojis de alarma del principio: la urgencia la declara el estado, no la prosa."""
    q = (v.get("que") or "").strip()
    if v.get("estado") in GRITAN:
        return q
    while True:
        for e in _EMOJI_ALARMA:
            if q.startswith(e):
                q = q[len(e):].lstrip()
                break
        else:
            return q


def _ahora():
    return time.time()


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalizar_clave(clave):
    """La ÚNICA forma de una clave del libro: sin backticks y sin espacios de más.

    POR QUÉ (24-sep-2026): healthcheck usa el texto de la alerta como clave, y ese texto traía
    `Python` entre backticks. El encargo le pedía al agente `deuda.py abrir "<clave>"`: dentro de
    comillas dobles un backtick es sustitución de comandos, así que el agente los quitó (bien) y
    anotó otra clave. Resultado: dos entradas del mismo hallazgo y el gate de entregable
    (`prueba_entregable.py`) tumbando un job de 1,72 USD que había hecho el trabajo. Todo lo que
    lee o escribe una clave pasa por aquí, y el gate también."""
    return _re.sub(r"\s+", " ", str(clave or "").replace("`", "")).strip()


def _sello(it):
    sellos = [it.get(k) for k in ("anotado_ts", "abierto_ts", "visto_ts", "cerrado_ts", "remitido_ts")]
    sellos = [x for x in sellos if isinstance(x, (int, float))]
    return max(sellos) if sellos else 0


def _fusionar(a, b):
    """Dos entradas que resultan ser la misma clave: manda la tocada más tarde. Las veces NO se
    suman (serían dos detecciones que no ocurrieron y podrían escalar solas); se toma el máximo."""
    base, otra = (a, b) if _sello(a) >= _sello(b) else (b, a)
    out = dict(base)
    out["veces"] = max(int(a.get("veces") or 1), int(b.get("veces") or 1))
    nota_otra = otra.get("nota") or ""
    if nota_otra and nota_otra not in (out.get("nota") or ""):
        out["nota"] = ((out.get("nota") or "") + " || fusionada: " + nota_otra).strip(" |")[:4000]
    return out


def _cargar():
    try:
        with open(LIBRO, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return {}
    if not isinstance(d, dict):
        return {}
    out = {}
    for k, v in d.items():
        k2 = normalizar_clave(k)
        if not isinstance(v, dict):
            continue
        out[k2] = _fusionar(out[k2], v) if k2 in out else v
    return out


def _guardar(d):
    os.makedirs(os.path.dirname(LIBRO), exist_ok=True)
    tmp = LIBRO + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, LIBRO)
    _latido(d)


def _latido(d=None):
    """R3: dejo latido para que healthcheck sepa que el libro está vivo."""
    try:
        d = d if d is not None else _cargar()
        os.makedirs(os.path.dirname(HB), exist_ok=True)
        abiertas = [v for v in d.values() if v.get("estado") != "cerrado"]
        esc = [v for v in abiertas if v.get("estado") == "escalado"]
        with open(HB, "w", encoding="utf-8") as f:
            json.dump({"agente": "deuda", "ts": _iso(_ahora()),
                       "estado": "escalada_pendiente" if esc else "ok",
                       "abiertas": len(abiertas), "escaladas": len(esc)}, f, ensure_ascii=False)
    except Exception:
        pass


def _umbral(item):
    return UMBRAL_MURO if item.get("muro") else UMBRAL


def _test_valido(ruta):
    """R1: el test tiene que EXISTIR, estar enganchado en test_all.sh y **PASAR**. (ok, motivo).

    Lo de «y pasar» faltaba, y es la mitad que sostiene todo lo demás (13-sep-2026, hallazgo
    `deuda-cerrar-no-verifica-test-pasa`, detectado 3 veces). Hasta hoy bastaba con que el fichero
    existiera y su nombre apareciera en `test_all.sh`: se podía cerrar un hallazgo con un test en
    ROJO, o con uno que no probara nada de lo que se acababa de arreglar. Y «cerrado» es la palabra
    con la que este libro afirma que **un fallo no puede volver sin que nos enteremos**; si esa
    palabra se puede poner sin ejecutar nada, el libro entero pasa a ser decorativo.

    Fail-closed en los tres modos de no-saber: si no se puede ejecutar, si peta, o si se pasa del
    tiempo, NO se cierra. Preferimos una deuda abierta de más que un «arreglado» que nadie comprobó.
    """
    if not ruta:
        return False, "sin test: «cerrado» exige un test que falle si el fallo vuelve"
    abs_ruta = ruta if os.path.isabs(ruta) else os.path.join(REPO, ruta)
    if not os.path.exists(abs_ruta):
        return False, "el test %s no existe" % ruta
    base = os.path.basename(ruta)
    try:
        cuerpo = open(TEST_ALL, encoding="utf-8").read()
    except Exception:
        return False, "no pude leer tests/test_all.sh para comprobar el enganche"
    if base not in cuerpo:
        return False, ("%s existe pero NO está en tests/test_all.sh: un test que nadie corre no "
                       "cierra nada" % base)
    # Freno de recursión (13-sep-2026). `test_deuda_escalada.py` cerraba deudas usándose A SÍ MISMO
    # como test: verificarlo lo ejecutaba, él volvía a llamar a `cerrar()`, y así sin fondo, cuatro
    # hijos por nivel. Agotó los 2.666 procesos del mini y sobrevivió a un reinicio. Una verificación
    # puede anidar otra (el test de ESTE módulo cierra deudas de mentira), pero no una tercera.
    try:
        nivel = int(os.environ.get("BTP_DEUDA_VERIFICANDO") or 0)
    except ValueError:
        nivel = PROFUNDIDAD_MAX                   # variable rara: no se sabe, así que no se lanza
    if nivel >= PROFUNDIDAD_MAX:
        return False, ("%s pide verificar dentro de %d verificaciones anidadas: un test que se "
                       "cierra a sí mismo no demuestra nada y no se lanza" % (base, nivel))
    cmd = (["bash", abs_ruta] if base.endswith(".sh") else [sys.executable, abs_ruta])
    try:
        # Grupo de procesos propio, para que el plazo mate el GRUPO entero: `subprocess.run` solo
        # mata al hijo directo, y el 13-sep los nietos quedaron huérfanos y siguieron multiplicándose.
        p = subprocess.Popen(cmd, cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             env=dict(os.environ, BTP_DEUDA_VERIFICANDO=str(nivel + 1)),
                             start_new_session=True)
    except Exception as e:
        return False, "no pude ejecutar %s (%r): sin ejecutarlo no se cierra" % (base, e)
    try:
        salida, errores = p.communicate(timeout=TEST_TIMEOUT)
    except subprocess.TimeoutExpired:
        _matar_grupo(p)
        return False, ("%s no terminó en %ds: no se puede cerrar lo que no se ha podido comprobar"
                       % (base, TEST_TIMEOUT))
    except BaseException:
        _matar_grupo(p)
        raise
    if p.returncode != 0:
        cola = (salida.decode("utf-8", "ignore") + errores.decode("utf-8", "ignore")).strip()
        return False, ("%s EXISTE pero está en ROJO (rc=%d). Un test que no pasa no demuestra "
                       "nada:\n    %s" % (base, p.returncode, cola[-400:].replace("\n", "\n    ")))
    return True, ""


# ── API ───────────────────────────────────────────────────────────────────────────────────────
def abrir(clave, que, ned="medio", muro=False, dueno=None, nota=""):
    clave = normalizar_clave(clave)
    d = _cargar()
    if ned not in NED:
        ned = "medio"
    it = d.get(clave)
    if it and it.get("estado") != "cerrado":
        return visto(clave, nota or "re-abierto")
    d[clave] = {"que": que, "estado": "abierto", "veces": 1, "test": None,
                "impacto_ned": ned, "muro": bool(muro),
                "dueno": dueno or os.environ.get("BTP_ACTOR") or "Claude",
                "abierto_ts": _ahora(), "visto_ts": _ahora(), "nota": nota}
    _guardar(d)
    return d[clave]


_RUIDO = frozenset({"no", "de", "la", "el", "en", "que", "se", "un", "una", "por", "sin",
                    "con", "y", "a", "al", "del", "lo", "es"})


def _tokens(clave):
    """Los trozos con significado de una clave, en singular y sin separadores."""
    out = []
    for t in _re.split(r"[-_.:/ ]+", str(clave or "").lower()):
        t = t.rstrip("s") if len(t) > 4 else t        # variable/variables, ruta/rutas
        if t and t not in _RUIDO:
            out.append(t)
    return set(out)


def parecidas(clave, d=None, minimo=2, tope=5):
    """Claves ABIERTAS que comparten al menos `minimo` trozos con ésta, las más parecidas antes.

    POR QUÉ (20-sep-2026): el libro acumuló CINCO entradas abiertas del mismo agujero del guard
    clínico (`-variable-shell`, `-variables-evaden-bash`, `-ruta-en-variable`,
    `-variable-shell-no-detecta`, `-no-resuelve-variables`), cada una abierta por quien lo
    volvía a encontrar sin ver que ya estaba. Cinco entradas de 1x no escalan nunca: el mismo
    bug detectado cinco veces se lee como cinco bugs menores. La norma ya decía «si ya estaba,
    `deuda.py visto`», pero dependía de que te acordaras de buscar. Esto lo enseña solo. Avisa,
    NO bloquea: a veces dos hallazgos se parecen en el nombre y son distintos de verdad.
    """
    d = _cargar() if d is None else d
    # `familia:instancia` (`daemon_fallando:com.btp.backup`) la abre un detector, una por cosa
    # vigilada: ahí parecerse es lo normal y el aviso sería ruido en cada pasada.
    if ":" in str(clave or ""):
        return []
    clave = normalizar_clave(clave)
    mios = _tokens(clave)
    fuera = []
    for k, it in d.items():
        if k == clave or (it or {}).get("estado") == "cerrado":
            continue
        comunes = mios & _tokens(k)
        if len(comunes) >= minimo:
            fuera.append((len(comunes), k, (it or {}).get("estado", "")))
    fuera.sort(key=lambda x: (-x[0], x[1]))
    return [(k, est, n) for n, k, est in fuera[:tope]]


def visto(clave, nota=""):
    """Alguien lo volvió a detectar. Incrementa y ESCALA al llegar al umbral (R2)."""
    clave = normalizar_clave(clave)
    d = _cargar()
    it = d.get(clave)
    if not it:
        return None
    if it.get("estado") == "cerrado" and _es_disponibilidad(clave):
        # R5: el TERCERO se volvió a caer. No es una regresión NUESTRA, así que no reabre ni borra
        # el test: se cuenta y se calla. Lo que garantizamos con test es la degradación, no que un
        # proveedor esté siempre arriba. (12-sep-2026; ver R5 abajo.)
        it["veces"] = int(it.get("veces", 1)) + 1
        it["caidas_tras_cierre"] = int(it.get("caidas_tras_cierre", 0)) + 1
        it["visto_ts"] = _ahora()
        if nota:
            it["nota"] = nota
        d[clave] = it
        _guardar(d)
        return it
    if it.get("estado") == "cerrado":          # volvió después de cerrado: REGRESIÓN, se reabre
        it["estado"] = "abierto"
        it["veces"] = 1
        it["regresion"] = True
        it["test"] = None
    else:
        it["veces"] = int(it.get("veces", 1)) + 1
        if it.get("estado") == "remitido":     # R4: se había callado y ha vuelto → despierta
            it["estado"] = "abierto"
    it["visto_ts"] = _ahora()
    if nota:
        it["nota"] = nota
    # `intermitente` es terminal por arriba: ya grita, y `visto` no puede bajarlo de ahí.
    if it["veces"] >= _umbral(it) and it["estado"] not in ("cerrado", "intermitente"):
        it["estado"] = "escalado"
    d[clave] = it
    _guardar(d)
    return it


def remitir(clave, motivo=""):
    """R4: la condición DEJÓ de detectarse → el hallazgo deja de gritar, pero NO se cierra.

    Remitir y cerrar son cosas distintas y por eso tienen verbos distintos, igual que `nota` y
    `visto` (27/7/26). Cerrado = alguien demostró con un test que no puede volver. Remitido = el
    síntoma ya no se ve, que es mucho menos: el hallazgo sigue en el libro, sigue sin test y sigue
    contando en `abiertas()`. Solo deja de poner roja la suite.

    El freno contra la puerta trasera: cada remisión se cuenta. A la tercera el hallazgo pasa a
    `intermitente` y ya no se puede volver a callar nunca — un fallo que va y viene es un fallo, y
    normalmente de los peores. Devuelve (ok, motivo).
    """
    clave = normalizar_clave(clave)
    d = _cargar()
    it = d.get(clave)
    if not it:
        return False, "no existe el hallazgo %r" % clave
    estado = it.get("estado")
    if estado == "cerrado":
        return False, "ya estaba cerrado con test: remitir no aplica"
    if estado == "remitido":
        return False, "ya estaba remitido"
    if estado == "intermitente" and not _es_disponibilidad(clave):
        return False, ("%s es INTERMITENTE (%d remisiones): va y viene, así que ya no se calla. "
                       "Solo sale de ahí con un test (R1)" % (clave, it.get("remisiones", 0)))
    rem = int(it.get("remisiones", 0)) + 1
    it["remisiones"] = rem
    it["remitido_ts"] = _ahora()
    # R5: un tercero que cae y vuelve no es un bug con disfraz, es su comportamiento normal. No
    # asciende a `intermitente` (que es terminal); se calla las veces que haga falta y se cierra
    # con el test de DEGRADACIÓN, no con uno de disponibilidad ajena.
    if _es_disponibilidad(clave):
        it["estado"] = "remitido"
    else:
        it["estado"] = "intermitente" if rem >= REMISIONES_MAX else "remitido"
    if motivo:
        previa = it.get("nota") or ""
        it["nota"] = ("remitido: " + motivo + (" || " + previa if previa else ""))[:4000]
    d[clave] = it
    _guardar(d)
    if it["estado"] == "intermitente":
        return True, ("%s se ha callado y ha vuelto %d veces → 🔁 INTERMITENTE: se queda escalado "
                      "para siempre" % (clave, rem))
    return True, "%s remitido (%d/%d): su condición dejó de detectarse" % (clave, rem, REMISIONES_MAX)


def anotar(clave, nota):
    """Añade contexto a un hallazgo SIN tocar el contador (27/7/26). Existe porque la única forma
    de escribir una nota era `visto`, que significa «alguien lo ha vuelto a detectar»: usarlo para
    apuntar un matiz subió el contador, cruzó el umbral de muro (2x) y puso ROJA toda la batería por
    una segunda detección que nunca ocurrió. Anotar y re-detectar son cosas distintas y ahora tienen
    verbos distintos. No puede cerrar ni desescalar nada: eso sigue exigiendo su test (R1)."""
    clave = normalizar_clave(clave)
    d = _cargar()
    it = d.get(clave)
    if not it or not nota:
        return None
    previa = it.get("nota") or ""
    it["nota"] = (nota + (" || " + previa if previa else ""))[:4000]
    it["anotado_ts"] = _ahora()
    d[clave] = it
    _guardar(d)
    return it


def cerrar(clave, test=None, nota=""):
    """R1: solo se cierra con un test que exista y corra. Devuelve (ok, motivo)."""
    clave = normalizar_clave(clave)
    d = _cargar()
    it = d.get(clave)
    if not it:
        return False, "no existe el hallazgo %r" % clave
    ok, motivo = _test_valido(test)
    if not ok:
        return False, motivo
    it.update({"estado": "cerrado", "test": test, "cerrado_ts": _ahora(),
               "nota": nota or it.get("nota", ""),
               "cerrado_por": os.environ.get("BTP_ACTOR") or "Claude"})
    d[clave] = it
    _guardar(d)
    return True, "cerrado con %s" % test


def escaladas():
    """Lo único que pone ROJA la suite: escalado + intermitente (el que ya no puede callarse)."""
    return {k: v for k, v in _cargar().items() if v.get("estado") in GRITAN}


def abiertas():
    return {k: v for k, v in _cargar().items() if v.get("estado") != "cerrado"}


def numero():
    """La línea de una sola ojeada (para HOY y para el Observatorio)."""
    ab = abiertas()
    esc = [v for v in ab.values() if v.get("estado") in GRITAN]
    dias = 0
    if ab:
        mas_vieja = min(v.get("abierto_ts", _ahora()) for v in ab.values())
        dias = int((_ahora() - mas_vieja) / 86400)
    try:
        sys.path.insert(0, HERE)
        import normas
        e = normas.estado()
        cob = "%d/%d" % (e["con_mecanismo_aplicable"], e["memorias_feedback"])
    except Exception:
        cob = "?"
    return ("deuda abierta: %d (escalada: %d · la más vieja: %d días) · normas con mecanismo: %s"
            % (len(ab), len(esc), dias, cob))


# ── CLI ───────────────────────────────────────────────────────────────────────────────────────
def _arg(a, flag, defecto=None):
    return a[a.index(flag) + 1] if flag in a and len(a) > a.index(flag) + 1 else defecto


def _print_list(todo=False):
    d = _cargar()
    items = sorted(d.items(), key=lambda kv: (kv[1].get("estado") not in GRITAN,
                                              kv[1].get("abierto_ts", 0)))
    n = 0
    for k, v in items:
        if not todo and v.get("estado") == "cerrado":
            continue
        n += 1
        dias = int((_ahora() - v.get("abierto_ts", _ahora())) / 86400)
        marca = {"escalado": "🔴", "intermitente": "🔁", "remitido": "💤",
                 "abierto": "·", "cerrado": "✅"}.get(v.get("estado"), "?")
        extra = " · test: %s" % v["test"] if v.get("test") else ""
        if v.get("remisiones"):
            extra += " · remisiones: %d/%d" % (v["remisiones"], REMISIONES_MAX)
        muro = " ⛔muro" if v.get("muro") else ""
        print("%s %-44s %s · %dx · %dd · NED:%s%s%s" % (marca, k[:44], v.get("estado"),
                                                        v.get("veces", 1), dias,
                                                        v.get("impacto_ned", "?"), muro, extra))
        print("    %s" % texto(v)[:100])
    if not n:
        print("Sin deuda abierta.")
    print("\n" + numero())


def main(argv):
    cmd = argv[0] if argv else "list"
    a = argv[1:]
    if cmd == "abrir":
        if len(a) < 2:
            print('uso: deuda.py abrir <clave> "<qué es>" [--ned alto|medio|bajo] [--muro]')
            return 2
        it = abrir(a[0], a[1], ned=_arg(a, "--ned", "medio"), muro="--muro" in a,
                   dueno=_arg(a, "--dueño") or _arg(a, "--dueno"))
        print("abierto: %s (umbral de escalada: %dx)" % (a[0], _umbral(it)))
        cerca = parecidas(a[0])
        if cerca:
            print("⚠️  ya hay deuda abierta que se parece — ¿es el MISMO hallazgo?")
            for k, est, _n in cerca:
                print("      %-46s %s" % (k, est))
            print("    Si lo es: `deuda.py visto <esa clave>` (escala) en vez de una entrada nueva,")
            print("    y `deuda.py nota <esta> \"duplicada de <esa>\"`. Cinco entradas de 1x no escalan.")
        return 0
    if cmd == "visto":
        if not a:
            print("uso: deuda.py visto <clave> [nota]")
            return 2
        it = visto(a[0], a[1] if len(a) > 1 else "")
        if not it:
            print("no existe %r — ábrelo primero con `abrir`" % a[0])
            return 1
        print("%s: %dx%s" % (a[0], it["veces"],
                             " → 🔴 ESCALADO (la suite se pondrá roja)"
                             if it["estado"] == "escalado" else ""))
        return 0
    if cmd == "nota":
        if len(a) < 2:
            print('uso: deuda.py nota <clave> "<contexto nuevo>"   # NO toca el contador')
            return 2
        it = anotar(a[0], a[1])
        if not it:
            print("no existe %r — ábrelo primero con `abrir`" % a[0])
            return 1
        print("anotado: %s (sigue en %dx, %s)" % (a[0], it["veces"], it["estado"]))
        return 0
    if cmd == "remitir":
        if not a:
            print('uso: deuda.py remitir <clave> "<por qué ya no se detecta>"   # NO lo cierra')
            return 2
        ok, motivo = remitir(a[0], a[1] if len(a) > 1 else "")
        print(("💤 " if ok else "❌ NO se remite: ") + motivo)
        return 0 if ok else 1
    if cmd == "cerrar":
        if not a:
            print("uso: deuda.py cerrar <clave> --test tests/test_x.py [nota]")
            return 2
        ok, motivo = cerrar(a[0], _arg(a, "--test"), _arg(a, "--nota", ""))
        print(("✅ " if ok else "❌ NO se cierra: ") + motivo)
        return 0 if ok else 1
    if cmd == "escalada":
        esc = escaladas()
        if "--json" in a:
            print(json.dumps(esc, ensure_ascii=False, indent=1))
        else:
            for k, v in esc.items():
                print("🔴 %s (%dx) — %s" % (k, v.get("veces", 0), (v.get("que") or "")[:80]))
        return 1 if esc else 0
    if cmd == "numero":
        print(numero())
        return 0
    if cmd == "list":
        _print_list("--todo" in a)
        return 0
    if cmd == "latido":
        _latido()
        print("latido escrito en %s" % HB)
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
