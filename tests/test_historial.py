#!/usr/bin/env python3
"""test_historial.py — el historial médico no pierde páginas ni clasifica dos veces.

Cada check nació de un fallo real encontrado al meter el paquete de Vall d'Hebron:
  · `pet.?[ct]` casaba con «PETICión» y mandaba a Imagen cualquier informe de laboratorio.
  · `splitext` tomaba « Barberi)» por extensión en «(Dra. Barberi)» y truncaba el nombre.
  · La fecha se cogía «la primera que apareciera», así que una biopsia de 2024 citada
    dentro de una visita de 2026 fechaba la visita en 2024.
  · Un curso clínico cita biopsia, PET y molecular en la misma página: clasificarlo por
    palabras sueltas lo mandaba a una carpeta distinta según la página.
  · Los labs escriben `14/7/26`, que no casaba con `\\d{2}` en los tres campos.
Y el invariante que sostiene todo: partir NO puede perder ni duplicar una página.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import historial as h   # noqa: E402

_pass = 0
_fail = 0


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


# ── clasificación: determinista y con la cabecera mandando ────────────────
check("un curso clínico es consulta aunque cite biopsia, PET y mutaciones",
      h.clasificar("Curs clínic\nOncologia\nvaloramos la biopsia ósea, el PET-TC y "
                   "el {{TEST_MOL}} con mutaciones") == "consulta")
check("«petición» NO manda un laboratorio a Imagen",
      h.clasificar("Laboratoris Clínics Vall d'Hebron\nNúmero de petició del laboratori: "
                   "133527621\nHemograma") == "laboratorio")
check("anatomía patológica va a patología, no a laboratorio",
      h.clasificar("Servei d'Anatomia Patològica\nINFORME ANATOMIA PATOLÒGICA") == "patologia")
check("{{TEST_MOL}} va a molecular",
      h.clasificar("{{TEST_MOL}} - MUTATION TEST RESULTS\nCopy number alterations") == "molecular")
check("un PET-TC de verdad sí va a imagen",
      h.clasificar("CETIR CENTRE MÈDIC\nEXPLORACIÓN: PETTC COS SENC.FDG") == "imagen")
check("un consentimiento va a trámites",
      h.clasificar("Consentimiento informado\nFirma y DNI del paciente") == "tramite")
check("lo que no se reconoce NO cae en una carpeta clínica",
      h.clasificar("texto sin señal alguna de nada") == "tramite")

# ── fechas ───────────────────────────────────────────────────────────────
check("no fecha por la fecha de nacimiento",
      h.detecta_fecha("Data Naixement 03.04.1977") == "")
check("coge la del campo, no la primera que aparezca",
      h.detecta_fecha("biopsia de 16/01/2024 comentada\nData i hora 29.07.2026 11:02")
      == "2026-07-29")
check("entiende el formato corto de los labs (14/7/26)",
      h.detecta_fecha("Data d'obtenció de les mostres: 14/7/26") == "2026-07-14")
check("sin fecha devuelve vacío, no inventa", h.detecta_fecha("sin fechas aquí") == "")

# ── nombres ──────────────────────────────────────────────────────────────
n = h.nombre_documento("2026-07-29", "HUVH", "Curso clínico oncología médica (Dra. Barberi)")
check("no trunca en el punto de «Dra.»", n.endswith("(Dra. Barberi)"))
check("el nombre lleva fecha y centro", n.startswith("2026-07-29 - HUVH - "))
check("la categoría NO va en el nombre (la da la carpeta)",
      not any(c in n for c in ("Consulta -", "Lab -", "Imagen -")))
# El hueco se NOMBRA. Antes el centro desconocido salía como `?`, que el saneado del nombre
# convertía en `_`, y un guion bajo en medio de un nombre no se distingue de un nombre bueno:
# el hueco quedaba invisible justo donde hay que verlo. `sin-centro` se busca con un grep,
# igual que `sin-fecha`.
check("lo que falta se dice: sin-fecha y sin-centro, no '?' ni '_'",
      h.nombre_documento("", "?", "algo") == "sin-fecha - sin-centro - algo")
check("centro vacío también se nombra",
      h.nombre_documento("2026-01-01", "", "algo") == "2026-01-01 - sin-centro - algo")
check("saca los separadores de ruta",
      "/" not in h.nombre_documento("2026-01-01", "X", "a/b") )

# ── centros ──────────────────────────────────────────────────────────────
check("CETIR gana a Vall d'Hebron cuando emite CETIR",
      h.detecta_centro("CETIR CENTRE MÈDIC\nHOSPITAL: FUNDACIO ... VALL D'HEBRON") == "CETIR")
check("centro desconocido es '?', no un centro cualquiera",
      h.detecta_centro("Hospital de ningún sitio") == "?")
check("todo centro del catálogo tiene nombre completo",
      all(c in h.CENTROS for c in ("HUVH", "{{CENTRO}}", "CETIR", "MDA")))

# ── partir: el invariante que de verdad importa ──────────────────────────
class _FalsoPDF:
    """Evita depender de un PDF real: solo se comprueba la aritmética de cobertura."""


_orig = h.n_paginas
h.n_paginas = lambda pdf: 10
try:
    r = h.partir("x.pdf", [{"desde": 1, "hasta": 5, "carpeta": "imagen", "centro": "X",
                            "fecha": "2026-01-01", "descripcion": "a"}], apply=False)
    check("detecta páginas sin asignar", r["faltan"] == list(range(6, 11)))
    check("no escribe nada si falta cobertura", r["escritos"] == 0)

    r = h.partir("x.pdf", [{"desde": 1, "hasta": 6, "carpeta": "imagen", "centro": "X",
                            "fecha": "2026-01-01", "descripcion": "a"},
                           {"desde": 5, "hasta": 10, "carpeta": "imagen", "centro": "X",
                            "fecha": "2026-01-02", "descripcion": "b"}], apply=False)
    check("detecta páginas en dos documentos", r["duplicadas"] == [5, 6])

    r = h.partir("x.pdf", [{"desde": 1, "hasta": 4, "carpeta": "imagen", "centro": "X",
                            "fecha": "2026-01-01", "descripcion": "a"},
                           {"desde": 5, "hasta": 10, "carpeta": "molecular", "centro": "X",
                            "fecha": "2026-01-02", "descripcion": "b"}], apply=False)
    check("cobertura exacta no da error", not r["faltan"] and not r["duplicadas"])
    check("dry-run no escribe", r["escritos"] == 0)
    check("cada documento va a la carpeta de su clave",
          r["documentos"][1]["carpeta"] == h.CLAVE_A_CARPETA["molecular"])
finally:
    h.n_paginas = _orig

# ── estructura ───────────────────────────────────────────────────────────
check("las carpetas van numeradas y en orden",
      [c for _k, c in h.CARPETAS] == sorted(c for _k, c in h.CARPETAS))
check("molecular va antes que laboratorio (se abre más)",
      [k for k, _c in h.CARPETAS].index("molecular")
      < [k for k, _c in h.CARPETAS].index("laboratorio"))
check("toda señal tiene su carpeta",
      all(k in h.CLAVE_A_CARPETA for k, _p in h.SENALES))

# ── ingerir: el corpus de Murcia ─────────────────────────────────────────
# Los 335 informes del portal de Murcia y del volcado de Drive no traen membrete de
# servicio: traen una REJILLA de campos administrativos. Cada check de aquí abajo es un
# documento que se archivaba mal antes de mirar esa rejilla.
check("un alta de hospitalización va a Hospitalización, no a patología por citar la biopsia",
      h.clasificar("N.H.C. 999001 Servicio HEMATOLOGIA Nº Cama I220-2 F. Ingreso 12/01/2024 "
                   "F. Alta 19/01/2024 ... se comenta la biopsia y los receptores hormonales")
      == "ingreso")
check("una consulta externa se reconoce por la hora de cita",
      h.clasificar("N.H.C. 999001 HOSPITAL DE DIA F. Consulta 23/01/2024 H. Cita 08:24")
      == "consulta")
check("un laboratorio con «Servicio Remitente: URGENCIAS» sigue siendo laboratorio",
      h.clasificar("HISTORICO Datos Petición. LOS RESULTADOS SIN FIRMA FACULTATIVA SON "
                   "PROVISIONALES ... Servicio Remitente URGENCIAS") == "laboratorio")
check("el informe de enfermería al alta es de enfermería, no del ingreso",
      h.clasificar("INFORME DE CUIDADOS DE ENFERMERIA AL ALTA F. Alta 19/01/2024") == "soporte")
check("el anatomopatológico se anuncia solo",
      h.clasificar("INFORME ANATOMOPATOLÓGICO Biopsia: 24B0001043") == "patologia")
check("un ECG no cae en el cajón de trámites",
      h.clasificar("ECG ritmo sinusal FC 90 bpm QTc 445 ms") == "imagen")

check("el alta se fecha el día del alta, no el del ingreso",
      h.detecta_fecha("F. Ingreso 12/01/2024 21:22 F. Alta 19/01/2024 10:00") == "2024-01-19")
check("un informe suizo se fecha por su salida (Ausgang), no por la entrada de la muestra",
      h.detecta_fecha("Entnahme: . . Eingang: 20.07.2026 Ausgang: 19.08.2026") == "2026-08-19")

check("la dirección del membrete identifica el centro aunque venga partida en dos columnas",
      h.detecta_centro("c/ marques de los telefono 619718173 velez, s/n pag 1 de 6") == "HMM")
check("el hospital nombrado gana a la dirección",
      h.detecta_centro("Hospital ... Virgen de la {{CENTRO}} ... C/ Marqués de los Vélez")
      == "{{CENTRO}}")
check("sin hospital pero con tarjeta CARM, el emisor es el servicio murciano, no un invento",
      h.detecta_centro("INFORME RADIODIAGNOSTICO cip aut.: carm258210141414") == "SMS")
check("el USZ gana al {{CENTRO}} en el molecular que firma el USZ",
      h.detecta_centro("{{CENTRO}} {{CENTRO}} ... www.pathologie.usz.ch "
                       "Institut für Pathologie und Molekularpathologie") == "USZ")
check("un centro que solo está en el nombre del fichero se rescata de ahí",
      h.detecta_centro_en_nombre("2026-01-24 - Consulta - OnMyMeal - Primera consulta")
      == "OnMyMeal")
check("todo alias de nombre apunta a un centro del catálogo",
      all(v in h.CENTROS for v in h.ALIAS_EN_NOMBRE.values()))

check("el índice del portal no ensucia la descripción",
      h.limpia_descripcion("19_REH - INFORME CONSULTAS EXTERNAS") == "REH - INFORME CONSULTAS EXTERNAS")
check("un nombre que es solo un número de petición se cambia por lo que el documento ES",
      h.limpia_descripcion("20-377541665", "laboratorio") == "Analítica")
check("una descripción de verdad se respeta",
      h.limpia_descripcion("Ecocardiograma", "imagen") == "Ecocardiograma")

check("del nombre viejo se rescata la fecha y la descripción, nunca la categoría",
      h._del_nombre("2024-01-16 - Imagen - Ecografía de mama")
      == ("2024-01-16", "Ecografía de mama", ""))
check("si el trozo del medio ya es un centro, se cree",
      h._del_nombre("2026-07-29 - HUVH - Analítica")[2] == "HUVH")
check("el sello de fecha pegado al final también vale",
      h._del_nombre("DIPCAN19715_Bioquimica_20240722113201")[0] == "2024-07-22")
check("un nombre que no dice nada no inventa fecha",
      h._del_nombre("informe_final") == ("", "", ""))

check("ingerir no toca un byte sin --apply",
      h.ingerir([os.path.dirname(os.path.abspath(__file__))], apply=False)["escritos"] == 0)
check("ingerir solo mira PDFs",
      all(p.lower().endswith(".pdf")
          for p in [d["origen"] for d in
                    h.ingerir([os.path.dirname(os.path.abspath(__file__))],
                              apply=False)["documentos"]]))

# ── subir: saber qué hay YA en Drive (bug del 11-sep-2026) ─────────────────
# `subir` pasaba «'ID' in parents» a `drive.py list`, que lo envolvía en `name contains` →
# listado siempre vacío → daba por pendientes los ~900 ficheros y los habría duplicado.
import unicodedata as _u   # noqa: E402
_nfd = _u.normalize("NFD", "2024-03-11 - SMS - INFORME ANATOMÍA PATOLÓGICA.pdf")
_nfc = _u.normalize("NFC", _nfd)
check("subir: lo que ya está en Drive no se da por pendiente aunque difiera NFC/NFD",
      h.pendientes_de_subir([_nfd], [_nfc]) == [])
check("subir: lo que falta arriba sí sale pendiente",
      h.pendientes_de_subir(["a.pdf", "b.pdf"], ["a.pdf"]) == ["b.pdf"])
check("subir: nombre exacto, no subcadena",
      h.pendientes_de_subir(["X.pdf"], ["X.pdf.bak"]) == ["X.pdf"])
fuente = open(os.path.join(ROOT, "tools", "historial.py"), encoding="utf-8").read()
check("subir: lista la carpeta con --folder, no con una query que acaba en name contains",
      '"--folder", fid' in fuente and "'%s' in parents\" % fid" not in fuente)
_drive_src = open(os.path.join(ROOT, "tools", "drive.py"), encoding="utf-8").read()
check("drive.py list --folder pagina (list_files se para en 50)",
      "def list_folder" in _drive_src and "nextPageToken" in _drive_src)

# ── muro ─────────────────────────────────────────────────────────────────
check("no importa smtplib", "import smtplib" not in fuente)
check("no hace red", not any(x in fuente for x in ("urllib.request", "requests.", "socket.")))

print("test_historial: %d ok, %d fallos" % (_pass, _fail))
sys.exit(1 if _fail else 0)
