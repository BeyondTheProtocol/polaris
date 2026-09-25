#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests del sistema de memoria: índice generado + guardián de límites.

Lo que protegen estos tests es una sola cosa: que **una regla escrita siga llegando
a la sesión**. Si el índice pierde una memoria, o si un fichero se pasa del corte del
cargador sin que nadie chille, la norma existe en disco pero ya no existe en la práctica.
"""
import os
import shutil
import sys
import tempfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "tools"))

fallos = []


def check(cond, etiqueta):
    if cond:
        print("  ✅ %s" % etiqueta)
    else:
        print("  ❌ %s" % etiqueta)
        fallos.append(etiqueta)


def _memoria(dirmem, slug, desc, cuerpo="cuerpo de la memoria"):
    with open(os.path.join(dirmem, slug + ".md"), "w", encoding="utf-8") as fh:
        fh.write("---\nname: %s\ndescription: \"%s\"\nmetadata:\n  type: feedback\n---\n\n%s\n"
                 % (slug, desc, cuerpo))


def test_indice():
    print("── índice de memoria ──")
    tmp = tempfile.mkdtemp(prefix="btp_mem_")
    try:
        os.environ["BTP_MEMORY_DIR"] = tmp
        import importlib
        import indice_memoria
        importlib.reload(indice_memoria)

        _memoria(tmp, "feedback-codigo-rojo", "parar todo si el goal peligra")
        _memoria(tmp, "feedback-no-tocar-copy-web-sin-ok", "el hero de la web no se toca sin OK")
        _memoria(tmp, "reference-algo-raro", "una referencia sin tema claro")
        _memoria(tmp, "project-cola-consumer-first", "la cola rechaza campos desconocidos")

        memorias = indice_memoria.cargar_memorias()
        check(len(memorias) == 4, "lee las 4 memorias del directorio")

        ficheros = indice_memoria.construir(memorias)
        raiz = ficheros[indice_memoria.INDICE]

        # Ninguna memoria puede quedarse fuera de TODOS los índices.
        tematicos = "\n".join(v for k, v in ficheros.items() if k != indice_memoria.INDICE)
        huerfanas = [m["fichero"] for m in memorias if ("(%s)" % m["fichero"]) not in tematicos]
        check(not huerfanas, "ninguna memoria huérfana (fuera: %s)" % huerfanas)

        # Las críticas se enlazan desde el índice raíz, que es lo único que se carga solo.
        check("feedback-codigo-rojo.md" in raiz, "una memoria crítica sale en el índice raíz")
        check("_indice-" in raiz, "el índice raíz apunta a los índices temáticos")

        # El índice raíz tiene que caber holgado.
        ok, _msgs = indice_memoria.comprobar(raiz)
        check(ok, "el índice raíz cabe dentro de los límites del cargador")

        # Regenerar dos veces da lo mismo (idempotente): si no, cada sesión lo ensucia.
        ficheros2 = indice_memoria.construir(indice_memoria.cargar_memorias())
        check(ficheros == ficheros2, "generar el índice es idempotente")

        # Un índice que se pasa del corte se detecta como fallo, no como aviso suave.
        gordo = "x" * (26 * 1024)
        ok_gordo, msgs_gordo = indice_memoria.comprobar(gordo)
        check(not ok_gordo, "un índice de 26KB se marca como cortado (%s)" % msgs_gordo[0][:40])
    finally:
        os.environ.pop("BTP_MEMORY_DIR", None)
        shutil.rmtree(tmp, ignore_errors=True)


def test_salud():
    print("── guardián de límites ──")
    tmp = tempfile.mkdtemp(prefix="btp_mem_")
    try:
        os.environ["BTP_MEMORY_DIR"] = tmp
        import importlib
        import salud_memoria
        importlib.reload(salud_memoria)

        # Con el MEMORY.md real del repo fuera de juego, medimos el caso sano...
        with open(os.path.join(tmp, "MEMORY.md"), "w", encoding="utf-8") as fh:
            fh.write("# índice\n- [una](una.md) — corta\n")
        alertas, info = salud_memoria.chequear()
        rojas = [a for a in alertas if a[1] == "alerta"]
        check(not [r for r in rojas if r[0] == "memoria-indice-cortado"],
              "un MEMORY.md pequeño no dispara alerta")
        check(any("MEMORY.md" in i for i in info), "el informe incluye la medida de MEMORY.md")

        # ...y el caso en que el índice ya no cabe: eso SÍ es alerta roja.
        with open(os.path.join(tmp, "MEMORY.md"), "w", encoding="utf-8") as fh:
            fh.write("# índice\n" + ("- [x](x.md) — relleno\n" * 1400))
        alertas, _info = salud_memoria.chequear()
        rojas = [a for a in alertas if a[1] == "alerta"]
        check(any(r[0] == "memoria-indice-cortado" for r in rojas),
              "un MEMORY.md pasado del corte dispara alerta roja")

        # La clave de la alerta es estable: `salud.py ack <clave>` depende de ello.
        claves = {a[0] for a in alertas}
        check("memoria-indice-cortado" in claves, "la clave de la alerta es estable")
    finally:
        os.environ.pop("BTP_MEMORY_DIR", None)
        shutil.rmtree(tmp, ignore_errors=True)


def test_repetidas():
    """El detector de normas repetidas: que cace lo que debe y calle con el resto."""
    print("── detector de normas repetidas ──")
    import reglas_repetidas as rr

    casos_si = [
        "{{TITULAR}} me lo ha tenido que repetir 3 veces",
        "regla repetida, ya te lo dije el otro día",
        "ha pasado varias veces y le hice perder tiempo",
        "Volví a hacer lo mismo que ya estaba corregido",
    ]
    casos_no = [
        "no repetir los disclaimers del muro en cada mensaje",
        "repetir el índice en cada sesión sería caro",
        "esto se ejecuta dos veces al día",
    ]
    check(all(rr._AUTODECLARA.search(t) for t in casos_si),
          "caza las confesiones de repetición")
    fallos_no = [t for t in casos_no if rr._AUTODECLARA.search(t)]
    check(not fallos_no, "no confunde «no repetir X» con una repetición (%s)" % fallos_no)

    # El filtro de precisión: sin él, 60 días daban 37 falsos positivos. Pero pasarse
    # de estricto sería peor (un detector que nunca detecta), así que se comprueban
    # las dos caras.
    import cosecha_correcciones as cc
    pasan = [
        "No, así no. A partir de ahora los correos SIEMPRE en HTML",
        "eso está mal, te lo he dicho: nunca mergees tú el hero",
        "no me convence, la regla es que primero pasa por el comité",
    ]
    no_pasan = [
        "mejor mañana, hoy estoy cansada",
        "quiero que mires el vuelo de vuelta",
        "y esto cómo lo ves, lo dejamos así?",
    ]
    fallan_si = [t for t in pasan if not rr._es_correccion_seria(t, cc.detectar_senales(t))]
    check(not fallan_si, "deja pasar una corrección de verdad (%s)" % fallan_si)
    cuelan = [t for t in no_pasan if rr._es_correccion_seria(t, cc.detectar_senales(t))]
    check(not cuelan, "no cuela un mensaje de trabajo normal (%s)" % cuelan)

    # El destino se decide por el SLUG: el cuerpo menciona medio sistema.
    check(rr._destino("feedback-cola-consumer-first", "texto") == ".claude/rules/cola.md",
          "propone la regla de cola para una lección de cola")
    check("clinico" not in rr._destino("feedback-equipo-primero-titular-ultimo-recurso",
                                       "un informe de un paciente"),
          "no manda a clínico una lección que solo menciona «informe»")


def main():
    test_indice()
    test_salud()
    test_repetidas()
    print()
    if fallos:
        print("❌ %d fallo(s): %s" % (len(fallos), "; ".join(fallos)))
        return 1
    print("✅ sistema de memoria OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
