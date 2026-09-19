#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Eval del recall de memorias: ¿sale la regla que hacía falta, en el top-3?

POR QUÉ (25-jul-26): el hook `memoria_recall.sh` inyecta las memorias más relevantes en
cada mensaje de {{TITULAR}}. Si ese ranking falla, la lección existe pero no llega, y ella
tiene que repetirse. Antes de esta rama, «voy a mandar un correo a la oncóloga» no traía
NINGUNA memoria de correo ni del muro: BM25 casa palabras, y ella no usa las palabras de
las memorias. Esto lo convierte en un número que se vigila, en vez de un fallo que se sufre.

El corpus real cambia ({{TITULAR}} añade memorias), así que el umbral es holgado a propósito:
detecta una REGRESIÓN del ranking, no clava un número exacto.
"""
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "tools"))

# (situación tal y como la escribiría {{TITULAR}}, [memorias que valdrían])
CASOS = [
    ("voy a mandar un correo a la oncóloga",
     ["feedback-correos-formato-html", "feedback-delegar-correos-al-agente",
      "feedback-si-puedo-adjuntar-correos", "feedback-correo-cuenta-correcta-del-hilo",
      "project-correo-outbox-engine", "feedback-gestor-correo-hace-vega-consulta"]),
    ("quiero cambiar el hero de la web",
     ["feedback-no-tocar-copy-web-sin-ok", "feedback-siempre-pasar-preview",
      "feedback-web-workflow", "feedback-copy-publico-siempre-comite"]),
    ("hazme el itinerario del viaje a {{CIUDAD}}",
     ["project-orbita-viaje-{{CIUDAD}}", "feedback-formato-tabla-pasos-itinerarios",
      "project-carpeta-docs-imprimir-viaje-julio2026",
      "feedback-vega-propone-acciones-datadas-de-viaje"]),
    ("verifica esta cita de un paper",
     ["feedback-evidencia-grok-no-perplexity", "project-consensus-scite-must",
      "feedback-cotejar-siempre-fuente-clinica", "feedback-convergencia-solo-si-abri-cada-fuente"]),
    ("qué modelo uso para esto, quiero ahorrar",
     ["feedback-estrategia-coste-precision", "feedback-elegir-modelo-y-modo",
      "feedback-coste-nunca-corta-ned-pide-aprobacion", "feedback-no-degradar-lo-critico-bloquear-avisar"]),
    ("voy a abrir una rama para trabajar en esto",
     ["feedback-sesion-paralela-seguridad-primero", "feedback-casa-base-commiteada-para-aislar",
      "feedback-comite-git", "feedback-autopodar-worktrees", "feedback-worktree-editar-rutas-del-worktree"]),
    ("publica esto en instagram",
     ["feedback-comentarios-siempre-voz-titular", "project-acceso-autonomo-redes",
      "feedback-no-hilos-post-largo", "project-dm-monitor-ig-linkedin", "reference-meta-cuentas-mapa"]),
    ("añade una tarjeta al tablero",
     ["feedback-tarjetas-necesitan-ok-del-gestor", "feedback-tablero-tareas-generales-no-tecnico",
      "project-comite-gestion-tareas", "feedback-tablero-capturar-agendado-y-autoreconciliar"]),
    ("dime qué me queda pendiente",
     ["feedback-fuente-unica-exhaustiva-no-slice", "feedback-comprobar-si-lo-hizo-solo",
      "feedback-cierre-lo-tuyo-ahora", "feedback-centralizar-info-no-resurgir-resueltos"]),
    ("instala esta herramienta nueva que he visto",
     ["feedback-herramienta-ia-nueva-checklist", "feedback-copiar-y-mejorar-sin-preguntar-instalar-si",
      "feedback-herramientas-externas-auditoria-primero", "feedback-instalar-lo-que-necesite-en-el-mac"]),
    ("se ha caído un daemon otra vez",
     ["feedback-auto-detectar-resolver-problemas", "project-acuse-alertas-salud",
      "feedback-arreglar-errores-sin-preguntar"]),
    ("contéstale a {{CONTACTO}} por whatsapp",
     ["feedback-responder-whatsapp-borrador-a-telegram", "feedback-wp-leer-ultimo-mensaje-antes-de-redactar",
      "feedback-chequear-whatsapp-antes-de-triar", "feedback-whatsapp-monitor-silencioso-vega-saca-tareas"]),
    ("escríbeme un post para X",
     ["feedback-no-hilos-post-largo", "feedback-comentarios-siempre-voz-titular",
      "feedback-no-rollo-builder-voz", "feedback-no-em-dash-tell-ia"]),
    ("cuánto llevamos gastado este mes",
     ["reference-coste-lazo-medidor-y-fuga", "feedback-control-gasto-tarjetas-ia",
      "feedback-estrategia-coste-precision"]),
    ("quiero montar un comité nuevo para esto",
     ["feedback-plan-primero-luego-ejecutar", "feedback-comites-registro",
      "feedback-comites-poseen-dominio-no-duplicar", "project-constelacion-constructor-cajas"]),
    ("esto lo valido yo o {{CONTACTO}}",
     ["feedback-titular-valida-tecnico-contacto-clinico", "project-contacto-contacto-{{CIUDAD}}"]),
    ("búscame todo sobre esta persona",
     ["feedback-dossier-contactos-norma", "feedback-scraping-publico-a-fondo-linea-quema-causa",
      "feedback-flujo-redaccion-contactos"]),
    ("prepárame un pdf para imprimir",
     ["reference-pdf-tooling-polaris", "project-carpeta-docs-imprimir-viaje-julio2026"]),
    ("guarda esto para que no se te olvide",
     ["feedback-auto-mejora", "feedback-entregable-no-vive-solo-en-chat",
      "feedback-automejora-minar-lecciones-no-solo-adoptar"]),
    ("responde a este periodista",
     ["feedback-contacto-contacto-prensa", "feedback-flujo-redaccion-contactos",
      "feedback-prensa-integrada-no-mock-periodico"]),
    ("sube los cambios a producción",
     ["feedback-deploy-netlify-creditos", "feedback-no-tocar-copy-web-sin-ok",
      "feedback-web-workflow"]),
    ("qué sabemos de sus mutaciones",
     ["reference-clinical-profile", "feedback-dianas-publicas", "reference-tablero-ensayos"]),
    ("necesito comprar un cacharro",
     ["feedback-compras-excelencia", "feedback-compras-opcion-optima-coste-proactiva",
      "project-inventario-gadgets-amazon", "project-asistente-amazon"]),
    ("estoy agotada hoy",
     ["reference-capa-humano-centro", "insights-trabajar-con-titular",
      "feedback-coach-colaboracion", "user-altas-capacidades-nd-no-deficit"]),
    ("no me des tanta chapa",
     ["feedback-ahorrar-tokens-no-sobreexplicar", "feedback-mensajes-claros-no-cripticos",
      "feedback-pensar-por-dentro-responder-limpio"]),
]

UMBRAL = 0.72   # recall@3 mínimo. Medido el 25-jul: 0.76 antes (BM25 puro), 0.80 con alias.


def main():
    import memoria_radar

    if not os.path.isdir(memoria_radar.MEMORY_DIR):
        print("ℹ️  Sin carpeta de memorias: nada que evaluar (ok en una máquina limpia).")
        return 0

    existentes = {f[:-3] for f in os.listdir(memoria_radar.MEMORY_DIR) if f.endswith(".md")}
    aciertos, evaluados, fallos = 0, 0, []

    for consulta, esperadas in CASOS:
        vivas = [s for s in esperadas if s in existentes]
        if not vivas:
            continue  # ninguna de las memorias del caso existe ya: el caso caducó
        evaluados += 1
        try:
            top = memoria_radar.resucitar(dias_dormida=0, n=3, foco_texto=consulta)
        except Exception as e:
            fallos.append("%s → EXCEPCIÓN %s" % (consulta, e))
            continue
        slugs = [c["slug"] for c in top]
        if any(s in vivas for s in slugs):
            aciertos += 1
        else:
            fallos.append("«%s» → %s" % (consulta, slugs or ["(nada)"]))

    if not evaluados:
        print("ℹ️  Ningún caso evaluable con el corpus actual.")
        return 0

    recall = aciertos / evaluados
    print("recall@3 = %.2f (%d/%d casos)" % (recall, aciertos, evaluados))
    # Que exista la caché no significa que el brazo se use: hace falta peso > 0 Y que
    # el modelo esté cargable en ESTE intérprete. Decirlo mal aquí es mentirse en el eval.
    usable = (memoria_radar.PESO_SEMANTICO > 0
              and memoria_radar._kb_embed() is not None
              and bool(memoria_radar._cache_vectores()))
    print("brazo semántico: %s (peso %.2f)"
          % ("en uso" if usable else "no interviene", memoria_radar.PESO_SEMANTICO))
    for f in fallos:
        print("  ✗ %s" % f)

    if recall < UMBRAL:
        print("❌ recall por debajo del umbral %.2f: el recall ha REGRESADO" % UMBRAL)
        return 1
    print("✅ recall dentro de lo esperado")
    return 0


if __name__ == "__main__":
    sys.exit(main())
