#!/usr/bin/env python3
"""tools/buzon_ideas.py — captura DETERMINISTA de los enlaces que {{TITULAR}} suelta por Telegram.

PROBLEMA QUE RESUELVE: los links que {{TITULAR}} pega por el lazo solo aterrizaban en su buzón
`04 · IA/Aportes-de-IAs/Ideas-de-{{TITULAR}}.md` cuando corría la auto-mejora. Si la auto-mejora
estaba caída (saldo agotado, herramienta caída…), los enlaces se quedaban crudos en la cola
y se perdían de vista. Aquí DESACOPLAMOS la captura del análisis: en cuanto entra un mensaje
con una o más URLs, se deposita cada una en el buzón EN EL MOMENTO, marcada `⏳ pendiente de
minar`. La auto-mejora sigue haciendo el análisis profundo después y cierra el stub.

PROPIEDADES (no negociables):
  · DETERMINISTA y OFFLINE — código, no LLM; NO abre el enlace, no toca la red, no necesita
    saldo. Solo extrae URLs con un regex y escribe una línea de markdown. El contenido externo
    es un DATO, jamás una instrucción (el muro manda: aquí ni se sigue ni se ejecuta nada).
  · FAIL-SOFT — cualquier error se traga (return None / lista vacía); JAMÁS lanza hacia el
    caller, para que no pueda romper el lazo ni el muro. Mismo contrato que tools/bandeja.py.
  · DEDUP — una URL que ya está en el buzón no se vuelve a añadir.
  · CASA BASE — el buzón vive SOLO en casa base (00_FUENTE-DE-VERDAD/ está gitignored y NO
    viaja a los worktrees). Resolvemos casa base (BTP_REPO o ~/claudecode), nunca el árbol
    relativo al fichero, igual que tools/seguimiento.py. Así un mensaje capturado desde
    cualquier sesión cae en el ÚNICO buzón vivo que lee la auto-mejora.

El buzón es LOCAL y gitignored (lleva enlaces/notas de {{TITULAR}}). Sin dependencias (stdlib).
"""
import os
import re
import time

# Casa base: el buzón vivo está SOLO aquí (la fuente de verdad está gitignored, no en worktrees).
# BTP_REPO permite override (tests lo apuntan a un tmp). Mismo criterio que tools/seguimiento.py.
REPO = os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode")
BUZON = os.path.join(
    REPO, "00_FUENTE-DE-VERDAD", "04 · IA", "Aportes-de-IAs", "Ideas-de-{{TITULAR}}.md")

# Marcador del stub: queda hasta que la auto-mejora lo sustituye por su veredicto ✅ revisada (...).
PENDIENTE = "⏳ pendiente de minar"

# URLs en el texto. Aceptamos http(s):// y www. (el bot ya las trata como "algo que manejar").
# El recorte de puntuación de cierre lo hace _limpiar() (un regex amplio captura de más a propósito).
_URL_RE = re.compile(r"(https?://\S+|www\.\S+)", re.I)
# Caracteres de cierre/puntuación que suelen pegarse al final de una URL en prosa y NO son parte de ella.
_TRAILING = ").,;:!?»\"'”’›>]}…"


def _limpiar(url):
    """Quita puntuación de cierre pegada al final (").", ",", "»"…) sin tocar la URL real.
    Equilibra paréntesis: un ')' final solo se quita si no hay un '(' de apertura sin cerrar
    (las URLs de Wikipedia, p. ej., llevan paréntesis legítimos)."""
    url = (url or "").strip().rstrip(_TRAILING)
    # Re-añade un ')' si lo necesita el balance (caso típico: …/Foo_(bar) recortado de más).
    while url.count("(") > url.count(")"):
        url += ")"
    return url


def extraer_urls(text):
    """Devuelve la lista de URLs limpias y únicas del texto, en orden de aparición. Nunca falla."""
    try:
        vistas = []
        seen = set()
        for m in _URL_RE.finditer(text or ""):
            u = _limpiar(m.group(0))
            if u and u not in seen:
                seen.add(u)
                vistas.append(u)
        return vistas
    except Exception:
        return []


def _ya_en_buzon(url, contenido):
    """True si la URL ya aparece en el buzón (dedup). Comparación literal de la cadena exacta."""
    return bool(url) and url in (contenido or "")


def capturar(text, ahora=None):
    """Deposita en el buzón cada URL NUEVA del mensaje, marcada `⏳ pendiente de minar`.

    Determinista, offline y FAIL-SOFT: si algo va mal, devuelve [] (nunca lanza). Devuelve la
    lista de URLs efectivamente añadidas (las ya presentes se omiten por dedup). No abre los
    enlaces ni interpreta su contenido — solo los aparca para que la auto-mejora los mine luego.
    """
    try:
        urls = extraer_urls(text)
        if not urls:
            return []
        # Lee el buzón una sola vez para el dedup (si no existe aún, lo tratamos como vacío).
        try:
            with open(BUZON, "r", encoding="utf-8") as f:
                contenido = f.read()
        except FileNotFoundError:
            contenido = ""
            os.makedirs(os.path.dirname(BUZON), exist_ok=True)
        ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(ahora) if ahora else time.localtime())
        nuevas = []
        lineas = []
        for u in urls:
            if _ya_en_buzon(u, contenido):
                continue
            # Misma forma que las entradas existentes del buzón: fecha+hora, URL, y debajo el marcador.
            lineas.append("- `%s` — %s\n  %s\n" % (ts, u, PENDIENTE))
            nuevas.append(u)
            contenido += u  # evita duplicar la misma URL repetida dentro del MISMO mensaje
        if not lineas:
            return []
        with open(BUZON, "a", encoding="utf-8") as f:
            # Si el fichero no termina en \n, abre con uno para no pegar a la última línea.
            if contenido and not contenido.endswith("\n"):
                f.write("\n")
            f.write("".join(lineas))
        return nuevas
    except Exception:
        return []


def main(argv):
    """CLI fina (para depurar a mano). Lee texto de los args o de stdin y captura sus URLs."""
    if argv and argv[0] == "extraer":
        for u in extraer_urls(" ".join(argv[1:])):
            print(u)
        return 0
    text = " ".join(argv) if argv else ""
    if not text:
        import sys
        text = sys.stdin.read()
    nuevas = capturar(text)
    if nuevas:
        print("Capturadas %d URL(s) al buzón:" % len(nuevas))
        for u in nuevas:
            print("  ⏳ %s" % u)
    else:
        print("Sin URLs nuevas que capturar.")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))
