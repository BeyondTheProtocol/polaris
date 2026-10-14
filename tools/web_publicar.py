#!/usr/bin/env python3
"""web_publicar.py — el único punto por el que algo se FUSIONA a la web pública.

POR QUÉ EXISTE (13-oct-2026, plan «guardián de la web», aprobado por {{TITULAR}}). Hasta hoy había dos
herramientas que publicaban en helptitular.com, y las dos lo hacían por su cuenta:

  · `web_novedad.py` fusionaba tres segundos después de crear el PR (#199: creado 14:10:41Z,
    fusionado 14:10:44Z, y sus checks acabaron a las 14:11 y 14:12). El CI y la preview de
    Netlify no llegaban a opinar.
  · `web_revertir.py` fusionaba sin comprobar NINGÚN permiso: cualquier agente podía cambiar
    producción con un comando que está en la allowlist.

Y las dos ejecutan `git` y `gh` dentro de un subproceso de Python, que es justo donde no miran
`copy_web_guard` (solo ve `git commit` en Bash) ni `salida_guard` (solo ve `gh pr merge` en
Bash). Un guardián de la web montado encima habría heredado los dos agujeros.

QUÉ HACE `fusionar()`, SIEMPRE EN ESTE ORDEN
  1. lee el PR (número, cabeza, estado);
  2. comprueba que hay permiso (sin gastarlo): sin él no se tiene a nadie mirando checks;
  3. mira el copy: si el PR cambia el hero/H1 o borra una clave, solo sigue si ELLA nombró ese
     PR en su mensaje. Aquí NO vale `BTP_COPY_OK=1`: es una variable de entorno sin firma;
  4. exige el CI en verde (espera un rato acotado; «sin checks» no es verde);
  5. vuelve a comprobar el permiso firmado de ESTA sesión (no «el más reciente de cualquiera»,
     que era la deuda `web_novedad_permiso_sin_sesion`) y lo gasta;
  6. fusiona con `--match-head-commit`: si la cabeza cambió desde que se leyó, GitHub lo rechaza;
  7. comprueba el despliegue en vivo si se le dice qué buscar. Mergeado no es desplegado;
  8. lo apunta en `tools/state/web_publicar.jsonl`.

El fallo por defecto es «queda en PR», nunca «publicado».

LO QUE NO HACE. No redacta, no decide qué se publica y no abre PRs: eso es de quien llama.

Uso (lo normal es importarlo; la CLI es para mirar):
  python3 tools/web_publicar.py estado <PR|rama>            # CI, cabeza y qué diría del copy
  python3 tools/web_publicar.py verificar <url> "<texto>"   # ¿está ya en producción?
"""
import datetime
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _casa  # noqa: E402
import permiso_envio as P  # noqa: E402

WEB = os.environ.get("BTP_WEB_REPO", "/Users/polaris/projects/titular-{{APELLIDO}}-case")
BASE = "main"
REPO = os.environ.get("BTP_WEB_SLUG", "BeyondTheProtocol/titular-{{APELLIDO}}-case")
LOG =os.path.join(_casa.state_dir(), "web_publicar.jsonl")
LOCALES = "i18n/locales"
# El CI de la web tarda 2–3,5 min (medido el 13-oct-26 en las 6 últimas ejecuciones de main) y su
# job más largo tiene un tope de 20. El permiso de {{TITULAR}} dura 10: esperar más que esto es
# quedarse sin permiso a mitad, así que pasado el tope se deja en PR y se dice.
TOPE_CI_S = int(os.environ.get("BTP_WEB_TOPE_CI", "360"))
PASO_S = 15


# ── piezas pequeñas, cada una con su prueba ─────────────────────────────────────────────────

def sesion_actual():
    """La sesión de Claude Code que lanza esta herramienta. Coincide con el `session_id` que el
    hook de su mensaje firma en el permiso (comprobado el 13-oct-26 contra el transcript)."""
    return os.environ.get("CLAUDE_CODE_SESSION_ID") or None


def _run(cmd, cwd=None, timeout=180):
    return subprocess.run(cmd, cwd=cwd or WEB, capture_output=True, text=True, timeout=timeout)


def _registrar(**extra):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(dict(
                ts=datetime.datetime.now().replace(microsecond=0).isoformat(), **extra),
                ensure_ascii=False) + "\n")
    except OSError as e:   # el log no puede tumbar una publicación, pero tampoco callarse
        sys.stderr.write("web_publicar: no pude apuntar en el log (%r)\n" % (e,))


def pr_info(ref, cwd=None):
    """dict del PR o None. `ref` = número, rama o URL."""
    r = _run(["gh", "pr", "view", str(ref), "--json",
              "number,headRefOid,state,url,title,statusCheckRollup"], cwd=cwd)
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout)
    except ValueError:
        return None


def ci_estado(rollup):
    """«verde» | «pendiente» | «rojo» | «sin-checks», a partir de `statusCheckRollup`.

    Hay dos formas mezcladas en la misma lista: CheckRun (`status` + `conclusion`) y
    StatusContext (`state`). La preview de Netlify llega como una de ellas, así que esperar a
    «verde» incluye que la preview se haya construido."""
    if not rollup:
        return "sin-checks"
    pendiente = False
    for c in rollup:
        if "state" in c and "status" not in c:            # StatusContext
            s = (c.get("state") or "").upper()
            if s in ("FAILURE", "ERROR"):
                return "rojo"
            if s != "SUCCESS":
                pendiente = True
            continue
        if (c.get("status") or "").upper() != "COMPLETED":
            pendiente = True
            continue
        if (c.get("conclusion") or "").upper() not in ("SUCCESS", "NEUTRAL", "SKIPPED"):
            return "rojo"
    return "pendiente" if pendiente else "verde"


def esperar_ci(ref, cwd=None, tope_s=None, paso_s=PASO_S, _info=pr_info, _dormir=time.sleep):
    """(estado, info). Espera hasta `tope_s` a que deje de estar pendiente. «sin-checks» también
    se espera: recién creado el PR la lista sale vacía unos segundos."""
    tope_s = TOPE_CI_S if tope_s is None else tope_s
    limite = time.time() + tope_s
    while True:
        info = _info(ref, cwd)
        estado = ci_estado((info or {}).get("statusCheckRollup")) if info else "sin-checks"
        if estado in ("verde", "rojo") or time.time() >= limite:
            return estado, info
        _dormir(paso_s)


def _es_hero(clave):
    c = clave.lower()
    return c.startswith("hero.") or "hero_" in c or c.endswith(".h1") or ".h1." in c


def _aplana(o, pre=""):
    out = {}
    if isinstance(o, dict):
        for k, v in o.items():
            out.update(_aplana(v, "%s.%s" % (pre, k) if pre else str(k)))
    elif isinstance(o, list):
        for i, v in enumerate(o):
            out.update(_aplana(v, "%s[%d]" % (pre, i)))
    elif isinstance(o, str):
        out[pre] = o
    return out


def trae(repo, cabeza):
    """Baja `main` y la cabeza del PR al repo local, para poder leer su diff sin hacer checkout."""
    subprocess.run(["git", "fetch", "-q", "origin", BASE, cabeza], cwd=repo,
                   capture_output=True, timeout=120)


def revisa_copy(repo, base, cabeza):
    """(frenan, avisan): qué copy PUBLICADO toca el rango `base...cabeza`.

    Mismas reglas que `.claude/hooks/copy_web_guard.py`, aplicadas al diff del PR y no al índice
    de un commit: frena el valor cambiado de una clave de hero/H1 y cualquier clave borrada;
    avisa del resto de copy existente que cambia. La regla del hero se repite aquí (3 líneas) y
    no se importa del hook porque aquel arrastra el tokenizador de Bash; `test_web_publicar`
    comprueba que las dos clasifican igual.

    Fail-CLOSED, al revés que el hook: si un locale no se puede leer o parsear, frena. El hook
    corre en cada commit y un falso freno ahí estorba; esto corre al publicar."""
    r = _run(["git", "diff", "--name-only", "%s...%s" % (base, cabeza)], cwd=repo)
    if r.returncode != 0:
        return ["no pude leer el diff (%s)" % (r.stderr or "").strip()[:120]], []
    comun = _run(["git", "merge-base", base, cabeza], cwd=repo).stdout.strip() or base
    frenan, avisan = [], []
    for f in [x.strip() for x in r.stdout.splitlines() if x.strip()]:
        if f.startswith(LOCALES + "/") and f.endswith(".json"):
            viejo = _run(["git", "show", "%s:%s" % (comun, f)], cwd=repo)
            if viejo.returncode != 0:
                continue                                   # locale nuevo: todo es nuevo
            nuevo = _run(["git", "show", "%s:%s" % (cabeza, f)], cwd=repo)
            if nuevo.returncode != 0:
                frenan.append("%s: fichero de copy borrado" % f)
                continue
            try:
                a, b = _aplana(json.loads(viejo.stdout)), _aplana(json.loads(nuevo.stdout))
            except ValueError:
                frenan.append("%s: no se puede leer como JSON, así que no sé qué copy cambia" % f)
                continue
            for k in a:
                if k not in b:
                    frenan.append("%s: clave borrada «%s»" % (f, k))
                elif a[k] != b[k]:
                    (frenan if _es_hero(k) else avisan).append(
                        "%s: %s «%s»" % (f, "hero/H1" if _es_hero(k) else "copy existente", k))
        elif f.startswith("content/"):
            d = _run(["git", "diff", "--unified=0", "%s...%s" % (base, cabeza), "--", f], cwd=repo)
            quita = [x for x in d.stdout.splitlines()
                     if x.startswith("-") and not x.startswith("---")]
            if quita:
                avisan.append("%s: texto modificado o borrado (%d líneas)" % (f, len(quita)))
    return frenan, avisan


def permiso(que):
    """(d, motivo, ctx, k). `d` es None si no hay permiso que valga para `que` en ESTA sesión.
    La clave nunca sale del entorno: esta herramienta la lanza el agente."""
    if not P.hay_alguno():
        return None, "nadie lo ha pedido en un mensaje", {}, None
    k, sin_llave = P.llave(permitir_env=False)     # en modo Ed25519 no hay secreto que leer
    if not k:
        return None, "el permiso no se puede comprobar: " + sin_llave, {}, None
    ses = sesion_actual()
    if not ses:
        return None, ("no sé desde qué sesión se me llama (falta CLAUDE_CODE_SESSION_ID): "
                      "un permiso sin sesión es un permiso prestado"), {}, k
    d, motivo, ctx = P.validar(k, ses)
    if not d:
        return None, motivo, {}, k
    if not P.permite(ctx, que):
        return None, "su mensaje no pedía esto (%s)" % que, ctx, k
    return d, "", ctx, k


def verificar_despliegue(url, aguja, tope_s=0, paso_s=20, _dormir=time.sleep):
    """True si `aguja` está en `url`; False si no tras esperar `tope_s`; None si no se pudo mirar.
    Mergeado no es desplegado: Netlify tarda, y a veces no despliega."""
    limite = time.time() + tope_s
    visto = None
    while True:
        r = subprocess.run(["curl", "-sL", "--max-time", "25", url],
                           capture_output=True, text=True)
        if r.returncode == 0 and r.stdout:
            visto = aguja in r.stdout
            if visto:
                return True
        if time.time() >= limite:
            return visto
        _dormir(paso_s)


def aguja_segura(texto):
    """El trozo más largo de `texto` que sale igual en el HTML (sin & < > " ' que se escapan)."""
    trozos, actual = [], ""
    for ch in texto or "":
        if ch in "&<>\"'":
            trozos.append(actual)
            actual = ""
        else:
            actual += ch
    trozos.append(actual)
    return max((t.strip() for t in trozos), key=len, default="")


def numeros_sin_almohadilla(ctx):
    """"" o el motivo para NO fusionar: su mensaje trae un número suelto que coincide con un PR
    que tenía delante («fusiona 268 y 270»).

    `permiso_envio` solo reconoce un PR escrito como «#268», «PR 268», «el 268» o su URL. Con
    números sueltos no reconoce ninguno, `prs_suyos` queda vacío y la orden se leería como
    «fusiona todo lo que vi»: saldría justo el que ella quiso dejar fuera. Ante la duda no sale
    nada y se le pide que los nombre con almohadilla."""
    import re
    texto = (ctx or {}).get("texto") or ""
    sueltos = {int(n) for n in re.findall(r"(?<![#\w/.])(\d{1,6})(?![\w.])", texto)}
    dudosos = (sueltos - (ctx.get("prs_suyos") or set())) & (ctx.get("prs_vistos") or set())
    if not dudosos:
        return ""
    return ("su mensaje nombra %s sin «#» y no sé si quiso decir solo esos: que los escriba como "
            "%s" % (", ".join(str(n) for n in sorted(dudosos)),
                    " y ".join("#%d" % n for n in sorted(dudosos))))


def gasta_lo_que_quede(por, _permiso=None):
    """Da por usado el permiso de esta sesión si sigue vivo. True si había algo que gastar."""
    d, _motivo, _ctx, _k = (_permiso or permiso)("fusionar")
    if not d:
        return False
    P.marcar_usado(d, por)
    return True


def _merge_real(cmd, cwd=None):
    """Con un worktree temporal propio (`cwd`), se fusiona desde él. Sin él, con `-R` y desde
    fuera de cualquier repo: así `gh` no cambia de rama ni borra ramas locales en el checkout
    compartido de la web, que suele tener trabajo de otra sesión."""
    if cwd:
        return _run(cmd, cwd=cwd)
    import tempfile
    return _run(cmd[:3] + ["-R", REPO] + cmd[3:], cwd=tempfile.gettempdir())


# ── la operación ─────────────────────────────────────────────────────────────────────────────

def fusionar(ref, *, por, que="envio", cwd=None, usos_max=1, atar_a_lo_visto=False,
             exige_nombrado=False, verifica=None, tope_ci_s=None, tope_despliegue_s=0,
             _info=pr_info, _ci=esperar_ci, _copy=revisa_copy, _permiso=permiso,
             _merge=None, _verifica=verificar_despliegue, _fetch=trae):
    """Fusiona el PR `ref` si pasa TODO lo de arriba. Devuelve un dict; no lanza.

    `que`: «envio» (novedad, revertir) o «fusionar» (un PR que ella vio).
    `atar_a_lo_visto`: exige que el PR y su cabeza sean los que ella tenía delante
        (`permiso_envio.comprobar_fusion`). Para una novedad no aplica: cuando lo pidió el PR
        aún no existía, y lo que la ata es su propio texto.
    `exige_nombrado`: el PR solo sale si ELLA lo nombra por su número (peldaño 3 del lote:
        hero, clínico nuevo, navegación). Un «fusiona» genérico no lo arrastra.
    `usos_max`: cuántas publicaciones abre un mismo mensaje suyo (la novedad admite 3).
    `verifica`: (url, texto) para comprobar el despliegue en vivo."""
    res = {"ref": str(ref), "por": por, "merged": False}

    def cierra(motivo=None):
        if motivo:
            res["motivo_no_merge"] = motivo
        _registrar(**res)
        return res

    info = _info(ref, cwd)
    if not info:
        return cierra("no encuentro el PR %s" % ref)
    numero, cabeza = info.get("number"), info.get("headRefOid") or ""
    res.update(numero=numero, cabeza=cabeza[:12], url=info.get("url"))
    if (info.get("state") or "").upper() != "OPEN":
        return cierra("el PR #%s no está abierto (%s)" % (numero, info.get("state")))
    if len(cabeza) != 40:
        return cierra("no sé cuál es la cabeza del PR #%s" % numero)

    d, motivo, ctx, k = _permiso(que)          # aquí NO se gasta
    res["pedido_por_titular"] = bool(d)
    if not d:
        return cierra(motivo)

    if exige_nombrado and numero not in (ctx.get("prs_suyos") or set()):
        return cierra("el PR #%s es de los que solo salen si ella lo nombra por su número" % numero)

    repo = cwd or WEB
    _fetch(repo, cabeza)
    frenan, avisan = _copy(repo, "origin/" + BASE, cabeza)
    res["copy_avisa"] = avisan[:10]
    if frenan:
        res["copy_frena"] = frenan[:10]
        if numero not in (ctx.get("prs_suyos") or set()):
            return cierra("toca copy que solo se publica si ella nombra este PR (#%s) en su "
                          "mensaje: %s" % (numero, "; ".join(frenan[:3])))

    estado, info2 = _ci(ref, cwd, tope_ci_s)
    res["ci"] = estado
    if estado != "verde":
        return cierra({"rojo": "el CI está en rojo",
                       "pendiente": "el CI no ha terminado en el tiempo que dura su permiso",
                       "sin-checks": "el PR no tiene checks: sin CI no se publica"}[estado])
    if (info2 or {}).get("headRefOid") != cabeza:
        return cierra("la cabeza del PR cambió mientras esperaba al CI")

    d, motivo, ctx, k = _permiso(que)          # puede haber caducado durante la espera
    if not d:
        res["pedido_por_titular"] = False
        return cierra("el permiso ya no vale tras esperar al CI (%s)" % motivo)
    if atar_a_lo_visto:
        pega = numeros_sin_almohadilla(ctx) or P.comprobar_fusion(ctx, numero, cabeza)
        if pega:
            return cierra(pega)
    # El uso se reserva ANTES de fusionar y con cerrojo: de dos fusiones a la vez, una sale.
    try:
        mio = bool(P.gastar_uno(d, k, usos_max)) if usos_max > 1 else P.consumir(d, por)
    except Exception:
        mio = False
    if not mio:
        return cierra("ese mensaje suyo ya abrió todo lo que podía abrir")

    m = (_merge or _merge_real)(["gh", "pr", "merge", str(numero), "--squash", "--delete-branch",
                                 "--match-head-commit", cabeza], cwd=cwd)
    res["merged"] = m.returncode == 0
    if m.returncode != 0:
        res["merge_error"] = (m.stderr or m.stdout or "").strip()[:300]
        return cierra("el merge no salió")

    if verifica:
        url, texto = verifica
        res["verifica"] = {"url": url, "texto": aguja_segura(texto)}
        res["desplegado"] = _verifica(url, aguja_segura(texto), tope_despliegue_s)
    return cierra()


def explica_despliegue(res):
    """Una línea honesta sobre si lo fusionado está ya en producción."""
    v = res.get("verifica")
    if not v:
        return "fusionado; el despliegue no se ha comprobado."
    if res.get("desplegado") is True:
        return "fusionado y comprobado en vivo: %s" % v["url"]
    return ("fusionado, pero AÚN no lo veo en producción. Compruébalo con: "
            "python3 tools/web_publicar.py verificar %s \"%s\"" % (v["url"], v["texto"]))


def main(argv):
    if len(argv) >= 2 and argv[0] == "estado":
        info = pr_info(argv[1])
        if not info:
            print("no encuentro el PR %s" % argv[1], file=sys.stderr)
            return 2
        cabeza = info.get("headRefOid") or ""
        trae(WEB, cabeza)
        frenan, avisan = revisa_copy(WEB, "origin/" + BASE, cabeza)
        print("#%s %s\n  cabeza: %s\n  estado: %s · CI: %s" % (
            info.get("number"), info.get("title"), cabeza,
            info.get("state"), ci_estado(info.get("statusCheckRollup"))))
        for x in frenan:
            print("  🛑 %s" % x)
        for x in avisan:
            print("  ⚠️  %s" % x)
        return 0
    if len(argv) >= 3 and argv[0] == "verificar":
        tope = int(argv[3]) if len(argv) > 3 else 0
        visto = verificar_despliegue(argv[1], argv[2], tope)
        print({True: "✅ está en producción", False: "🟡 todavía no está en producción",
               None: "⛔ no he podido mirar la página"}[visto])
        return 0 if visto else 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
