#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""La ventanilla clínica sirve BINARIOS intactos — byte a byte, mismo sha256.

Cierra la deuda `lector-clinico-corrompe-pdf-binario` (verificada el 19-sep-2026 por el agente
`verificacion`): `tools/lector_clinico.py` servía TODO con
`open(…, encoding="utf-8", errors="ignore")` + `sys.stdout.write`, así que los bytes que no
decodifican se caían por el camino sin un solo aviso. Con 5 informes de imagen reales
(RECIST 8-sep, PET-FDG 8-sep, RM mama 8-sep, PET-Ga 26-may, PET-FDG 24-mar) pdftoppm
renderizaba páginas en blanco y tesseract devolvía 0 bytes: ningún original se podía cotejar
por el canal sancionado, y las notas que decían «verificado contra el original» se apoyaban en
transcripciones.

Es un test de MURO, así que comprueba las dos mitades: que el dato llega ENTERO, y que la
ventanilla sigue sin dejar escribir fuera de la zona clínica.

Campaña de mutantes (20-sep-26): quitando de una en una las defensas de `--a` (islink del
destino, realpath del padre, es_ruta_clinica, samefile/ya-existe, magias, saneado del log,
modo 0600) el test se pone ROJO en las siete. La única que sobrevive es cambiar `O_EXCL` por
`O_TRUNC`, y es correcto que sobreviva: O_EXCL es el respaldo contra la carrera entre mirar y
abrir, y la comprobación visible la hace el `lexists` de arriba — quitando ESE, el test cae.
Una carrera no se prueba con un test determinista; se deja la capa y se dice.

Todo con ficheros SINTÉTICOS bajo un `_PRIVADO_CLINICO/` de usar y tirar (familia A de
`zonas_clinicas`): cero dato de la paciente. `BTP_REPO` apunta al temporal para que el log de
accesos del test no toque el del sistema; la política se sigue cargando del árbol en el que
corre el test (el `_cargar_politica()` de la ventanilla cae al hermano de `tools/`), que es lo
que hace que desde un worktree se pruebe el código de ESA rama y no el de casa base.
"""
import hashlib
import io
import json
import os
import shutil
import subprocess
import time
import sys
import tempfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LECTOR = os.environ.get("BTP_LECTOR") or os.path.join(RAIZ, "tools", "lector_clinico.py")

fallos = []
total = [0]


def check(cond, desc):
    total[0] += 1
    print("  %s %s" % ("✅" if cond else "❌", desc))
    if not cond:
        fallos.append(desc)


def sha(b):
    return hashlib.sha256(b).hexdigest()


def corre(args, repo):
    p = subprocess.run([sys.executable, LECTOR] + args, capture_output=True, timeout=60,
                       env=dict(os.environ, BTP_REPO=repo, BTP_AGENT="test-binario"))
    return p.returncode, p.stdout, (p.stderr or b"").decode("utf-8", "replace")


def pdf_valido(texto=b"LESION HEPATICA 12 mm"):
    """Un PDF MÍNIMO pero válido (xref incluido) con capa de texto. Sin dependencias: si el test
    necesitara reportlab no correría en la batería, y un PDF de la paciente aquí no entra."""
    cont = b"BT /F1 12 Tf 20 100 Td (" + texto + b") Tj ET\n"
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Contents 4 0 R "
            b"/Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length " + str(len(cont)).encode() + b" >>\nstream\n" + cont + b"endstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offs = []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += str(i).encode() + b" 0 obj\n" + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 " + str(len(objs) + 1).encode() + b"\n0000000000 65535 f \n"
    for off in offs:
        out += ("%010d 00000 n \n" % off).encode()
    out += (b"trailer\n<< /Size " + str(len(objs) + 1).encode() + b" /Root 1 0 R >>\nstartxref\n"
            + str(xref).encode() + b"\n%%EOF\n")
    return bytes(out)


# ── PDF sintético: cabecera real + TODOS los valores de byte (NUL y 0x80-0xFF incluidos) ──
CUERPO = bytes(bytearray(range(256))) * 40
PDF = (b"%PDF-1.4\n1 0 obj\n<< /Length " + str(len(CUERPO)).encode() + b" >>\nstream\n"
       + CUERPO + b"\nendstream\nendobj\n%%EOF\n")

tmp = tempfile.mkdtemp(prefix="btp-ventanilla-")
try:
    zona = os.path.join(tmp, "_PRIVADO_CLINICO")
    fuera = os.path.join(tmp, "fuera")
    os.makedirs(zona)
    os.makedirs(fuera)
    # Overlay sintético del titular (norma feedback-verificar-identidad-paciente-en-informe,
    # rescatada 24-sep-26): sin `tools/perfil.local.json` la ventanilla ahora es fail-closed
    # (veredicto `sin_overlay`, NO se sirve nada) — correcto en producción, pero este test corre
    # con `BTP_REPO=tmp` y sin overlay real. Un titular de mentira, cero dato de la paciente,
    # para que los fixtures SIN filiación etiquetada caigan en `no_consta` (SÍ se sirve, con
    # aviso) en vez de `sin_overlay` (rechazo). Mismo patrón que test_identidad_paciente.py.
    os.makedirs(os.path.join(tmp, "tools"), exist_ok=True)
    with io.open(os.path.join(tmp, "tools", "perfil.local.json"), "w", encoding="utf-8") as fh:
        json.dump({"titular": {"nombre": "Prueba", "apellidos": ["Sintetica"],
                                "nacimiento": ["01/01/1900"]}}, fh)
    pdf = os.path.join(zona, "informe-sintetico.pdf")
    with io.open(pdf, "wb") as f:
        f.write(PDF)
    esperado = sha(PDF)

    # 0) El bug, nombrado: el camino viejo (utf-8 + errors=ignore) SÍ destruía el fichero.
    #    Si esto dejara de ser cierto, el test no estaría probando nada.
    viejo = io.open(pdf, encoding="utf-8", errors="ignore").read().encode("utf-8")
    check(sha(viejo) != esperado,
          "el camino viejo (utf-8/errors=ignore) corrompe el PDF (%d → %d bytes)"
          % (len(PDF), len(viejo)))

    # 1) Sin bandera: un binario NO se vuelca a stdout (ni corrupto ni entero).
    rc, out, err = corre([pdf], tmp)
    check(rc != 0 and not out and "RECHAZADO" in err and "--binario" in err and "--a" in err,
          "sin --binario/--a, un binario se RECHAZA (y el mensaje dice las dos salidas)")

    # 2) `--binario`: los bytes salen intactos por stdout (esto es lo que tubea a pdftoppm).
    rc, out, err = corre(["--binario", pdf], tmp)
    check(rc == 0 and sha(out) == esperado,
          "--binario → sha256(servido) == sha256(original) (%s)" % esperado[:12])

    # 3) `--a <destino en zona clínica>`: copia intacta en disco.
    destino = os.path.join(zona, "copia.pdf")
    rc, out, err = corre(["--a", destino, pdf], tmp)
    copiado = io.open(destino, "rb").read() if os.path.exists(destino) else b""
    check(rc == 0 and sha(copiado) == esperado,
          "--a (zona clínica) → el fichero en disco es idéntico al original")
    check(not out, "--a no escupe el contenido por stdout (el resumen va a stderr)")

    # 4) FAIL-CLOSED: el destino fuera de la zona clínica no se escribe. Esto es el muro.
    escape = os.path.join(fuera, "exfiltrado.pdf")
    rc, out, err = corre(["--a", escape, pdf], tmp)
    check(rc != 0 and not os.path.exists(escape) and "zona clínica" in err,
          "--a fuera de zona clínica → RECHAZADO por serlo, y no se crea nada")

    # 5) El original no se puede usar de destino (lo truncaría antes de leerlo).
    rc, out, err = corre(["--a", pdf, pdf], tmp)
    check(rc != 0 and sha(io.open(pdf, "rb").read()) == esperado,
          "--a sobre el propio original → RECHAZADO y el original sigue entero")

    # 5b) HARDLINK al original. `realpath` no lo ve (otra ruta, mismo inodo) y la copia
    #     truncaba el original clínico a 128 KiB con rc=0: la ventanilla que existe para poder
    #     cotejar originales era capaz de destruirlos (caja revision-contribuciones, 20-sep-26).
    hl = os.path.join(zona, "enlace-duro.pdf")
    os.link(pdf, hl)
    rc, out, err = corre(["--a", hl, pdf], tmp)
    check(rc != 0 and sha(io.open(pdf, "rb").read()) == esperado and "inodo" in err,
          "--a sobre un HARDLINK del original → RECHAZADO y el original sigue entero")
    os.unlink(hl)

    # 5c) Nada de sobrescribir dato clínico que ya está ahí.
    otro = os.path.join(zona, "informe-que-ya-estaba.md")
    with io.open(otro, "wb") as f:
        f.write(b"# dato que importa\n")
    rc, out, err = corre(["--a", otro, pdf], tmp)
    check(rc != 0 and io.open(otro, "rb").read() == b"# dato que importa\n" and "EXISTE" in err,
          "--a sobre un fichero que ya existe → RECHAZADO sin machacarlo")

    # 5d) Un directorio como destino: mensaje, no traceback (y con su línea en el log).
    rc, out, err = corre(["--a", zona, pdf], tmp)
    check(rc != 0 and "Traceback" not in err and "directorio" in err,
          "--a con un directorio de destino → RECHAZADO con mensaje, sin traceback")

    # 5e) SYMLINK como destino, apuntando fuera. Sin esta guarda el dato sale de la zona.
    salida_link = os.path.join(fuera, "por-el-enlace.pdf")
    enlace = os.path.join(zona, "enlace-blando.pdf")
    os.symlink(salida_link, enlace)
    rc, out, err = corre(["--a", enlace, pdf], tmp)
    check(rc != 0 and not os.path.exists(salida_link) and "symlink" in err,
          "--a a un symlink que sale de la zona → RECHAZADO y no se escribe fuera")
    os.unlink(enlace)

    # 5f) …y lo mismo con el DIRECTORIO PADRE enlazado hacia fuera (el tobogán de verdad:
    #     la ruta LÉXICA sigue teniendo el segmento clínico, así que el predicado solo la
    #     caza si se resuelve el padre ANTES de decidir).
    dirlink = os.path.join(zona, "subcarpeta")
    os.symlink(fuera, dirlink)
    colado = os.path.join(fuera, "colado.pdf")
    rc, out, err = corre(["--a", os.path.join(dirlink, "colado.pdf"), pdf], tmp)
    check(rc != 0 and not os.path.exists(colado),
          "--a con el directorio padre enlazado fuera → RECHAZADO y no se escribe fuera")
    os.unlink(dirlink)

    # 5g) La copia no nace con más permisos que el original.
    os.chmod(pdf, 0o600)
    d600 = os.path.join(zona, "copia-modo.pdf")
    rc, out, err = corre(["--a", d600, pdf], tmp)
    check(rc == 0 and (os.stat(d600).st_mode & 0o777) == 0o600,
          "la copia se crea en 0600, no heredando el umask")

    # 6) El texto no se rompe: acentos y multibyte salen byte a byte.
    texto = os.path.join(zona, "nota.md")
    CONTENIDO = u"# Informe\nRESPUESTA PARCIAL — lesión de 12 mm. Ratio κ/λ ✅\n".encode("utf-8")
    with io.open(texto, "wb") as f:
        f.write(CONTENIDO)
    rc, out, err = corre([texto], tmp)
    check(rc == 0 and out == CONTENIDO, "el modo texto sigue sirviendo el fichero completo")

    # 7) Un fichero latin-1 (lo que escupen algunos hospitales) tampoco pierde bytes.
    latin = os.path.join(zona, "nota-latin1.txt")
    L = u"Mama izquierda, adenopatía axilar\n".encode("latin-1")
    with io.open(latin, "wb") as f:
        f.write(L)
    rc, out, err = corre(["--binario", latin], tmp)
    check(rc == 0 and out == L, "latin-1 sale entero con --binario (antes perdía las tildes)")

    # 7b) Un PDF cuyos primeros 8 KiB son ASCII: la heurística de cabecera sola lo daba por
    #     texto y lo volcaba ENTERO a stdout. La magia `%PDF` lo caza antes.
    mixto = os.path.join(zona, "cabecera-ascii.pdf")
    with io.open(mixto, "wb") as f:
        f.write(b"%PDF-1.4\n" + b"A" * 9000 + b"\x00\xff" * 500)
    rc, out, err = corre([mixto], tmp)
    check(rc != 0 and not out, "un PDF con 8 KiB de cabecera ASCII NO se vuelca a stdout")

    # 7c) UTF-16 tampoco se sirve a ciegas, y con --binario sale entero.
    u16 = os.path.join(zona, "u16.txt")
    U = u"lesión hepática\n".encode("utf-16")
    with io.open(u16, "wb") as f:
        f.write(U)
    rc, out, err = corre([u16], tmp)
    rc2, out2, _ = corre(["--binario", u16], tmp)
    check(rc != 0 and rc2 == 0 and out2 == U, "UTF-16: fail-closed sin bandera, entero con ella")

    # 7d) El registro no se puede falsificar desde el argumento: un `\n` en el destino metía
    #     una línea ENTERA inventada en clinico-access.log.
    rc, out, err = corre(["--a", os.path.join(zona, "x\n2026-01-01 00:00:00\tfalso\tLEIDO\t/inventado"),
                          pdf], tmp)
    logp = os.path.join(tmp, ".claude", "logs", "clinico-access.log")
    crudo = io.open(logp, encoding="utf-8", errors="replace").read() if os.path.exists(logp) else ""
    check("falso\tLEIDO" not in crudo, "un destino con salto de línea no inyecta líneas en el log")

    # ── `--texto` (20-sep-26): la comodidad no puede romper el sello de evidencia ──────
    # 9a) PDF CON capa de texto: sale el texto, y sale MARCADO como derivación.
    conpdf = os.path.join(zona, "informe-con-capa.pdf")
    with io.open(conpdf, "wb") as f:
        f.write(pdf_valido())
    rc, out, err = corre(["--texto", conpdf], tmp)
    check(rc == 0 and b"LESION HEPATICA" in out and "NO el original" in err,
          "--texto sirve la capa del PDF y avisa de que NO es el original")

    # 9b) PDF ESCANEADO (sin capa): no se devuelve vacío como si fuera el informe.
    escpdf = os.path.join(zona, "escaneado.pdf")
    with io.open(escpdf, "wb") as f:
        f.write(pdf_valido(b" "))
    rc, out, err = corre(["--texto", escpdf], tmp)
    check(rc != 0 and not out.strip() and "escaneado" in err and "ocr_informes" in err,
          "PDF escaneado sin sidecar → RECHAZADO nombrando la ruta de OCR, no un vacío")

    # 9c) …y con sidecar OCR al día, se sirve el sidecar, también marcado.
    with io.open(escpdf + ".ocr.txt", "wb") as f:
        f.write(b"TEXTO OCREADO: adenopatia axilar\n")
    rc, out, err = corre(["--texto", escpdf], tmp)
    check(rc == 0 and b"adenopatia" in out and "sidecar OCR" in err,
          "con sidecar .ocr.txt al día, --texto lo sirve marcado como OCR")

    # 9c-bis) EL SIDECAR PASA POR LA MISMA PUERTA. Un symlink `x.pdf.ocr.txt -> fuera`
    #     colocado en la carpeta clínica convertía la ventanilla en lector de ficheros de fuera,
    #     y el log decía que se había leído el PDF (reproducido con canario el 20-sep-26).
    canario = os.path.join(fuera, "canario-fuera-de-la-zona.txt")
    with io.open(canario, "wb") as f:
        f.write(b"CANARIO-QUE-NO-DEBERIA-SALIR\n")
    os.utime(canario, (time.time() + 60, time.time() + 60))   # más nuevo que el original
    trampa = os.path.join(zona, "trampa.pdf")
    with io.open(trampa, "wb") as f:
        f.write(pdf_valido(b" "))
    os.symlink(canario, trampa + ".ocr.txt")
    rc, out, err = corre(["--texto", trampa], tmp)
    check(rc != 0 and b"CANARIO" not in out and "sidecar" in err,
          "un sidecar que es symlink hacia fuera → RECHAZADO, no sirve lo de fuera")

    # 9d) El rechazo de un PDF sin bandera enseña `--texto`, que es lo que casi siempre quiere.
    rc, out, err = corre([conpdf], tmp)
    check(rc != 0 and "--texto" in err, "un PDF sin bandera enseña --texto en el rechazo")

    # 9e) No se mezclan una derivación y el original en la misma llamada.
    rc, out, err = corre(["--texto", "--a", os.path.join(zona, "z.pdf"), conpdf], tmp)
    check(rc != 0 and "no se combina" in err, "--texto no se combina con --a ni --binario")

    # 9f) --texto sobre algo que no es PDF no se inventa nada.
    rc, out, err = corre(["--texto", texto], tmp)
    check(rc != 0 and "es para PDF" in err, "--texto sobre un .md → RECHAZADO")

    # 9g) El OCR es un procesador SANCIONADO: antes la ventanilla no lo servía (no es texto) y
    #     el guard bloqueaba correrlo por fuera — callejón sin salida para 3 informes escaneados
    #     (deuda `historial-pdf-sin-texto-ventanilla`). Carpeta vacía: solo se comprueba que la
    #     ventanilla lo LANZA y lo registra, sin depender de tesseract.
    vacia = os.path.join(zona, "para-ocr")
    os.makedirs(vacia)
    rc, out, err = corre(["procesa", "ocr_informes", "--", "--dir", vacia], tmp)
    check(rc == 0 and "no sancionado" not in err,
          "`procesa ocr_informes` está en la allowlist de la ventanilla")

    # ── `listar` (20-sep-26): la ventanilla ya sabe decir QUÉ hay, sin servir contenido ──
    # 10a) Encuentra por nombre, y sin tildes: se busca «anatomia» y aparece «Anatomía».
    hondo = os.path.join(zona, "sub", "mas-hondo")
    os.makedirs(hondo)
    diana = os.path.join(hondo, u"2024-02-28 - Anatomía patológica.pdf")
    with io.open(diana, "wb") as f:
        f.write(pdf_valido(b"CONTENIDO QUE NO DEBE SALIR"))
    rc, out, err = corre(["listar", "anatomia patologica", "--dir", zona], tmp)
    check(rc == 0 and diana.encode("utf-8") in out,
          "listar encuentra el fichero por nombre, sin tildes y en una subcarpeta")

    # 10b) Devuelve RUTAS, nunca CONTENIDO. Es la línea que separa listar de leer.
    check(b"CONTENIDO QUE NO DEBE SALIR" not in out,
          "listar NO sirve el contenido de lo que lista")

    # 10c) Una zona que no es clínica no se lista por aquí.
    rc, out, err = corre(["listar", "", "--dir", fuera], tmp)
    check(rc != 0 and not out and "no es zona clínica" in err,
          "listar --dir fuera de zona clínica → RECHAZADO")

    # 10d) Un enlace que sale de la zona ni se sigue ni se nombra. OJO al escribir este
    #      caso: el enlace tiene que casar ÉL MISMO con el patrón, o el test pasa por no
    #      encontrar nada y no prueba la defensa (me pasó: el mutante sin el filtro seguía
    #      en verde). El fichero de dentro con nombre parecido es el control: ese SÍ sale.
    secreto = os.path.join(fuera, "canario-de-fuera.txt")
    with io.open(secreto, "wb") as f:
        f.write(b"CANARIO\n")
    enlace = os.path.join(zona, "atajo-tentador.txt")
    os.symlink(secreto, enlace)
    control = os.path.join(zona, "atajo-legitimo.txt")
    with io.open(control, "wb") as f:
        f.write(b"esto si es de la zona\n")
    rc, out, err = corre(["listar", "atajo", "--dir", zona], tmp)
    check(rc == 0 and enlace.encode("utf-8") not in out and control.encode("utf-8") in out,
          "listar omite el symlink que sale de la zona y sigue nombrando lo que sí está")

    # 10d-bis) Un DIRECTORIO enlazado hacia fuera tampoco se recorre.
    os.symlink(fuera, os.path.join(zona, "atajo-dir"))
    rc, out, err = corre(["listar", "canario", "--dir", zona], tmp)
    check(rc == 0 and b"canario-de-fuera" not in out,
          "listar no entra en un directorio enlazado que sale de la zona")
    os.unlink(enlace)
    os.unlink(control)
    os.unlink(os.path.join(zona, "atajo-dir"))

    # 10e) El tope existe: listar 3.000 rutas al contexto de un agente es el mismo veneno
    #      que volcarle un PDF.
    rc, out, err = corre(["listar", "", "--dir", zona, "--max", "2"], tmp)
    check(rc == 0 and len(out.strip().split(b"\n")) == 2 and "cortado" in err,
          "--max corta y lo dice")

    # ── `retirar` (20-sep-26): sacar basura del archivo SIN destruir nada ──────────────
    # 11a) Mueve a `_RETIRADOS/` y el fichero sigue existiendo: se puede deshacer.
    basura = os.path.join(zona, "informe-vacio.pdf")
    with io.open(basura, "wb") as f:
        f.write(b"%PDF-1.4\n%%EOF\n")
    rc, out, err = corre(["retirar", basura, "--motivo", "PDF en blanco, 0 texto"], tmp)
    retirado = os.path.join(zona, "_RETIRADOS", "informe-vacio.pdf")
    check(rc == 0 and not os.path.exists(basura) and os.path.isfile(retirado),
          "retirar mueve a _RETIRADOS/ y NO destruye el fichero")

    # 11b) Queda escrito POR QUÉ, al lado. Una retirada sin razón no se puede revisar luego.
    nota = retirado + ".retirada.txt"
    txt_nota = io.open(nota, encoding="utf-8").read() if os.path.exists(nota) else ""
    check("PDF en blanco" in txt_nota and "test-binario" in txt_nota,
          "deja al lado el motivo, la fecha y quién lo retiró")

    # 11c) Sin motivo no se retira.
    otro = os.path.join(zona, "otro.pdf")
    with io.open(otro, "wb") as f:
        f.write(b"%PDF-1.4\n")
    rc, out, err = corre(["retirar", otro], tmp)
    check(rc != 0 and os.path.isfile(otro) and "motivo" in err,
          "sin --motivo no se retira nada")

    # 11d) Fuera de la zona clínica, ni tocarlo. Esta puerta mueve DENTRO del archivo.
    ajeno = os.path.join(fuera, "cosa-mia.txt")
    with io.open(ajeno, "wb") as f:
        f.write(b"no es clinico\n")
    rc, out, err = corre(["retirar", ajeno, "--motivo", "x"], tmp)
    check(rc != 0 and os.path.isfile(ajeno) and "zona clínica" in err,
          "retirar fuera de zona clínica → RECHAZADO y el fichero sigue ahí")

    # 11e) No machaca una retirada anterior del mismo nombre.
    with io.open(basura, "wb") as f:
        f.write(b"%PDF-1.4\nOTRO DISTINTO\n")
    rc, out, err = corre(["retirar", basura, "--motivo", "otra vez"], tmp)
    check(rc != 0 and os.path.isfile(basura)
          and io.open(retirado, "rb").read() == b"%PDF-1.4\n%%EOF\n",
          "no sobrescribe lo ya retirado con el mismo nombre")

    # 8) Registro: la ventanilla sigue dejando rastro de cada acceso.
    log = os.path.join(tmp, ".claude", "logs", "clinico-access.log")
    txt = io.open(log, encoding="utf-8").read() if os.path.exists(log) else ""
    check("LEIDO-binario" in txt and "COPIADO" in txt and "RECHAZADO-destino-no-clinico" in txt
          and "test-binario" in txt,
          "clinico-access.log registra lo servido Y lo rechazado, con el agente")
    check("LEIDO-texto-pdftotext" in txt and "LEIDO-texto-sidecar-ocr" in txt,
          "el log DISTINGUE haber leído una derivación de haber leído el original")
    check("PROCESA-ocr_informes" in txt, "el lanzamiento del OCR también deja su línea en el log")
    check("LISTADO-" in txt and "RECHAZADO-listar-fuera-de-allowlist" in txt,
          "listar también deja rastro: lo listado y lo rechazado")
    # El log es TSV: se casa el CAMPO, no la subcadena. «RETIRADO» a secas también casa dentro
    # de la ruta «_RETIRADOS/», así que el check pasaba aunque se quitara la línea del log
    # (lo cazó la campaña de mutantes, no la lectura del test). Segunda vez el mismo día.
    check("\tRETIRADO\t" in txt and "\tRECHAZADO-retirar-fuera-de-allowlist\t" in txt,
          "retirar deja rastro: lo movido y lo rechazado")

    # 9) Fuera del allowlist se sigue rechazando (la puerta no se abrió al arreglar la ventana).
    suelto = os.path.join(fuera, "cualquiera.pdf")
    with io.open(suelto, "wb") as f:
        f.write(PDF)
    rc, out, err = corre(["--binario", suelto], tmp)
    check(rc != 0 and not out and "no es zona clínica" in err,
          "un binario FUERA de zona clínica se sigue rechazando por serlo")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("%s %d/%d" % ("❌ FALLOS:" if fallos else "✅ ventanilla clínica: binarios intactos",
                    total[0] - len(fallos), total[0]))
sys.exit(1 if fallos else 0)
