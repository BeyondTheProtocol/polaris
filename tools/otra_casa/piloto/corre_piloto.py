#!/usr/bin/env python3
"""tools/otra_casa/piloto/corre_piloto.py — el lanzador del piloto de aceptación de Grok como revisor
de otra casa (fase 1 de la vía de Grok). El protocolo y el umbral están en PROTOCOLO.md, al lado,
escritos y fechados el 10-oct-2026 antes de correr nada. Aquí no se decide nada: se comprueba, se
envía por `tools/otra_casa.py` y se guarda.

CÓMO SE LANZA `grok` (PROTOCOLO.md, apartado 5): desde CASA BASE y con la rama fusionada (desde un
worktree la boca cierra); EN SEGUNDO PLANO o en la terminal de {{TITULAR}}, nunca en un Bash de agente en
primer plano (cada envío espera hasta 900 s y son 31: el Bash corta a los 600 s y deja un envío sin
desenlace, que solo {{TITULAR}} reanuda); y DE DÍA, fuera del silencio nocturno (el primer envío espera a
que a {{TITULAR}} le haya llegado su aviso; de noche la boca cierra).

Órdenes:
  previa                         sin red, sin clave, sin sellar: guardas de destino, la sonda y los 30
                                 problemas contra las guardas de contenido, y la cota de coste.
  entrada --problema pNN|sonda   imprime la entrada EXACTA de ese texto (instrucción + separador +
                                 texto): lo mismo que recibe Grok es lo que tiene que recibir Claude.
  apunta-claude --corrida ID --problema pNN --modelo <id> respuesta.txt
                                 guarda la respuesta de Claude a un problema. El pase de Claude va
                                 ANTES que los 30 de Grok: se niega si la corrida ya tiene algo de
                                 Grok sobre el corpus (la sonda no cuenta: va la primera).
  grok --corrida ID --enviar-de-verdad --solo-sonda
                                 envía la sonda desechable (paso 1). Se mira su uso antes de seguir.
  grok --corrida ID --enviar-de-verdad
                                 envía los 30 problemas uno a uno (paso 3), con la misma tanda (= la
                                 corrida) y avisar=True. Se niega sin la sonda ya guardada.

LO QUE `grok` HACE Y LO QUE NO.
  · Exige la bandera `--enviar-de-verdad`. En la batería de tests se niega salvo con un transporte
    inyectado (y la boca, por su lado, niega ahí el transporte real y la clave real).
  · No envía ningún problema si en la corrida no están ya la sonda guardada y las 30 respuestas de
    Claude (la sonda sola, `--solo-sonda`, sí puede ir antes: es el paso 1), ni nada si el protocolo,
    la instrucción o algún problema cambiaron desde que se abrió la corrida (manifiesto).
  · Antes de la primera llamada pasa en local las guardas de destino y las de contenido de TODO lo
    pendiente: si algo cierra, no sale nada.
  · Para en la PRIMERA excepción y lo dice. No reanuda (reanudar lo teclea {{TITULAR}} en su terminal:
    `python3 tools/otra_casa.py reanudar --destino razona:grok --motivo "…"`) y no reintenta.
  · Si la respuesta llegó y contradice una condición auditada (`store` distinto de false, o retención
    cero anotada y la cabecera sin «true»), NO guarda su registro: queda la parada, con la ruta de la
    pista, y dice que reanudar no lo arregla (antes se corrige la configuración o se vuelve a
    auditar). Ese texto se enviará otra vez.
  · No vuelve a enviar lo que ya tiene respuesta guardada en esa corrida, la sonda incluida.
  · Nunca lee `_clave/`. La respuesta de Grok es dato NO confiable: se guarda y no se imprime, no se
    ejecuta ni se interpreta.

DÓNDE QUEDA. En el estado de casa base (`_casa.state_dir()`), `otra_casa/piloto/<corrida>/`:
  manifiesto.json · claude/pNN.json · grok/sonda.json · grok/pNN.json · grok/<id>.parada-*.json
(tipo `parada`, `condicion` o `puerta`). Fuera de la corrida: las pistas de la boca en
`otra_casa/respuestas/`, sus sellos en la cadena del borde y la línea de gasto de cada petición.
Cada registro: corrida, problema, modelo, texto (la respuesta tal cual), sha256_entrada (la entrada
exacta), sha256_peticion y ruta_pista (solo Grok), modelo_devuelto, tanda, uso, cuando.

LÍMITES DECLARADOS.
  · Si la respuesta llega y no se puede guardar el registro, se para; al relanzar, ese problema se
    envía otra vez (se paga dos veces). La pista sí queda en el directorio de respuestas de la boca.
  · Un tiempo agotado después de que xAI procese la petición se cobra y no deja respuesta.
  · La cota de coste supone que el razonamiento se cobra como salida y cabe en el tope de salida por
    defecto; la página de modelos de xAI no dice ni lo uno ni lo otro. El coste real no está medido.
  · Que el pase de Claude sea de verdad a ciegas no lo puede comprobar el código: impone el orden
    (Claude antes que los 30 de Grok), un solo modelo de Claude y que este fichero no abra `_clave/`.
  · Que alguien MIRE el uso de la sonda antes de lanzar los 30 tampoco: impone que la sonda vaya en
    una orden aparte. Y la sonda puede ir después del pase de Claude (si obliga a tocar la
    instrucción, ese pase se pierde; el resultado no se contamina: el manifiesto fija la instrucción).
  · Un envío sin desenlace (el proceso murió a media llamada) no deja registro aquí: lo ve la boca,
    que cierra hasta que {{TITULAR}} reanude. Tras reanudar, ese texto se envía otra vez.

Salida: 0 bien · 2 el lanzador se niega · 3 una guarda de la boca cerró · 4 parada del proveedor.
"""
import argparse
import glob
import hashlib
import json
import math
import os
import re
import sys
import time

# El corpus, la instrucción y el protocolo son CÓDIGO de este árbol (se versionan con él): salen de
# la carpeta de este fichero, a propósito. El ESTADO (las respuestas) va a casa base, por _casa.
AQUI = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(os.path.dirname(AQUI))
sys.path.insert(0, TOOLS)
import _casa  # noqa: E402

DESTINO = "razona:grok"
SEPARADOR = "\n\n---\n\n"                   # el de otra_casa.py; `grok` comprueba que coinciden
N_PROBLEMAS = 30
IDS = tuple("p%02d" % i for i in range(1, N_PROBLEMAS + 1))
SONDA = "sonda"
MODELOS = ("claude", "grok")
RE_CORRIDA = re.compile(r"^[A-Za-z0-9._-]{1,40}$")     # la corrida es también la tanda
BANDERA = "--enviar-de-verdad"
FORMATO = 1
MAX_BYTES_RESPUESTA_CLAUDE = 1024 * 1024
FIJADOS = ("PROTOCOLO.md", "instruccion.txt")          # más los 30 problemas: el manifiesto

# Cota de coste (PROTOCOLO.md, apartado 8). Precios de docs.x.ai/developers/models, 10-oct-2026.
USD_POR_MILLON_ENTRADA = 2.0
USD_POR_MILLON_SALIDA = 6.0
MAX_TOKENS_SALIDA = 128000


class Negado(RuntimeError):
    """El lanzador se niega: no es una guarda de la boca ni una parada del proveedor."""


# ── El corpus ──────────────────────────────────────────────────────────────────────────────
def ruta_texto(ident):
    """El fichero de un problema (pNN) o de la sonda."""
    if ident == SONDA:
        return os.path.join(AQUI, "sonda.txt")
    if ident not in IDS:
        raise Negado("problema %r: vale p01 a p%02d, o %s" % (str(ident)[:20], N_PROBLEMAS, SONDA))
    return os.path.join(AQUI, "problemas", ident + ".txt")


def _lee(ruta):
    """El texto del fichero, UTF-8 estricto y sin traducir saltos (como lo lee la boca)."""
    with open(ruta, "rb") as fh:
        return fh.read().decode("utf-8", errors="strict")


def instruccion():
    """La instrucción de los dos modelos: instruccion.txt sin sus saltos de línea finales."""
    return _lee(os.path.join(AQUI, "instruccion.txt")).rstrip("\n")


def entrada(ident):
    """La entrada exacta de `ident`: instrucción + separador + texto."""
    return instruccion() + SEPARADOR + _lee(ruta_texto(ident))


def sha(texto):
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def cota_coste():
    """(peticiones, bytes de entrada, cota en dólares). Un token no ocupa menos de un byte, así que
    los bytes acotan los tokens de entrada sin tokenizador; la salida, al tope por defecto."""
    idents = (SONDA,) + IDS
    octetos = sum(len(entrada(i).encode("utf-8")) for i in idents)
    usd = (octetos * USD_POR_MILLON_ENTRADA + len(idents) * MAX_TOKENS_SALIDA * USD_POR_MILLON_SALIDA) / 1e6
    return len(idents), octetos, usd


# ── La corrida: dónde vive, su manifiesto y sus registros ──────────────────────────────────
def dir_corrida(corrida):
    if not isinstance(corrida, str) or not RE_CORRIDA.match(corrida):
        raise Negado("corrida no válida: letras, cifras, punto y guion, 40 como máximo")
    return os.path.join(_casa.state_dir(), "otra_casa", "piloto", corrida)


def huellas():
    """sha256 de lo que no puede cambiar a mitad de corrida: protocolo, instrucción y problemas."""
    rels = list(FIJADOS) + ["problemas/%s.txt" % i for i in IDS]
    out = {}
    for rel in rels:
        with open(os.path.join(AQUI, *rel.split("/")), "rb") as fh:
            out[rel] = hashlib.sha256(fh.read()).hexdigest()
    return out


def _escribe_nuevo(ruta, obj):
    """JSON en un fichero NUEVO, 0600. Si ya existe, FileExistsError: aquí no se sobrescribe nada."""
    os.makedirs(os.path.dirname(ruta), mode=0o700, exist_ok=True)
    fd = os.open(ruta, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=1, sort_keys=True)
        fh.write("\n")


def _lee_json(ruta):
    with open(ruta, encoding="utf-8") as fh:
        return json.load(fh)


def exigir_manifiesto(corrida, crear=False):
    """Las huellas guardadas al abrir la corrida tienen que ser las de hoy. `crear`: si la corrida
    no tiene manifiesto, se abre ahora. Negado si no hay o si algo cambió."""
    ruta = os.path.join(dir_corrida(corrida), "manifiesto.json")
    ahora = huellas()
    if not os.path.exists(ruta):
        if not crear:
            raise Negado("la corrida %s no está abierta (sin manifiesto): la abren la sonda o el pase de Claude" % corrida)
        _escribe_nuevo(ruta, {"formato": FORMATO, "corrida": corrida, "sha256": ahora,
                              "abierta": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        return ahora
    try:
        guardadas = _lee_json(ruta).get("sha256")
    except (OSError, ValueError, AttributeError):
        raise Negado("manifiesto de la corrida %s ilegible" % corrida)
    if guardadas != ahora:
        cambiados = sorted(k for k in set(ahora) | set(guardadas if isinstance(guardadas, dict) else {})
                           if not isinstance(guardadas, dict) or guardadas.get(k) != ahora.get(k))
        raise Negado("el protocolo o el corpus cambiaron desde que se abrió la corrida %s (%s): ni se "
                     "envía ni se puntúa" % (corrida, ", ".join(cambiados)[:200]))
    return ahora


def ruta_registro(corrida, modelo, ident):
    if modelo not in MODELOS:
        raise Negado("modelo de registro %r: vale claude o grok" % str(modelo)[:20])
    return os.path.join(dir_corrida(corrida), modelo, ident + ".json")


def guarda_registro(corrida, modelo, ident, texto, modelo_devuelto, sha256_peticion=None,
                    ruta_pista=None, uso=None):
    """Un registro de respuesta, con el mismo formato para los dos modelos. No sobrescribe."""
    ruta = ruta_registro(corrida, modelo, ident)
    _escribe_nuevo(ruta, {
        "formato": FORMATO, "corrida": corrida, "problema": ident, "modelo": modelo, "texto": texto,
        "sha256_entrada": sha(entrada(ident)), "sha256_peticion": sha256_peticion,
        "ruta_pista": ruta_pista, "modelo_devuelto": modelo_devuelto,
        "tanda": corrida if modelo == "grok" else None, "uso": uso or {},
        "cuando": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    return ruta


def lee_registro(corrida, modelo, ident):
    """El registro de `ident`, o None si no está. Negado si está y no es el de ese problema, ese
    modelo y esa entrada exacta."""
    ruta = ruta_registro(corrida, modelo, ident)
    if not os.path.exists(ruta):
        return None
    try:
        r = _lee_json(ruta)
    except (OSError, ValueError):
        raise Negado("registro ilegible: %s/%s" % (modelo, ident))
    if (not isinstance(r, dict) or r.get("problema") != ident or r.get("modelo") != modelo
            or r.get("corrida") != corrida or not isinstance(r.get("texto"), str)):
        raise Negado("registro que no es de %s/%s en la corrida %s" % (modelo, ident, corrida))
    if r.get("sha256_entrada") != sha(entrada(ident)):
        raise Negado("el registro %s/%s se hizo con otra entrada (instrucción o texto distintos)" % (modelo, ident))
    return r


def paradas(corrida, ident):
    """Los registros de parada de `ident` en el pase de Grok (puede haber varios, uno por intento)."""
    out = []
    patron = os.path.join(glob.escape(os.path.join(dir_corrida(corrida), "grok")), ident + ".parada-*.json")
    for ruta in sorted(glob.glob(patron)):
        try:
            r = _lee_json(ruta)
        except (OSError, ValueError):
            raise Negado("registro de parada ilegible: %s" % os.path.basename(ruta))
        if isinstance(r, dict):
            out.append(r)
    return out


def corte(corrida, ident):
    """¿Tiene `ident` una parada que BLOQUEA su texto de hoy? (negativa o corte del proveedor)."""
    hoy = sha(entrada(ident))
    return any(p.get("bloquea_texto") is True and p.get("sha256_entrada") == hoy for p in paradas(corrida, ident))


TIPOS_PARADA = ("parada", "condicion", "puerta")


def _guarda_parada(corrida, ident, tipo, motivo, bloquea, status=None, ruta_pista=None):
    """Un registro por envío que no acabó en respuesta guardada. `tipo`: «parada» (del proveedor),
    «condicion» (la respuesta llegó y contradice una condición auditada; `ruta_pista` dice dónde
    quedó) o «puerta» (la boca cerró antes de enviar)."""
    if tipo not in TIPOS_PARADA:
        raise Negado("tipo de parada %r: vale %s" % (str(tipo)[:20], ", ".join(TIPOS_PARADA)))
    ruta = os.path.join(dir_corrida(corrida), "grok", "%s.parada-%s-%s.json"
                        % (ident, time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()), os.urandom(2).hex()))
    _escribe_nuevo(ruta, {"formato": FORMATO, "corrida": corrida, "problema": ident, "modelo": "grok",
                          "tipo": tipo, "motivo": str(motivo)[:200], "status": status,
                          "bloquea_texto": bool(bloquea), "ruta_pista": ruta_pista,
                          "sha256_entrada": sha(entrada(ident)),
                          "cuando": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})


# ── previa: sin red, sin clave, sin sellar, sin escribir ───────────────────────────────────
def previa(salida=print):
    """Guardas de destino (dato aparte), guardas de contenido de la sonda y los 30, y la cota.
    0 si TODO el contenido pasa; 3 si algún texto no pasa."""
    import otra_casa as M
    if M.SEPARADOR != SEPARADOR or M.DESTINO != DESTINO:
        raise Negado("el separador o el destino de otra_casa.py ya no son los de este lanzador")
    instr = instruccion()
    salida("Guardas de destino (lo que falte aquí es de {{TITULAR}} o del estado; no es fallo del corpus):")
    destino_ok = True
    for nombre, ok, detalle in M.estado_destino(DESTINO):
        destino_ok = destino_ok and ok
        salida("  %s %s: %s" % ("✅" if ok else "🛑", nombre, detalle))
    salida("Guardas de contenido (Puerta, borde y centralita, sobre la entrada exacta):")
    try:
        bloqueados = M.textos_bloqueados(DESTINO)
    except Exception as e:                                  # noqa: BLE001 — se informa, no se adivina
        bloqueados = None
        salida("  🛑 no pude leer los textos bloqueados de la cadena (%s)" % type(e).__name__)
    malos, vistos = 0, {}
    for ident in (SONDA,) + IDS:
        try:
            plan = M.preparar(instr, ruta_texto(ident))
        except M.PuertaCerrada as e:
            malos += 1
            salida("  🛑 %s: %s" % (ident, e))
            continue
        if plan["sha256_texto"] in vistos:
            malos += 1
            salida("  🛑 %s: mismo texto que %s" % (ident, vistos[plan["sha256_texto"]]))
            continue
        vistos[plan["sha256_texto"]] = ident
        if bloqueados is None or plan["sha256_texto"] in bloqueados:
            malos += 1
            salida("  🛑 %s: texto bloqueado en la cadena (o no se pudo comprobar)" % ident)
    n, octetos, usd = cota_coste()
    salida("  %d de %d textos pasan (la sonda y %d problemas)" % (n - malos, n, N_PROBLEMAS))
    salida("Cota de coste: %d peticiones, %d bytes de entrada, hasta %d tokens de salida cada una: "
           "como mucho %.2f dólares. Supone que el razonamiento se cobra como salida y cabe en ese tope "
           "(sin verificar); el coste real con esfuerzo xhigh no está medido."
           % (n, octetos, MAX_TOKENS_SALIDA, math.ceil(usd * 100) / 100.0))
    for linea in ANTES_DE_LANZAR:
        salida(linea)
    salida("Contenido: %s · Destino: %s" % ("LISTO" if not malos else "NO PASA",
                                             "listo" if destino_ok else "PENDIENTE (ver arriba)"))
    return 3 if malos else 0


# Lo que el código no puede poner por nadie (PROTOCOLO.md, apartados 4 y 5). Lo imprimen `previa` y `grok`.
SEGUNDO_PLANO = ("`grok` lo lanza {{TITULAR}} en su terminal, o una sesión PRINCIPAL que lo deja EN SEGUNDO PLANO, sigue "
                 "abierta y espera a que termine. Nunca un subagente ni un flujo de agentes (su proceso muere cuando "
                 "responden) ni un Bash de agente en primer plano (corta a los 600 s y cada envío espera hasta 900): "
                 "cualquiera de los dos deja un envío sin desenlace, que solo {{TITULAR}} reanuda.")
ANTES_DE_LANZAR = (
    "Antes de lanzar `grok` (PROTOCOLO.md, apartados 4 y 5):",
    "  · la auditoría de casa base confirmada por {{TITULAR}}: c1, c2, c3 y c4, cada una con su constancia;",
    "  · saldo prepago en el equipo por la cota de arriba;",
    "  · desde casa base y con la rama fusionada (desde un worktree la boca cierra);",
    "  · de día, fuera del silencio nocturno: el primer envío espera a que a {{TITULAR}} le haya llegado su aviso;",
    "  · " + SEGUNDO_PLANO,
    "  · primero la sonda sola (--solo-sonda); se mira su uso; después Claude y después los 30.",
)


# ── apunta-claude: el pase a ciegas, antes que el de Grok ────────────────────────────────────────
def apunta_claude(corrida, ident, modelo, ruta_respuesta):
    """Guarda la respuesta de Claude a `ident`, leída de un fichero. Abre la corrida si es la primera."""
    if ident not in IDS:
        raise Negado("problema %r: el pase de Claude es de p01 a p%02d" % (str(ident)[:20], N_PROBLEMAS))
    if not isinstance(modelo, str) or not modelo.strip():
        raise Negado("falta --modelo: el identificador del modelo de Claude que respondió")
    d_grok = os.path.join(dir_corrida(corrida), "grok")
    if os.path.isdir(d_grok) and any(not f.startswith(SONDA + ".") for f in os.listdir(d_grok)):
        raise Negado("la corrida %s ya tiene algo de Grok sobre el corpus: el pase de Claude iba antes y ya "
                     "no se toca" % corrida)
    exigir_manifiesto(corrida, crear=True)
    otros = modelos_de_claude(corrida) - {modelo.strip()}
    if otros:
        raise Negado("el pase de Claude es con UN modelo para los 30: la corrida %s ya tiene respuestas de %s"
                     % (corrida, ", ".join(sorted(str(m)[:40] for m in otros))))
    with open(ruta_respuesta, "rb") as fh:
        crudo = fh.read(MAX_BYTES_RESPUESTA_CLAUDE + 1)
    if len(crudo) > MAX_BYTES_RESPUESTA_CLAUDE:
        raise Negado("la respuesta pasa de %d bytes" % MAX_BYTES_RESPUESTA_CLAUDE)
    try:
        return guarda_registro(corrida, "claude", ident, crudo.decode("utf-8", errors="strict"), modelo.strip())
    except UnicodeDecodeError:
        raise Negado("la respuesta no es UTF-8 válido")
    except FileExistsError:
        raise Negado("%s ya tiene respuesta de Claude en la corrida %s: un intento por problema" % (ident, corrida))


def faltan_de_claude(corrida):
    return [i for i in IDS if lee_registro(corrida, "claude", i) is None]


def modelos_de_claude(corrida):
    """Los identificadores de modelo que constan en las respuestas de Claude ya apuntadas."""
    registros = (lee_registro(corrida, "claude", i) for i in IDS)
    return {r.get("modelo_devuelto") for r in registros if r is not None}


# ── grok: la sonda y, si va bien, los 30 ───────────────────────────────────────────────────
def grok(corrida, enviar_de_verdad=False, solo_sonda=False, transporte=None, salida=print):
    """El pase de Grok. Devuelve 0 (todo lo pedido se envió y se guardó), 3 (una guarda de la boca
    cerró) o 4 (parada del proveedor). Negado si el lanzador no debe ni empezar."""
    if enviar_de_verdad is not True:
        raise Negado("esto envía texto a xAI de verdad: hace falta la bandera %s" % BANDERA)
    if os.environ.get("BTP_TEST_BATTERY") == "1" and transporte is None:
        raise Negado("batería de tests: el lanzador no envía sin un transporte inyectado")
    d = dir_corrida(corrida)
    exigir_manifiesto(corrida, crear=solo_sonda)        # la sonda sola puede abrir la corrida
    faltan = [] if solo_sonda else faltan_de_claude(corrida)
    if faltan:
        raise Negado("el pase de Claude va antes que los 30 de Grok y le faltan %d respuestas (%s…)"
                     % (len(faltan), faltan[0]))
    import otra_casa as M
    if M.SEPARADOR != SEPARADOR or M.DESTINO != DESTINO:
        raise Negado("el separador o el destino de otra_casa.py ya no son los de este lanzador")
    for ident in IDS:
        if corte(corrida, ident):
            raise Negado("el pase de Grok de la corrida %s acabó en %s con una parada que bloquea el texto: "
                         "no se envía más; toca puntuar" % (corrida, ident))
    if corte(corrida, SONDA):
        raise Negado("la sonda quedó bloqueada: no se envía ningún problema. Hace falta arreglar la boca y "
                     "una sonda con texto nuevo")
    if not solo_sonda and lee_registro(corrida, "grok", SONDA) is None:
        raise Negado("la sonda va sola y la primera (paso 1): lánzala con --solo-sonda y mira su uso; los 30 "
                     "no salen sin la sonda guardada")
    instr = instruccion()
    todos = (SONDA,) if solo_sonda else (SONDA,) + IDS
    pendientes = [i for i in todos if lee_registro(corrida, "grok", i) is None]
    if not pendientes:
        salida("Nada que enviar: la corrida %s ya tiene guardado todo lo pedido" % corrida)
        return 0
    try:                                    # en local y antes de la primera llamada: o todo, o nada
        M.listo(DESTINO)
        bloqueados = M.textos_bloqueados(DESTINO)
        for ident in pendientes:
            if M.preparar(instr, ruta_texto(ident))["sha256_texto"] in bloqueados:
                raise M.PuertaCerrada("%s: ese texto ya recibió una respuesta inaceptable y no se reenvía" % ident)
    except M.PuertaCerrada as e:
        salida("🛑 no se envía nada: %s" % e)
        return 3
    salida(SEGUNDO_PLANO)
    salida("Corrida %s (tanda %s): %d envíos pendientes, de %s a %s. Estado en %s"
           % (corrida, corrida, len(pendientes), pendientes[0], pendientes[-1], d))
    for ident in pendientes:
        try:
            r = M.enviar(DESTINO, instr, ruta_texto(ident), tanda=corrida, avisar=True, transporte=transporte)
        except M.ParadaProveedor as e:
            incumplida = getattr(e, "condicion_incumplida", False) is True
            _guarda_parada(corrida, ident, "condicion" if incumplida else "parada", e.motivo, e.bloquea_texto,
                           e.status, ruta_pista=e.ruta)
            salida("⚠️ PARADA en %s: %s%s" % (ident, e.motivo, " · el texto queda BLOQUEADO" if e.bloquea_texto else ""))
            if incumplida:
                salida("La respuesta llegó y CONTRADICE una condición auditada. Reanudar NO lo arregla: relanzar "
                       "reenviaría el mismo texto bajo la misma condición rota. Primero se corrige la configuración "
                       "o se vuelve a auditar; después reanuda {{TITULAR}}. Su registro no se guarda; la pista está en %s"
                       % (e.ruta or "ningún sitio"))
            else:
                salida("No se reintenta ni se reanuda. Reanudar lo teclea {{TITULAR}} en su terminal.")
            return 4
        except M.PuertaCerrada as e:
            _guarda_parada(corrida, ident, "puerta", e, False)
            salida("🛑 una guarda de la boca cerró en %s: %s" % (ident, e))
            return 3
        try:
            guarda_registro(corrida, "grok", ident, r["texto"], r["modelo"], sha256_peticion=r["sha256_peticion"],
                            ruta_pista=r["ruta"], uso=r["uso"])
        except OSError as e:
            raise Negado("la respuesta de %s llegó y NO pude guardar su registro (%s): paro. La pista está en %s"
                         % (ident, type(e).__name__, r["ruta"] or "ningún sitio"))
        u = r["uso"]
        salida("✅ %s · %s · tokens de entrada %s, de salida %s%s"
               % (ident, r["modelo"], u.get("input_tokens", "?"), u.get("output_tokens", "?"),
                  "" if r["sellada"] else " · ⚠️ respuesta sin sellar en la cadena"))
    if solo_sonda:
        salida("Sonda enviada y guardada. Mira su uso antes de lanzar los 30 (la misma orden sin --solo-sonda).")
    else:
        salida("Pase de Grok completo. Puntúa UNA vez: python3 tools/otra_casa/piloto/puntua.py --corrida %s" % corrida)
    return 0


# ── CLI ────────────────────────────────────────────────────────────────────────────────────
def main(argv):
    ap = argparse.ArgumentParser(prog="corre_piloto.py")
    ap.add_argument("orden", choices=("previa", "entrada", "apunta-claude", "grok"))
    ap.add_argument("--corrida", default="")
    ap.add_argument("--problema", default="")
    ap.add_argument("--modelo", default="")
    ap.add_argument(BANDERA, dest="enviar", action="store_true")
    ap.add_argument("--solo-sonda", action="store_true")
    ap.add_argument("fichero", nargs="?")
    a = ap.parse_intermixed_args(argv)
    try:
        if a.orden == "previa":
            return previa()
        if a.orden == "entrada":
            sys.stdout.write(entrada(a.problema))
            return 0
        if a.orden == "apunta-claude":
            if not a.fichero:
                raise Negado("falta el fichero con la respuesta")
            print("✅ guardada en %s" % apunta_claude(a.corrida, a.problema, a.modelo, a.fichero))
            return 0
        return grok(a.corrida, enviar_de_verdad=a.enviar, solo_sonda=a.solo_sonda)
    except Negado as e:
        print("🛑 %s" % e)
        return 2
    except OSError as e:
        print("🛑 no pude leer o escribir (%s: %s)" % (type(e).__name__, os.path.basename(str(e.filename or ""))))
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
