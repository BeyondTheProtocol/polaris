#!/usr/bin/env python3
"""tools/ci_barrido.py — el muro en la PUERTA DE ENTRADA: lo que llega por PR al repo público.

POR QUÉ (19-sep-2026). `publicar.py` vigila lo que SALE. Nada vigilaba lo que ENTRA: un PR
puede traer una clave, un teléfono, un alelo HLA o un informe pegado en un fixture, y eso
acaba en un repo público con el nombre del proyecto encima. Esto corre en GitHub Actions
sobre el diff del PR y lo bloquea antes de que nadie lo mezcle.

QUÉ BUSCA (categorías, no la lista privada: el repo público no puede llevar el léxico del
caso — sería publicar la ficha que intenta proteger):
  · claves y tokens (sk-…, ghp_…, AKIA…, claves privadas, App Passwords de Google);
  · contacto personal: email, teléfono español, DNI/NIE, NHC;
  · dato clínico crudo: alelo HLA, contenido de VCF, cadena larga de bases, variante HGVS;
  · rutas que no deberían existir aquí: 00_FUENTE-DE-VERDAD/, *.local.json, PDFs, .db.

Lo que NO busca: vocabulario del dominio («oncología», «metástasis», nombres de genes). En
ESTE repo son el material de trabajo; vetarlos sería ruido que enseña a ignorar el CI.

Uso:
  python3 tools/ci_barrido.py --diff origin/master...HEAD   # lo que trae el PR
  python3 tools/ci_barrido.py fichero1 fichero2             # ficheros sueltos
  python3 tools/ci_barrido.py --todo                        # todo el árbol versionado
  python3 tools/ci_barrido.py --selftest
"""
import argparse
import io
import os
import re
import subprocess
import sys

BINARIO = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".gz",
           ".woff", ".woff2", ".ttf", ".otf", ".db", ".npy", ".sqlite", ".sqlite3")

# (clave, regex, qué es). El orden no importa: se reportan todos los hallazgos del fichero.
REGLAS = (
    ("clave_api", re.compile(r"\b(?:sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"
                             r"|xox[baprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,})"),
     "parece una clave o token"),
    ("clave_privada", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----"),
     "clave privada"),
    # Una App Password de Google son cuatro grupos de cuatro letras. Sin contexto eso casa
    # con cuatro palabras españolas seguidas ("hace seis años ya"), así que se exige la pista.
    ("app_password", re.compile(r"(?:app\s*password|contrase\w+\s+de\s+aplicaci)\w*[\s:=\"']+"
                                r"[a-z]{4}\s[a-z]{4}\s[a-z]{4}\s[a-z]{4}\b", re.I),
     "App Password de Google"),
    ("email", re.compile(r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]{2,}@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
     "correo personal"),
    # Con separadores o con +34. Nueve dígitos seguidos sin más casan con constantes de
    # código (978307200 es el epoch de Core Data), así que esos no cuentan.
    ("telefono", re.compile(r"(?<!\d)(?:\+34[\s.-]?\d{3}(?:[\s.-]?\d{2}){3}"
                            r"|[6-9]\d{2}(?:[\s.-]\d{2}){3})(?!\d)"),
     "teléfono español"),
    ("dni", re.compile(r"\b\d{8}[- ]?[A-HJ-NP-TV-Z]\b|\b[XYZ]\d{7}[- ]?[A-HJ-NP-TV-Z]\b"),
     "DNI o NIE"),
    ("nhc", re.compile(r"\bNHC\b[\s:]*\d{4,}", re.I), "número de historia clínica"),
    ("hla", re.compile(r"\bHLA-[ABC]\*\d{2}:\d{2}", re.I), "alelo HLA"),
    ("vcf", re.compile(r"##fileformat=VCF", re.I), "contenido de un VCF"),
    ("secuencia", re.compile(r"\b[ACGT]{24,}\b"), "cadena larga de bases"),
    ("hgvs", re.compile(r"\b[cp]\.\d+[A-Za-z>_]"), "variante en notación HGVS"),
)

RUTAS_VETADAS = (
    (re.compile(r"(^|/)00_FUENTE-DE-VERDAD/"), "contenido privado del caso"),
    (re.compile(r"(^|/)_PRIVADO"), "carpeta privada"),
    (re.compile(r"\.local\.json$"), "overlay local (datos del titular)"),
    (re.compile(r"\.(pdf|db|sqlite3?|npy)$", re.I), "binario que no se versiona aquí"),
    (re.compile(r"(^|/)tools/state/"), "estado vivo del lazo"),
)

# El propio barrido escribe los patrones que busca: sin esto se denunciaría a sí mismo,
# que es la misma clase de fallo que dejó salir un término vetado el 17-sep.
# Sitios cuyo TRABAJO es contener estos patrones: el muro que los veta, los detectores, los
# datos de ejemplo sintéticos y los fixtures de evals. Si un PR toca uno de estos, lo mira una
# persona: el CI no puede distinguir un HLA inventado de uno real, y fingir que sí es peor.
EXENTOS_PAT = (
    # Detectores: llevan dentro los ejemplos que buscan (un HGVS de manual, un teléfono de
    # muestra, un alelo de ejemplo). Denunciarlos es denunciar al vigilante.
    re.compile(r"^tools/(ci_barrido|_lexico_publico|deid|borde|cascada_clinica"
               r"|audit_constelacion|pipeline_vacuna)\.py$"),
    # tests/ entero: su material son PII y datos clínicos INVENTADOS (un DNI de pega, un
    # HLA falso, un correo `a@b.com`). Vetarlos ahí sería 60 falsos positivos y un CI que
    # nadie mira. Un PR que toque tests/ lo revisa una persona.
    re.compile(r"^tests/"),
    re.compile(r"^evals/"),
    # Los ficheros legales llevan por definición una dirección de contacto: para eso existen.
    re.compile(r"^(SECURITY\.md|CODE_OF_CONDUCT\.md|CITATION\.cff|NOTICE)$"),
    re.compile(r"^pipeline/(data_ejemplo/|docs/|bin/(muro|preparar_reales|test_)\w*\.py$)"),
)


def _exento(rel):
    return any(p.search(rel) for p in EXENTOS_PAT)


def _ficheros_del_diff(rango):
    try:
        out = subprocess.check_output(["git", "diff", "--name-only", "--diff-filter=d", rango],
                                      text=True, stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        return []
    return [f for f in out.splitlines() if f.strip()]


def _versionados():
    return subprocess.check_output(["git", "ls-files"], text=True).splitlines()


# Correos que no son de nadie: buzones institucionales, el placeholder que deja el
# despersonalizador, y los dominios de juguete de los ejemplos.
LOCAL_OK = {"noreply", "no-reply", "contacto", "contact", "info", "hola", "support",
            "security", "abuse", "press", "prensa", "admin", "hello", "team", "help",
            "titular", "usuario", "alguien", "tu"}
DOM_OK = re.compile(r"(?:^|\.)(?:example|ejemplo|test|invalid|localhost|dominio)\."
                    r"|^[a-z]\.[a-z]{2,4}$"
                    r"|\.local$"                       # servicios locales (anatomia-push@btp.local)
                    r"|^(?:github|linkedin)\.com$",     # buzones automáticos de plataformas
                    re.I)


def _email_de_nadie(addr):
    local, _, dom = addr.partition("@")
    local = local.lower()
    if local in LOCAL_OK or local.endswith("team") or any(
            local.endswith("." + x) or local.startswith(x + "-") or
            local.startswith(x + ".") for x in LOCAL_OK):
        return True
    if "noreply" in local or "no-reply" in local:
        return True
    return bool(DOM_OK.search(dom))


def revisar_texto(texto):
    """Devuelve [(clave, qué es, fragmento)] de lo que no debería estar."""
    out = []
    for clave, patron, que in REGLAS:
        for m in patron.finditer(texto):
            frag = m.group(0)
            if clave == "email" and _email_de_nadie(frag):
                continue
            out.append((clave, que, frag[:6] + "…" if len(frag) > 8 else frag))
            break
    return out


def revisar_ruta(rel):
    return [("ruta", que, rel) for patron, que in RUTAS_VETADAS if patron.search(rel)]


def revisar(ficheros):
    hallazgos = []
    for rel in ficheros:
        if _exento(rel):
            continue
        hallazgos += [(rel,) + h for h in revisar_ruta(rel)]
        if rel.lower().endswith(BINARIO) or not os.path.exists(rel):
            continue
        try:
            with io.open(rel, encoding="utf-8") as fh:
                texto = fh.read()
        except (UnicodeDecodeError, OSError):
            continue
        hallazgos += [(rel,) + h for h in revisar_texto(texto)]
    return hallazgos


def selftest():
    casos = [
        ("clave_api", "token = 'ghp_abcdefghijklmnopqrstuvwxyz0123'"),
        ("email", "escribe a juan.perez@gmail.com"),
        ("telefono", "llama al 612 34 56 78"),
        ("dni", "DNI 12345678Z"),
        ("hla", "HLA-A*02:01 del paciente"),
        ("vcf", "##fileformat=VCFv4.2"),
        ("secuencia", "ACGTACGTACGTACGTACGTACGTACGT"),
        ("hgvs", "la variante c.1521_1523delCTT"),
    ]
    fallos = 0
    for clave, texto in casos:
        claves = [h[0] for h in revisar_texto(texto)]
        if clave not in claves:
            print("✗ no detecta %s en %r" % (clave, texto)); fallos += 1
    limpios = ("un comentario normal sobre oncología y metástasis",
               "FGFR4 y {{DIANA2}} son dianas", "correo a alguien@example.com",
               "version = '1.0.0' y puerto 8080",
               "EPOCH = 978307200  # Core Data epoch",      # no es un teléfono
               "el placeholder titular@gmail.com",           # lo deja el despersonalizador
               "en los tests se usa a@b.com",
               "el buzon del proyecto beyondtheprotocolteam@gmail.com",
               "remoto git@github.com", "aviso inmail-hit-reply@linkedin.com",
               "servicio anatomia-push@btp.local")
    for texto in limpios:
        if revisar_texto(texto):
            print("✗ falso positivo en %r → %s" % (texto, revisar_texto(texto))); fallos += 1
    if revisar_ruta("00_FUENTE-DE-VERDAD/informe.md") == []:
        print("✗ no veta una ruta privada"); fallos += 1
    print("selftest: %d fallos" % fallos)
    return 1 if fallos else 0


def main(argv=None):
    p = argparse.ArgumentParser(description="Barrido de lo que ENTRA al repo público")
    p.add_argument("ficheros", nargs="*")
    p.add_argument("--diff", help="rango git (p.ej. origin/master...HEAD)")
    p.add_argument("--todo", action="store_true", help="todo el árbol versionado")
    p.add_argument("--selftest", action="store_true")
    a = p.parse_args(argv)

    if a.selftest:
        return selftest()
    ficheros = a.ficheros or (_ficheros_del_diff(a.diff) if a.diff else (_versionados() if a.todo else []))
    if not ficheros:
        print("nada que revisar")
        return 0

    hallazgos = revisar(ficheros)
    if not hallazgos:
        print("✅ barrido limpio: %d fichero(s)" % len(ficheros))
        return 0
    print("🔴 %d hallazgo(s): esto no puede entrar al repo público\n" % len(hallazgos))
    for rel, clave, que, frag in hallazgos:
        print("   %s → %s (%s): %s" % (rel, que, clave, frag))
    print("\nSi es un falso positivo, dilo en el PR: lo revisa una persona, no se salta el CI.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
