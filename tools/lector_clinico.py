#!/usr/bin/env python3
"""tools/lector_clinico.py — Ventanilla AUDITADA para leer dato clínico (N2).

Por qué (Capa C del guardián): la Capa A bloquea la herramienta Read sobre rutas clínicas.
Este script es el canal SANCIONADO para que los agentes clínicos (oncologo-virtual,
comite-medico, herramientas-medicas, verificacion) lean un informe crudo cuando el RAG no
basta — y deja REGISTRO de cada acceso, que hoy no existe.

Uso:  python3 tools/lector_clinico.py <ruta_fichero_clinico>
      python3 tools/lector_clinico.py --binario <ruta>        # bytes crudos a stdout
      python3 tools/lector_clinico.py --a <destino> <ruta>    # copia intacta en zona clínica
      python3 tools/lector_clinico.py --texto <ruta.pdf>      # capa de texto (NO es el original)
      python3 tools/lector_clinico.py listar [patrón] [--dir <zona>] [--max N]
      python3 tools/lector_clinico.py retirar <ruta> --motivo "<por qué>"
      python3 tools/lector_clinico.py procesa <procesador> [-- args…]
  · `listar` (20-sep-26): la ventanilla sabía SERVIR un fichero pero no sabía decir cuáles
    hay ni dónde está uno, y el guard bloquea `ls`/`find`/`glob` sobre zona clínica — con
    razón. Resultado: cualquier hallazgo que nombrara un fichero moría ahí. Pasó de verdad
    ese mismo día: `ocr_informes` reportó dos PDF que no podía OCRear imprimiendo solo su
    nombre, y no hubo forma sancionada de localizarlos para ver qué les pasaba.
    Devuelve RUTAS, nunca contenido: es metadato, y su ausencia no protegía nada — solo
    dejaba el sistema manco y empujaba a rodear el guard, que es peor.
  · BINARIOS (19-sep-26, deuda `lector-clinico-corrompe-pdf-binario`): la ventanilla servía
    TODO con `open(…, encoding="utf-8", errors="ignore")`, así que un PDF escaneado salía
    destrozado — los bytes que no decodifican desaparecían sin ruido. pdftoppm renderizaba
    páginas en blanco y tesseract devolvía 0 bytes: ningún original de imagen se podía cotejar
    por el canal sancionado, y las notas que decían «verificado contra el original» se apoyaban
    en transcripciones. Ahora se sirve SIEMPRE en binario (`rb` → `sys.stdout.buffer`), y lo
    que sale es idéntico a lo que entra (mismo sha256).
  · Un binario no se vuelca a stdout por accidente: si el contenido es binario y no se pidió
    ni `--binario` ni `--a`, se RECHAZA. Tirar megas de PDF al contexto de un agente lo
    envenena y cuesta dinero; con `--a` el fichero viaja por disco y solo sale el resumen.
  · `--a <destino>`: el destino tiene que ser ZONA CLÍNICA por el mismo predicado
    (`zonas_clinicas.es_ruta_clinica`) con el que se decide si se puede LEER el origen — el
    del guard, no el de `procesa`, que no comprueba rutas. Fail-closed: si no lo es, no se
    escribe nada; y no se sobrescribe nada que ya exista. El dato clínico no sale de la zona
    clínica por esta ventanilla.
  · `--texto` (20-sep-26): la mayoría de los informes son PDF CON capa de texto, y pedirle a
    cada agente que tubee `--binario` a `pdftotext` es fricción sobre el caso más común. Este
    modo la quita, pero SIN romper el sello de evidencia: lo que sirve es una DERIVACIÓN, no el
    original, así que lo dice en stderr y lo registra aparte en el log (`LEIDO-texto-pdftotext`
    / `LEIDO-texto-sidecar-ocr`, nunca `LEIDO`). Esa distinción es justo la que faltaba cuando
    unas notas decían «verificado contra el original» apoyándose en transcripciones. Si el PDF
    está escaneado, sirve el sidecar `.ocr.txt` si lo hay y, si no, lo dice y manda a OCRearlo
    por la ventanilla — nunca inventa ni devuelve vacío como si fuera el informe.
  · `retirar` (20-sep-26): un archivo clínico también acumula basura — el caso que lo trajo
    fueron dos PDF de 963 bytes titulados «Anatomía patológica» que están EN BLANCO, y que
    hacen creer que ese informe existe cuando no hay nada dentro. Hasta hoy no había vía
    sancionada para quitarlos: la ventanilla sabía leer y copiar, y nada más, así que la
    única salida era saltarse el guard con su propia llave y sin dejar registro.
    NO BORRA, mueve a `_RETIRADOS/` junto a donde estaba (sigue siendo zona clínica, sigue
    protegido, y se deshace arrastrándolo de vuelta). Exige `--motivo`: una retirada sin
    razón escrita es cómo se pudre un archivo. Deja su línea en el log, como todo.
  · `procesa` (19-sep-26): hay datos clínicos que no son texto (DICOM, NIfTI) y que un
    PROGRAMA tiene que leer, no un humano. Antes no cabían por aquí y se corrían por fuera
    (la segmentación del 17-sep no dejó ni una línea en el log). Ahora la ventanilla lanza
    un procesador de una ALLOWLIST por nombre (ruta exacta en el repo, intérprete fijo),
    registra el acceso con sus argumentos y devuelve su código de salida.
  · Solo sirve ficheros bajo el allowlist clínico (resuelve symlinks/`..` antes de decidir,
    para que no se pueda evadir la comprobación). Rechaza y registra lo demás.
  · Registra: timestamp, agente (env BTP_AGENT/CLAUDE_AGENT si está), resultado, ruta.
  · El log es LOCAL y gitignored (revela QUÉ informes se abrieron, no su contenido).

Límite honesto: NO es RBAC estricto — un agente con Bash podría leer por otra vía. Da (a) un
canal limpio ahora que la Capa A bloquea la herramienta Read, y (b) auditoría. Para que sea el
ÚNICO canal habría que quitar Bash a los clínicos (lockdown mayor, no hecho).
"""
import os, sys, time

HOME = os.path.expanduser("~")
REPO = os.environ.get("BTP_REPO") or os.path.join(HOME, "claudecode")

# La ventanilla sirve EXACTAMENTE lo que el guard deniega, usando el mismo predicado. Antes
# tenía su propia lista de raíces y había divergido: `_PRIVADO_EXPEDIENTE` (e `informes/`, y
# `docu enviada a nova/`) estaban denegados por el guard pero la ventanilla tampoco sabía
# servirlos → callejón sin salida, ni por la puerta ni por la ventana. Compartir el predicado
# cierra eso por construcción, no sincronizando dos listas que volverán a separarse.
# Import por ruta explícita: `.claude/hooks` no está en el sys.path de tools/, y meterlo
# tendría su propio riesgo (tools/queue.py ya sombrea el `queue` de la stdlib).
def _cargar_politica():
    import importlib.util
    ruta = os.path.join(REPO, ".claude", "hooks", "zonas_clinicas.py")
    if not os.path.exists(ruta):
        # …y si el repo resuelto no la tiene, prueba junto a este mismo fichero (worktrees).
        ruta = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            ".claude", "hooks", "zonas_clinicas.py")
    spec = importlib.util.spec_from_file_location("zonas_clinicas", ruta)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Fail-CLOSED, al revés que los hooks: esto NO está en el camino caliente, y una ventanilla
# que no puede confirmar su propia política no debe servir nada.
try:
    ZC = _cargar_politica()
except Exception as _e:
    ZC, _ZC_ERR = None, repr(_e)

LOG = os.path.join(REPO, ".claude", "logs", "clinico-access.log")

def _limpio(x):
    """El log es TSV de una línea por acceso, y la ruta la escribe quien llama. Un destino con
    un `\n` dentro inyectaba una línea ENTERA falsa en el registro de accesos clínicos (lo cazó
    `verificacion` en la caja revision-contribuciones, 20-sep-26). Un registro que se puede
    falsificar desde el argumento no es un registro."""
    return str(x).replace("\\", "\\\\").replace("\t", "\\t").replace("\r", "\\r").replace("\n", "\\n")


# La verificación de identidad vive en `tools/identidad_paciente.py` (testable sin datos
# clínicos y reutilizable por el comité). Si no se puede importar, la ventanilla NO sirve nada:
# una ventanilla que no sabe de quién es el informe es exactamente lo que la norma prohíbe.
try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import identidad_paciente as _ID
except Exception as _e:          # pragma: no cover
    _ID = None
    _ID_ERR = _e


def _sirve(veredicto):
    """¿Se puede servir con ese veredicto? Sin módulo → NO (fail-closed, no AttributeError)."""
    return bool(_ID) and _ID.SIRVE.get(veredicto, False)


def _pide_aviso(veredicto):
    return bool(_ID) and veredicto in _ID.PIDE_AVISO


def _verificar_identidad(texto, ruta=None):
    if _ID is None:
        return "sin_modulo", ("no pude cargar identidad_paciente.py (%s)" % _ID_ERR)
    try:
        return _ID.verificar(texto, ruta)
    except Exception as e:
        return "error", ("la verificación de identidad falló (%s)" % type(e).__name__)


def _identidad_ok(agent, path, texto):
    """(True, veredicto) si se puede servir; (False, veredicto) si hay que RECHAZAR ya.

    Un único sitio para las 3 puertas de salida de contenido (main() default, y las dos ramas
    de sirve_texto): sidecar OCR y pdftotext. Imprime el rechazo/aviso por stderr y loguea;
    no toca stdout — quien llama decide qué escribir ahí."""
    veredicto, detalle = _verificar_identidad(texto, path)
    if not _sirve(veredicto):
        _log(agent, "RECHAZADO-identidad-%s" % veredicto, path)
        print("RECHAZADO (identidad): %s.\n"
              "Estar en su carpeta NO prueba que sea suyo. Si de verdad necesitas este "
              "documento, ábrelo tú y confirma la filiación antes de usar un solo dato."
              % detalle, file=sys.stderr)
        return False, veredicto
    if _pide_aviso(veredicto):
        print("⚠️  IDENTIDAD NO ACREDITADA: %s.\n"
              "    Estar en su carpeta no prueba que sea suyo: verifica nombre y fecha de "
              "nacimiento antes de usar cualquier dato de este documento." % detalle,
              file=sys.stderr)
    return True, veredicto


def _log(agent, result, path):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(LOG, "a", encoding="utf-8") as f:
            f.write("%s\t%s\t%s\t%s\n" % (ts, _limpio(agent), _limpio(result), _limpio(path)))
    except Exception:
        pass  # el log nunca debe romper la lectura

# Procesadores sancionados: nombre -> (script relativo al repo, intérprete). Nada fuera de
# aquí se ejecuta por la ventanilla. El script se resuelve junto a ESTE fichero (worktree
# incluido: lo que se prueba en una rama es el código de esa rama); el intérprete, en casa
# base, que es donde vive la venv de imagen.
PROCESADORES = {
    "visor3d": ("tools/visor3d.py", os.path.join(REPO, ".venv-imagen", "bin", "python")),
    # `ocr_informes` estaba fuera y el guard lo bloqueaba (deuda
    # `historial-pdf-sin-texto-ventanilla`): 3 informes escaneados no se podían leer NI por la
    # ventanilla (no es texto) NI por el OCR (el guard lo paraba). Callejón sin salida. Es
    # stdlib + tesseract/poppler locales, egress 0, y no toca el original (escribe un sidecar
    # `.ocr.txt` al lado), así que cabe por aquí con su línea en el log como todo lo demás.
    "ocr_informes": ("tools/ocr_informes.py", "/usr/bin/python3"),
}


def _raices_para_listar():
    """Dónde busca `listar` por defecto: las raíces ancladas que existan, más las carpetas
    de nombre clínico que cuelgan de la fuente de verdad.

    Se descubren, no se listan a mano: una lista fija aquí volvería a divergir de
    `zonas_clinicas`, que es el error que ese módulo existe para no repetir.

    Límite honesto: una carpeta de nombre clínico que viva FUERA de esas raíces (un
    `/tmp/loquesea/_PRIVADO_CLINICO/`) no se descubre sola — encontrarla exigiría barrer el
    disco entero. Para esa, `--dir`. Sigue sin poder listarse nada que no sea zona clínica.
    """
    fuera = []
    for r in ZC.raices():
        if os.path.isdir(r):
            fuera.append(r)
    fv = os.path.join(REPO, "00_FUENTE-DE-VERDAD")
    if os.path.isdir(fv):
        for base, dirs, _ in os.walk(fv):
            for d in list(dirs):
                ruta = os.path.join(base, d)
                if ZC.es_segmento_clinico(d):
                    fuera.append(ruta)
                    dirs.remove(d)          # ya entra entera; no hace falta seguir bajando
    # quita las que ya están contenidas en otra (evita listar dos veces el mismo árbol)
    fuera = sorted({os.path.realpath(x) for x in fuera})
    limpias = []
    for r in fuera:
        if not any(r != o and r.startswith(o + os.sep) for o in fuera):
            limpias.append(r)
    return limpias


def _casa_patron(nombre, patron):
    """fnmatch sobre el nombre, sin tildes ni mayúsculas: se busca «anatomia» y aparece
    «Anatomía». Sin comodines, se trata como subcadena, que es como busca una persona."""
    import fnmatch
    n, p = ZC.normaliza(nombre), ZC.normaliza(patron)
    if not p:
        return True
    if any(c in p for c in "*?["):
        return fnmatch.fnmatch(n, p)
    return p in n


def listar(agent, argv):
    """Rutas de zona clínica que casan con el patrón. NUNCA contenido."""
    patron, dirs, tope = "", [], 200
    i = 0
    while i < len(argv):
        t = argv[i]
        if t == "--dir":
            i += 1
            if i >= len(argv):
                print("RECHAZADO: --dir necesita una ruta.", file=sys.stderr)
                return 2
            dirs.append(argv[i])
        elif t == "--max":
            i += 1
            try:
                tope = max(1, int(argv[i]))
            except (IndexError, ValueError):
                print("RECHAZADO: --max necesita un número.", file=sys.stderr)
                return 2
        elif t.startswith("-"):
            print("RECHAZADO: bandera desconocida %s" % t, file=sys.stderr)
            return 2
        elif not patron:
            patron = t
        else:
            print("RECHAZADO: sobra el argumento %r" % t, file=sys.stderr)
            return 2
        i += 1
    if dirs:
        raices = []
        for d in dirs:
            r = os.path.realpath(os.path.expanduser(d))
            if not ZC.es_ruta_clinica(r):
                _log(agent, "RECHAZADO-listar-fuera-de-allowlist", r)
                print("RECHAZADO: %s no es zona clínica; para lo de fuera están las tools "
                      "normales." % r, file=sys.stderr)
                return 1
            if not os.path.isdir(r):
                print("RECHAZADO: %s no es un directorio." % r, file=sys.stderr)
                return 1
            raices.append(r)
    else:
        raices = _raices_para_listar()
    hallados, vistos, truncado = [], set(), False
    for raiz in raices:
        for base, subdirs, ficheros in os.walk(raiz, followlinks=False):
            # Un enlace que salga de la zona no se sigue NI se nombra: misma regla que el
            # sidecar de `--texto` y que el destino de `--a`. Quien impide DESCENDER es el
            # `followlinks=False` de arriba; esta poda es el cinturón encima (cubre un
            # directorio que no es symlink pero cuyo realpath sale de la zona). Por eso el
            # mutante que la quita sigue en verde: no es un hueco del test, es una capa
            # redundante a propósito, y se dice en vez de fingir que hay cobertura.
            subdirs[:] = [d for d in subdirs
                          if ZC.es_ruta_clinica(os.path.realpath(os.path.join(base, d)))]
            for f in ficheros:
                ruta = os.path.join(base, f)
                if os.path.islink(ruta) and not ZC.es_ruta_clinica(os.path.realpath(ruta)):
                    continue
                if not _casa_patron(f, patron):
                    continue
                real = os.path.realpath(ruta)
                if real in vistos:
                    continue
                vistos.add(real)
                if len(hallados) >= tope:
                    truncado = True
                    break
                hallados.append(ruta)
            if truncado:
                break
        if truncado:
            break
    _log(agent, "LISTADO-%d" % len(hallados), "patrón=%s" % (patron or "*"))
    for r in sorted(hallados):
        print(r)
    if truncado:
        print("… cortado en %d (usa --max o afina el patrón)." % tope, file=sys.stderr)
    elif not hallados:
        print("0 ficheros casan con %r en %d raíz(ces) clínica(s)." % (patron, len(raices)),
              file=sys.stderr)
    return 0


RETIRADOS = "_RETIRADOS"


def retirar(agent, argv):
    """Mueve un fichero clínico a `_RETIRADOS/` junto a donde estaba. Nunca borra.

    Por qué cuarentena y no borrado: un fichero clínico borrado no se recupera (esta zona
    está gitignorada a propósito), y la mayoría de las veces lo que parece basura es un
    archivado torpe. Mover es reversible, borrar no; y quien quiera destruir de verdad ya
    tiene el Finder y su propio criterio.
    """
    ruta, motivo, i = None, None, 0
    while i < len(argv):
        t = argv[i]
        if t == "--motivo":
            i += 1
            if i >= len(argv):
                print("RECHAZADO: --motivo necesita un texto.", file=sys.stderr)
                return 2
            motivo = argv[i]
        elif t.startswith("--motivo="):
            motivo = t[len("--motivo="):]
        elif t.startswith("-"):
            print("RECHAZADO: bandera desconocida %s" % t, file=sys.stderr)
            return 2
        elif ruta is None:
            ruta = t
        else:
            print("RECHAZADO: sobra el argumento %r" % t, file=sys.stderr)
            return 2
        i += 1
    if not ruta:
        print('uso: python3 tools/lector_clinico.py retirar <ruta> --motivo "<por qué>"',
              file=sys.stderr)
        return 2
    if not (motivo or "").strip():
        print("RECHAZADO: falta --motivo. Una retirada sin razón escrita no se puede revisar "
              "dentro de seis meses.", file=sys.stderr)
        return 2
    origen = os.path.realpath(os.path.expanduser(ruta))
    if not ZC.es_ruta_clinica(origen):
        _log(agent, "RECHAZADO-retirar-fuera-de-allowlist", origen)
        print("RECHAZADO: %s no es zona clínica; esta puerta solo mueve dentro del archivo "
              "clínico." % origen, file=sys.stderr)
        return 1
    if not os.path.isfile(origen):
        _log(agent, "RECHAZADO-retirar-no-existe", origen)
        print("RECHAZADO: no existe o no es un fichero.", file=sys.stderr)
        return 1
    if os.path.basename(os.path.dirname(origen)) == RETIRADOS:
        print("RECHAZADO: ya está retirado.", file=sys.stderr)
        return 1
    destino_dir = os.path.join(os.path.dirname(origen), RETIRADOS)
    # El destino cuelga del mismo directorio, así que es zona clínica por construcción. Se
    # comprueba igual: barato, y si algún día cambia el predicado esto no se queda atrás.
    if not ZC.es_ruta_clinica(destino_dir):
        _log(agent, "RECHAZADO-retirar-destino-no-clinico", destino_dir)
        print("RECHAZADO: %s no sería zona clínica." % destino_dir, file=sys.stderr)
        return 1
    destino = os.path.join(destino_dir, os.path.basename(origen))
    if os.path.lexists(destino):
        _log(agent, "RECHAZADO-retirar-destino-ocupado", destino)
        print("RECHAZADO: ya hay un %s retirado; míralo antes de retirar otro igual."
              % os.path.basename(origen), file=sys.stderr)
        return 1
    try:
        os.makedirs(destino_dir, mode=0o700, exist_ok=True)
        os.rename(origen, destino)          # mismo sistema de ficheros: atómico
    except OSError as e:
        _log(agent, "RECHAZADO-retirar-fallo", "%s (%s)" % (origen, e.strerror))
        print("RECHAZADO: no pude mover (%s)." % e.strerror, file=sys.stderr)
        return 1
    try:
        with open(destino + ".retirada.txt", "a", encoding="utf-8") as f:
            f.write("%s\t%s\t%s\t%s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"),
                                            _limpio(agent), _limpio(motivo), _limpio(origen)))
    except OSError:
        pass                                 # la nota es un extra; el movimiento ya está hecho
    _log(agent, "RETIRADO", "%s -> %s (%s)" % (origen, destino, motivo))
    print("Retirado a %s\nMotivo: %s\nSe deshace moviéndolo de vuelta a %s"
          % (destino, motivo, os.path.dirname(origen)), file=sys.stderr)
    return 0


def procesa(agent, argv):
    import subprocess
    if not argv or argv[0] not in PROCESADORES:
        _log(agent, "RECHAZADO-procesador-no-sancionado", " ".join(argv)[:200])
        print("RECHAZADO: procesador no sancionado. Válidos: %s" % ", ".join(sorted(PROCESADORES)),
              file=sys.stderr)
        return 1
    nombre, args = argv[0], argv[1:]
    if args[:1] == ["--"]:
        args = args[1:]
    rel, interprete = PROCESADORES[nombre]
    raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    script = os.path.join(raiz, rel)
    if not os.path.isfile(script) or not os.path.isfile(interprete):
        _log(agent, "RECHAZADO-procesador-ausente", nombre)
        print("RECHAZADO: falta %s o su intérprete %s." % (script, interprete), file=sys.stderr)
        return 1
    _log(agent, "PROCESA-%s" % nombre, " ".join(args)[:400])
    env = dict(os.environ, BTP_VENTANILLA="1")
    return subprocess.call([interprete, script] + args, env=env)


CHUNK = 1 << 20   # 1 MiB: copia en streaming, sin cargarse un DICOM entero en RAM


# Firmas de formatos binarios. Sin esto, un PDF cuyos primeros 8 KiB son ASCII (los hay: el
# catálogo y el xref van delante) pasaba el filtro de cabecera y se volcaba ENTERO a stdout —
# justo el envenenamiento de contexto que el rechazo existe para evitar (`verificacion`,
# 20-sep-26). Lista conservadora a propósito: nada que pueda ser el principio de un informe
# de texto (`BM` de BMP se queda fuera: un informe puede empezar por «BMI»).
MAGIAS = (b"%PDF", b"\x89PNG", b"\xff\xd8\xff", b"GIF8", b"PK\x03\x04", b"\x1f\x8b",
          b"II*\x00", b"MM\x00*", b"OggS", b"RIFF", b"\x7fELF", b"\xd0\xcf\x11\xe0")


def no_es_texto_utf8(cabecera):
    """Heurística de git/grep sobre los primeros KiB: un NUL, o algo que no decodifica.

    Se llama por lo que MIDE, no por lo que uno querría que midiera: un informe en latin-1
    cae aquí igual que un DICOM. El mensaje de rechazo lo dice con esas palabras, porque
    llamar «DICOM» a un .txt de hospital manda a un agente a montar un OCR que no hace falta.

    A propósito NO decide CÓMO se sirve (siempre se sirven bytes crudos, byte a byte): decide
    si hay que pedirlo a propósito, para que nadie se vuelque un PDF de 3 MB en el contexto.
    """
    if any(cabecera.startswith(m) for m in MAGIAS):
        return True
    if cabecera[128:132] == b"DICM":          # DICOM: la magia va en el offset 128
        return True
    if b"\x00" in cabecera:
        return True
    try:
        cabecera.decode("utf-8")
    except UnicodeDecodeError as e:
        # un carácter multibyte partido por el corte de la cabecera no es un binario
        return e.start < len(cabecera) - 4
    return False


# pdftotext, pinchado por ruta y no por PATH: `procesa` ya fija sus intérpretes por el mismo
# motivo (un PATH manipulable es una vía para que corra otro binario sobre dato clínico).
_PDFTOTEXT = ("/opt/homebrew/bin/pdftotext", "/usr/local/bin/pdftotext", "/usr/bin/pdftotext")


def _pdftotext():
    for c in _PDFTOTEXT:
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


def sirve_texto(agent, path):
    """La capa de texto del PDF. Devuelve el código de salida.

    Regla de la casa: esto NO es el original. Cada salida va marcada en stderr y con su propio
    resultado en el log, para que nadie pueda decir «cotejado contra el original» habiendo leído
    una extracción. El original byte a byte sigue estando a un `--a` de distancia.
    """
    sidecar = path + ".ocr.txt"
    if os.path.lexists(sidecar):
        # El sidecar es OTRO fichero, así que pasa por la MISMA puerta que el original. Sin esto,
        # un symlink `informe.pdf.ocr.txt -> /cualquier/cosa` colocado en la carpeta clínica
        # convertía la ventanilla en lector de ficheros de fuera, y encima el log decía que se
        # había leído el PDF. Lo reproduje el 20-sep-26 con un canario: salía. Misma clase que el
        # tobogán del destino de `--a`; se cierra igual, resolviendo el enlace ANTES de decidir.
        real = os.path.realpath(sidecar)
        if os.path.islink(sidecar) or not ZC.es_ruta_clinica(real) or not os.path.isfile(real):
            _log(agent, "RECHAZADO-sidecar-fuera-de-allowlist", sidecar)
            print("RECHAZADO: el sidecar %s no es un fichero de zona clínica (¿un enlace?). "
                  "La ventanilla no lo sirve." % sidecar, file=sys.stderr)
            return 1
        try:
            al_dia = os.path.getmtime(real) >= os.path.getmtime(path)
        except OSError:
            al_dia = False
        if al_dia:
            with open(real, "rb") as f:
                datos = f.read()
            # ¿de quién es este informe? (feedback-verificar-identidad-paciente-en-informe)
            ok, veredicto = _identidad_ok(agent, path, datos.decode("utf-8", errors="ignore"))
            if not ok:
                return 1
            _log(agent, "LEIDO-texto-sidecar-ocr-%s" % veredicto, real)   # lo que se sirvió DE VERDAD
            print("⚠️  Esto es el sidecar OCR (%s), NO el original. Para cotejar bytes: --a"
                  % os.path.basename(sidecar), file=sys.stderr)
            sys.stdout.buffer.write(datos)
            sys.stdout.buffer.flush()
            return 0
    if not path.lower().endswith(".pdf"):
        _log(agent, "RECHAZADO-texto-no-pdf", path)
        print("RECHAZADO: --texto es para PDF. Si ya es texto, léelo sin banderas; si es otro "
              "binario, usa --binario o --a.", file=sys.stderr)
        return 1
    exe = _pdftotext()
    if not exe:
        _log(agent, "RECHAZADO-sin-pdftotext", path)
        print("RECHAZADO: no encuentro pdftotext (brew install poppler). No invento el texto.",
              file=sys.stderr)
        return 1
    import subprocess
    try:
        r = subprocess.run([exe, "-q", "-layout", path, "-"], capture_output=True, timeout=180)
    except Exception as e:
        _log(agent, "RECHAZADO-pdftotext-falla", path)
        print("RECHAZADO: pdftotext falló (%r)." % e, file=sys.stderr)
        return 1
    if r.returncode != 0 or not r.stdout.strip():
        _log(agent, "RECHAZADO-pdf-sin-capa-de-texto", path)
        print("RECHAZADO: este PDF no tiene capa de texto (está escaneado) y no hay sidecar "
              ".ocr.txt al día. NO te devuelvo vacío como si fuera el informe. Sácalo con OCR "
              "local por la ventanilla:\n"
              "    python3 tools/lector_clinico.py procesa ocr_informes -- --apply --dir "
              "\"%s\"\ny vuelve a pedirlo." % os.path.dirname(path), file=sys.stderr)
        return 1
    # ¿de quién es este informe? (feedback-verificar-identidad-paciente-en-informe)
    ok, veredicto = _identidad_ok(agent, path, r.stdout.decode("utf-8", errors="ignore"))
    if not ok:
        return 1
    _log(agent, "LEIDO-texto-pdftotext-%s" % veredicto, path)
    print("⚠️  Esto es la capa de texto del PDF (pdftotext -layout), NO el original byte a "
          "byte. Para cotejar el original: --a <destino en zona clínica>.", file=sys.stderr)
    sys.stdout.buffer.write(r.stdout)
    sys.stdout.buffer.flush()
    return 0


def _destino_sancionado(destino, origen):
    """(ruta_absoluta, None) si se puede escribir ahí; (None, motivo) si no.

    Mismo criterio que la entrada: los symlinks se resuelven ANTES de decidir, para que un
    enlace colocado en zona clínica no haga de tobogán hacia fuera.
    """
    d = os.path.expanduser(str(destino or ""))
    if not d:
        return None, "destino vacío"
    if not os.path.isabs(d):
        d = os.path.join(os.getcwd(), d)
    d = os.path.normpath(d)
    if os.path.islink(d):
        return None, "el destino %s es un symlink; no se escribe a través de enlaces" % d
    padre = os.path.dirname(d)
    if not os.path.isdir(padre):
        return None, "el directorio destino no existe: %s" % padre
    d = os.path.join(os.path.realpath(padre), os.path.basename(d))
    if ZC is None:
        return None, "sin política de zonas clínicas"
    if not ZC.es_ruta_clinica(d):
        return None, ("%s NO es zona clínica; el dato clínico no sale de la zona clínica "
                      "por esta ventanilla" % d)
    # `realpath` NO basta para decir «no es el original»: un HARDLINK tiene otra ruta y el
    # MISMO inodo, así que `--a <hardlink> <original>` truncaba el original clínico a la mitad
    # del búfer (3 MB → 128 KiB, con rc=0 y un «copiados intactos» en pantalla). Lo reprodujo
    # `verificacion` el 20-sep-26 y lo confirmé yo: la ventanilla que existe para PODER cotejar
    # originales era capaz de destruirlos. Se compara por inodo, y además NO se escribe sobre
    # NADA que ya exista (abajo, O_EXCL): un destino que ya está es un error, no una orden.
    if os.path.lexists(d):
        try:
            mismo = os.path.samefile(d, origen)
        except OSError:
            mismo = False
        if mismo:
            return None, ("el destino %s es el PROPIO original (mismo inodo, aunque la ruta "
                          "sea otra): escribirlo lo destruiría" % d)
        if os.path.isdir(d):
            return None, "el destino %s es un directorio; dame la ruta del fichero" % d
        return None, ("el destino %s YA EXISTE; esta ventanilla no sobrescribe dato clínico. "
                      "Elige otro nombre." % d)
    return d, None


def _vuelca(f, salida, cabecera):
    """Copia byte a byte: lo que entra es lo que sale."""
    salida.write(cabecera)
    while True:
        trozo = f.read(CHUNK)
        if not trozo:
            break
        salida.write(trozo)
    salida.flush()


def _parse(argv):
    """(ruta, destino, binario, texto, error). Las banderas valen en cualquier posición."""
    ruta, destino, binario, texto, i = None, None, False, False, 0
    while i < len(argv):
        t = argv[i]
        if t == "--binario":
            binario = True
        elif t == "--texto":
            texto = True
        elif t == "--a":
            i += 1
            if i >= len(argv):
                return None, None, False, False, "--a necesita una ruta de destino"
            destino = argv[i]
        elif t.startswith("--a="):
            destino = t[4:]
        elif t.startswith("-") and t != "-":
            return None, None, False, False, "bandera desconocida: %s" % t
        elif ruta is None:
            ruta = t
        else:
            return None, None, False, False, "sobra el argumento %r" % t
        i += 1
    if ruta is None:
        return None, None, False, False, "falta la ruta del fichero clínico"
    if texto and (destino or binario):
        return None, None, False, False, ("--texto no se combina con --a ni --binario: uno sirve "
                                          "una derivación y los otros el original")
    return ruta, destino, binario, texto, None


USO = ("uso: python3 tools/lector_clinico.py [--binario | --texto | --a <destino>] <ruta>\n"
       "     python3 tools/lector_clinico.py listar [patrón] [--dir <zona>] [--max N]")


def main(argv):
    if not argv:
        print(USO, file=sys.stderr)
        return 2
    agent = (os.environ.get("BTP_AGENT") or os.environ.get("CLAUDE_AGENT")
             or os.environ.get("USER") or "?")
    if argv[0] == "procesa":
        return procesa(agent, argv[1:])
    if argv[0] == "retirar":
        if ZC is None:
            _log(agent, "RECHAZADO-sin-politica", "retirar")
            print("RECHAZADO: no pude cargar zonas_clinicas.py (%s)." % _ZC_ERR, file=sys.stderr)
            return 1
        return retirar(agent, argv[1:])
    if argv[0] == "listar":
        if ZC is None:
            _log(agent, "RECHAZADO-sin-politica", "listar")
            print("RECHAZADO: no pude cargar zonas_clinicas.py (%s)." % _ZC_ERR,
                  file=sys.stderr)
            return 1
        return listar(agent, argv[1:])
    ruta, destino, binario, texto, err = _parse(argv)
    if err:
        print("RECHAZADO: %s\n%s" % (err, USO), file=sys.stderr)
        return 2
    path = os.path.realpath(os.path.expanduser(ruta))  # resuelve symlinks/.. ANTES de decidir
    if ZC is None:
        _log(agent, "RECHAZADO-sin-politica", path)
        print("RECHAZADO: no pude cargar zonas_clinicas.py (%s). Una ventanilla que no sabe "
              "cuál es su allowlist no sirve nada." % _ZC_ERR, file=sys.stderr)
        return 1
    if not ZC.es_ruta_clinica(path):
        _log(agent, "RECHAZADO-fuera-de-allowlist", path)
        print(f"RECHAZADO: {path} no es zona clínica — léelo con las tools normales.",
              file=sys.stderr)
        return 1
    if not os.path.isfile(path):
        _log(agent, "no-existe", path)
        print("RECHAZADO: no existe o no es un fichero.", file=sys.stderr)
        return 1
    if texto:
        return sirve_texto(agent, path)
    dest = None
    if destino is not None:
        dest, motivo = _destino_sancionado(destino, path)
        if dest is None:
            _log(agent, "RECHAZADO-destino-no-clinico", "%s -> %s" % (path, destino))
            print("RECHAZADO: %s." % motivo, file=sys.stderr)
            return 1
    with open(path, "rb") as f:                 # 'rb': el PDF escaneado sale entero
        cabecera = f.read(8192)
        if dest is not None:
            # O_EXCL es la comprobación DE VERDAD (la de arriba es para dar un motivo legible):
            # cierra la carrera entre mirar y abrir, y nunca sigue un symlink. 0600 porque el
            # original suele serlo y una copia clínica no debe nacer con más permisos.
            try:
                fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except OSError as e:
                _log(agent, "RECHAZADO-destino-no-escribible", "%s -> %s" % (path, dest))
                print("RECHAZADO: no puedo crear %s (%s)." % (dest, e.strerror), file=sys.stderr)
                return 1
            try:
                with os.fdopen(fd, "wb") as sal:
                    _vuelca(f, sal, cabecera)
            except Exception:
                os.unlink(dest)          # media copia de un informe es peor que ninguna
                raise
            _log(agent, "COPIADO", "%s -> %s" % (path, dest))
            print("%d bytes copiados intactos en %s" % (os.path.getsize(dest), dest),
                  file=sys.stderr)
            return 0
        if no_es_texto_utf8(cabecera) and not binario:
            _log(agent, "RECHAZADO-binario-sin-flag", path)
            if cabecera.startswith(b"%PDF"):
                print("RECHAZADO: %s es un PDF. Lo que casi siempre quieres es `--texto` (su "
                      "capa de texto, marcada como derivación). Si necesitas el original byte "
                      "a byte: `--a <destino en zona clínica>`, o `--binario` para tubearlo."
                      % path, file=sys.stderr)
                return 1
            print("RECHAZADO: %s NO es texto UTF-8 — o es binario (PDF escaneado, DICOM, "
                  "imagen) o viene en otra codificación (latin-1). En ninguno de los dos casos "
                  "se vuelca a stdout por accidente: eso envenena el contexto del agente. Usa "
                  "`--a <destino en zona clínica>` para copiarlo intacto y trabajarlo en disco, "
                  "o `--binario` si lo vas a tubear a un programa (pdftoppm, pdftotext…)."
                  % path, file=sys.stderr)
            return 1
        resto = f.read()                          # el fichero ya cupo en memoria para el chequeo utf8
        # ── ¿de quién es este informe? (norma `feedback-verificar-identidad-paciente-en-informe`,
        # rescatada 24-sep-26 de norma-identidad-paciente). Hasta aquí solo se comprobaba QUE la
        # ruta es zona clínica, nunca DE QUIÉN es el documento — y esa carpeta no contiene solo
        # informes suyos (el caso de su padre, PDFs de terceros que entran por correo). La única
        # señal fiable está DENTRO: la filiación. La ruta no lo es.
        ok, veredicto = _identidad_ok(agent, path, (cabecera + resto).decode("utf-8", errors="ignore"))
        if not ok:
            return 1
        _log(agent, "LEIDO-binario" if binario else "LEIDO-%s" % veredicto, path)
        sys.stdout.buffer.write(cabecera)
        sys.stdout.buffer.write(resto)
        sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
