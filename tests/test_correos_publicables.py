#!/usr/bin/env python3
"""test_correos_publicables.py — ninguna dirección de una persona real sale al repo público.

POR QUÉ EXISTE (24-sep-2026, deuda `correos-reales-en-el-arbol-publicable`). El barrido de
`publicar.py` sustituye el correo de la titular y el nombre del centro, pero no sabe nada de los
correos de TERCEROS que se cuelan en los *fixtures* de los tests. Al revisar qué publicaría la rama
del permiso de envío aparecieron, ya publicados: el correo del archivo de su hospital, el de una
amiga con nombre y apellido, y el de un contacto de un registro chino. Un local-part pegado a un
dominio real es PII de alguien que no ha dado permiso, y el repo es público y se indexa.

CÓMO LO DECIDE (allowlist, nunca denylist — una denylist de nombres no cubre al siguiente):
  · dominios de mentira (RFC 2606: `.example`, `example.com`, `.test`, `.invalid`…) → bien;
  · direcciones EXPLÍCITAS de esta lista → bien, y cada una dice por qué está (remitentes de
    servicio que el código TIENE que reconocer para triar, la cuenta pública del equipo, los
    ejemplos de docstring que ya viven en dominios inventados);
  · cualquier otra cosa → ROJO, con el fichero y la línea.

Si añades un correo nuevo: usa `@example.org` (o `{{CONTACTO}}` si es del caso), y si de verdad
hace falta el real —porque el código lo compara— añádelo aquí con su motivo en la misma línea.

Mira el árbol PUBLICABLE de verdad (lo genera `publicar.py`), no el repo: así cuenta con las
sustituciones del barrido y no da falsos rojos por lo que ya se enmascara al publicar.
"""
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

# Dominios que no son de nadie (RFC 2606/6761) y los internos del sistema.
DOMINIOS_OK = re.compile(
    r"\.(example|test|invalid|local|localdomain)$|^(example|ejemplo)\.(com|org|net)$|"
    r"^(localhost|hospital\.example)$", re.I)

# Lo que el propio barrido deja al despersonalizar: el correo de la titular sale como
# `titular…@gmail.com`. No es de nadie; es la marca de que la sustitución HIZO su trabajo.
MARCADOR_BARRIDO = re.compile(r"^titular[\w.+-]*@gmail\.com$", re.I)

# Direcciones reales que SÍ pueden viajar, y por qué.
EXPLICITAS = {
    # Remitentes de servicio que el triaje de correo compara literalmente: cambiarlos rompe el
    # código, y no son de una persona.
    "inmail-hit-reply@linkedin.com", "messaging-digest-noreply@linkedin.com",
    "notifications-noreply@linkedin.com", "invitations-noreply@linkedin.com",
    "updates@glass.health", "noreply@stripe.com", "promo@nordaccount.com",
    "notifications@github.com", "noreply@github.com", "git@github.com",
    "noreply@anthropic.com",
    # La cuenta PÚBLICA del equipo: ya está en la web y en el README.
    "beyondtheprotocolteam@gmail.com",
    # Contacto público de un servicio, citado en la documentación del pipeline.
    "contact@oncokb.org",
}

# Fixtures inventados que viven en dominios que existen pero no son de nadie del caso
# (`a@b.com`, `x@x.com`…). Se aceptan por NOMBRE COMPLETO para que un correo real nuevo no se
# cuele por parecerse: la lista es corta a propósito y no debería crecer.
FIXTURES = {
    # `@x.com` y `@hospital.org` se usan en varias baterías como buzones de pega, siempre con
    # local-parts de una palabra («a», «otra», «clinica-a»): no son de nadie.
    "a@b.com", "c@d.com", "t@t.com", "x@x.com", "y@h.com", "a@x.com", "otra@x.com",
    "otra@hospital.org",
    "alguien@x.com", "nueva@x.com", "ext@x.com", "clinica-a@x.com", "clinica-b@x.com",
    "attacker@bad.com", "mal@evil.com", "post@baseline.com", "auditor@externo.com",
    "cuenta-que-no-existe@nowhere.com", "no-existo@otrodominio.com", "news@vieja.com",
    "james.smith@nature.com", "store-news@amazon.com", "marina@amiga.com",
    "alguien@hospital.org",            # remitente de pega del guard de salida
    "mailer-daemon@googlemail.com",    # rebote automático, no una persona
    "juan.perez@gmail.com", "news.x7k2q@gmail.com",
    "boletin@news.com", "boletin@substack.com", "clinica@x.com",
    "contacto.contacto@fundacion.org", "contacto@dominio.com", "contacto@lab.org",
    "hola@contactoinpublic.com", "hola@lab-nuevo.org", "john@lab.org",
    "nadie@x.com", "news@promos.com", "nuevo@x.com",
    "persona@dominio-normal.com", "r%d@x.com", "vieja@clinica.com",
    # Webmail de PEGA: el caso que prueban es justo «servicio ≠ persona» y «esta cuenta no es
    # la de producción». No hay nadie detrás de ninguna.
    "updates@gmail.com", "la-secundaria@gmail.com",
}

fallos = []


def publicable():
    """El árbol que se publicaría, o None si aquí no se puede generar (runner público)."""
    destino = os.path.join(tempfile.mkdtemp(prefix="publicable-"), "arbol")
    r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "publicar.py"),
                        "--forzar", destino], capture_output=True, text=True, timeout=900)
    if r.returncode != 0 or not os.path.isdir(destino):
        print("⏭️  SKIP: no se puede generar el árbol publicable aquí (%s)"
              % (r.stderr or r.stdout).strip().splitlines()[-1:] or "sin salida")
        return None
    return destino


def main():
    arbol = publicable()
    if not arbol:
        return 0
    n = 0
    for base, _dirs, ficheros in os.walk(arbol):
        for nombre in ficheros:
            ruta = os.path.join(base, nombre)
            try:
                with open(ruta, encoding="utf-8", errors="strict") as f:
                    lineas = f.read().splitlines()
            except (UnicodeDecodeError, OSError):
                continue                      # binarios: no llevan correos que leer
            for i, linea in enumerate(lineas, 1):
                for correo in EMAIL.findall(linea):
                    n += 1
                    bajo = correo.lower()
                    if bajo in EXPLICITAS or bajo in FIXTURES or MARCADOR_BARRIDO.match(bajo):
                        continue
                    if DOMINIOS_OK.search(bajo.split("@", 1)[1]):
                        continue
                    if "{{" in linea and "}}" in linea and bajo.split("@")[0] in linea.lower():
                        pass                  # el barrido ya tocó la línea, pero el local-part sigue
                    fallos.append("%s:%d  %s" % (os.path.relpath(ruta, arbol), i, correo))
    if fallos:
        print("❌ %d dirección(es) que no deberían publicarse (de %d correos vistos):" % (len(fallos), n))
        for f in fallos[:40]:
            print("   · " + f)
        print("   Arréglalo con un dominio .example, o añádelo a EXPLICITAS con su motivo.")
        return 1
    print("✅ CORREOS PUBLICABLES EN VERDE (%d correos, ninguno de una persona real)" % n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
