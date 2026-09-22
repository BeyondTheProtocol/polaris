#!/usr/bin/env python3
"""tools/cascada_mapa.py — el MAPA DE DEPENDENCIAS de la cascada clínica.

«Dato clínico → qué análisis/decisiones dependen de él». Es lo que GARANTIZA que,
cuando un dato del perfil maestro cambia, NADIE que dependiera de él se quede sin
re-evaluar. Sin este mapa, la propagación se queda coja (pasó con R175H y con
«medible»: el dato cambió pero su efecto no se propagó a elegibilidad/cumbre).

Es COORDINACIÓN / ESTADO, **NO consejo clínico ni claims**: dice «si cambia X, mira
estos análisis», no «X significa Y para tu tratamiento». Deciden sus médicos.

El mapa es una CONSTANTE EN CÓDIGO (única fuente de verdad), no un fichero editable
por un informe externo: así una inyección en un PDF no puede reescribir qué depende
de qué. Validado y aumentado por `comite-medico` (2026-06-25): añadidos HLA_LOH,
ctDNA/MRD, receptores como fuente propia, sintomatología-que-excluye y viabilidad de
muestra; aristas cruzadas dato→dato (la que más se escapa: tratamiento→medible).

Sin dependencias (stdlib).
"""
import json
import sys

# ─────────────────────────────────────────────────────────────────────────────
# DEPENDIENTES: los análisis/decisiones del sistema que un cambio puede invalidar.
# Cada uno tiene un «modo» de re-ejecución:
#   - "auto"  : determinista y barato → la cascada lo re-encola y se ejecuta solo.
#   - "gate"  : re-ejecución LLM pesada (deep-research, etc.) → se ENCOLA con disparo
#               a un clic de {{TITULAR}}; NUNCA se auto-lanza (coste, regla del proyecto).
#   - "humano": pide criterio clínico/decisión → se marca y se avisa, no se ejecuta.
# ─────────────────────────────────────────────────────────────────────────────
DEPENDIENTES = {
    "elegibilidad":   {"modo": "gate",   "titulo": "Elegibilidad de ensayos (¿cumple criterios?)"},
    "cumbre":         {"modo": "auto",   "titulo": "Brújula a NED (en qué fase y ruta estás)"},
    "contactos":      {"modo": "humano", "titulo": "Coordinadores / contactos a avisar"},
    "fit_vacuna":     {"modo": "gate",   "titulo": "Encaje con la vacuna (neoantígenos / p53)"},
    "radar":          {"modo": "gate",   "titulo": "Radar de literatura (dianas, rutas)"},
    "dianas":         {"modo": "gate",   "titulo": "Dianas / selección de neoantígenos"},
    "diseno_peptidos":{"modo": "gate",   "titulo": "Diseño de péptidos (restricción HLA)"},
    "biopsia_muestra":{"modo": "humano", "titulo": "«Mejor muestra» (qué recoger en la biopsia)"},
    "medible":        {"modo": "auto",   "titulo": "Cuánto tumor es medible hoy (criterio RECIST)"},
    "lineas_previas": {"modo": "auto",   "titulo": "Conteo de líneas de tratamiento (umbral de ensayos)"},
    "respuesta_vacuna":{"modo": "humano","titulo": "Lectura de respuesta a la vacuna (readout ctDNA/MRD)"},
}

# ─────────────────────────────────────────────────────────────────────────────
# MAPA: dato-fuente del perfil maestro → [dependientes].
# Validado por comite-medico (5 lentes, 2026-06-25). Las aristas cruzadas dato→dato
# (un dato cuyo cambio re-dispara la re-lectura de OTRO dato) van en `gobierna_datos`.
# ─────────────────────────────────────────────────────────────────────────────
MAPA = {
    "enfermedad_medible": {
        "titulo": "Enfermedad medible (RECIST 1.1)",
        "dependientes": ["elegibilidad", "cumbre", "contactos", "fit_vacuna", "medible"],
        # respuesta que ELIMINA la última lesión medible también amenaza la ruta
        # (no solo la progresión): re-leer localización por si queda diana medible.
        "gobierna_datos": ["localizacion_lesiones"],
        "fuente_ref": "reference-clinical-profile (Lesion-Medible)",
    },
    "marcadores_moleculares": {
        "titulo": "Marcadores moleculares (cada uno: gen, variante, plataforma, fecha, confianza)",
        "dependientes": ["fit_vacuna", "radar", "elegibilidad", "dianas", "diseno_peptidos"],
        # un marcador que es neoantígeno público (TP53 R175H, KRAS) NO es usable sin
        # cruzar con HLA; subclonal/discordante gobierna decisiones distintas.
        "gobierna_datos": ["hla", "hla_loh"],
        "fuente_ref": "reference-clinical-profile (Perfil-Molecular-Maestro)",
    },
    "hla": {
        "titulo": "Tipado HLA (clase I y II, alta resolución 4 dígitos)",
        "dependientes": ["fit_vacuna", "dianas", "diseno_peptidos"],
        "gobierna_datos": ["hla_loh"],
        "fuente_ref": "reference-clinical-profile (HLA)",
    },
    "hla_loh": {
        # NUEVO (comite-medico): un epítopo restringido por un alelo PERDIDO en el
        # tumor es inútil aunque el péptido una bien. Filtro ANTES de predecir.
        "titulo": "HLA-LOH (pérdida alelo-específica del haplotipo HLA en el tumor)",
        "dependientes": ["fit_vacuna", "dianas", "diseno_peptidos"],
        "gobierna_datos": [],
        "fuente_ref": "J Immunother Cancer 2025 (PMID 40930743) — LOH alelo-específica",
    },
    "tratamientos_previos": {
        "titulo": "Tratamientos previos (líneas, exposición, naïve)",
        "dependientes": ["elegibilidad", "lineas_previas"],
        # un tratamiento que controla la enfermedad puede BORRAR la enfermedad medible
        # (la arista que más se escapa): respuesta ≠ progresión, pero amenaza la ruta.
        "gobierna_datos": ["enfermedad_medible", "estado_enfermedad"],
        "fuente_ref": "reference-clinical-profile (líneas previas)",
    },
    "estado_enfermedad": {
        "titulo": "Estado de enfermedad (NED / estable / progresión / nueva lesión)",
        "dependientes": ["cumbre", "elegibilidad", "biopsia_muestra"],
        # progresión puede crear nueva lesión biopsiable/medible → re-leer localización.
        "gobierna_datos": ["enfermedad_medible", "localizacion_lesiones"],
        "fuente_ref": "reference-clinical-profile (estado)",
    },
    "localizacion_lesiones": {
        "titulo": "Localización de lesiones (ósea-blástica / irradiada / biopsiable / visceral)",
        "dependientes": ["biopsia_muestra", "medible", "elegibilidad"],
        "gobierna_datos": ["enfermedad_medible"],
        "fuente_ref": "reference-clinical-profile (localización)",
    },
    "ctdna_mrd": {
        # NUEVO (comite-medico): señala progresión MOLECULAR antes que la imagen;
        # readout de respuesta de vacuna; FUENTE de varios marcadores (RB1, ESR1).
        # BIDIRECCIONAL con marcadores. NO dispara rojo solo (es señal amarilla).
        "titulo": "ctDNA / MRD (carga de ADN tumoral circulante, MRD+/–)",
        "dependientes": ["respuesta_vacuna", "radar"],
        "gobierna_datos": ["marcadores_moleculares", "estado_enfermedad"],
        "fuente_ref": "literatura (readout de vacuna personalizada)",
    },
    "receptores": {
        # NUEVO (comite-medico): dato-fuente propio, no sub-campo. Se re-testea en cada
        # biopsia y puede virar (conversión de receptor); HER2-low/ultralow abre ADC.
        "titulo": "Receptores (ER / PR / HER2) — subtipo",
        "dependientes": ["elegibilidad", "dianas"],
        "gobierna_datos": [],
        "fuente_ref": "reference-clinical-profile (receptores)",
    },
    "sintomatologia": {
        # NUEVO (comite-medico): PNV21 excluye enfermedad sintomática (citopenias,
        # órgano sintomático). Puede tumbar la ruta SIN cambiar marcador ni imagen.
        "titulo": "Sintomatología / carga sintomática (citopenias, órgano sintomático)",
        "dependientes": ["elegibilidad"],
        "gobierna_datos": [],
        "fuente_ref": "reference-clinical-profile (criterios de exclusión PNV21)",
    },
    "viabilidad_muestra": {
        # NUEVO (comite-medico): sin tejido viable + PBMC + RNA-seq profundo no hay
        # vacuna aunque el resto cuadre. El cuello real de la fabricación.
        "titulo": "Viabilidad de muestra inmuno (tejido viable, PBMC/leucaféresis, RNA-seq)",
        "dependientes": ["fit_vacuna", "biopsia_muestra"],
        "gobierna_datos": [],
        "fuente_ref": "reference-clinical-profile (gaps de la biopsia de Zúrich)",
    },
}


def datos():
    """Lista de los datos-fuente que el mapa conoce (claves canónicas)."""
    return sorted(MAPA.keys())


def dependientes_de(dato):
    """Dependientes DIRECTOS de un dato (lista de claves de DEPENDIENTES). [] si desconocido."""
    return list(MAPA.get(dato, {}).get("dependientes", []))


def afectados(datos_cambiados, *, _vistos=None):
    """Cierre TRANSITIVO: dado un conjunto de datos cambiados, devuelve TODOS los
    dependientes afectados, siguiendo también las aristas cruzadas dato→dato
    (`gobierna_datos`). Esto es lo que impide que un cambio «se escape» un nivel.

    Devuelve dict {dependiente: {"modo": .., "titulo": .., "via": [datos que lo gatillan]}}.
    Determinista, sin red. Protegido contra ciclos (`_vistos`).
    """
    if isinstance(datos_cambiados, str):
        datos_cambiados = [datos_cambiados]
    _vistos = _vistos if _vistos is not None else set()
    pendientes = [d for d in datos_cambiados if d not in _vistos]
    out = {}
    while pendientes:
        dato = pendientes.pop()
        if dato in _vistos:
            continue
        _vistos.add(dato)
        info = MAPA.get(dato)
        if not info:
            continue
        for dep in info.get("dependientes", []):
            meta = DEPENDIENTES.get(dep, {"modo": "humano", "titulo": dep})
            entry = out.setdefault(dep, {"modo": meta["modo"], "titulo": meta["titulo"], "via": []})
            if dato not in entry["via"]:
                entry["via"].append(dato)
        # aristas cruzadas: un dato que gobierna otros datos arrastra sus dependientes
        for otro in info.get("gobierna_datos", []):
            if otro not in _vistos and otro not in pendientes:
                pendientes.append(otro)
    return out


def datos_alcanzados(datos_cambiados):
    """Cierre transitivo SOLO de datos (incluye los gobernados aguas abajo). Útil para
    saber qué OTROS datos del maestro habría que re-leer."""
    vistos = set()
    afectados(datos_cambiados, _vistos=vistos)
    return sorted(vistos)


def validar_mapa():
    """Auto-consistencia: todo dependiente referido existe en DEPENDIENTES; todo dato
    gobernado existe en MAPA; ningún dato se referencia a sí mismo. Devuelve [] si ok."""
    errores = []
    for dato, info in MAPA.items():
        for dep in info.get("dependientes", []):
            if dep not in DEPENDIENTES:
                errores.append("dato %r → dependiente desconocido %r" % (dato, dep))
        for otro in info.get("gobierna_datos", []):
            if otro not in MAPA:
                errores.append("dato %r → gobierna dato desconocido %r" % (dato, otro))
            if otro == dato:
                errores.append("dato %r se gobierna a sí mismo" % dato)
    return errores


def render_json():
    """Vuelca el mapa como JSON legible (para inspección/auditoría)."""
    return json.dumps(
        {"datos": MAPA, "dependientes": DEPENDIENTES, "consistente": not validar_mapa()},
        ensure_ascii=False, indent=2)


def main(argv):
    cmd = argv[0] if argv else "show"
    if cmd == "show":
        print(render_json())
        return 0
    if cmd == "datos":
        print("\n".join(datos()))
        return 0
    if cmd == "afectados":
        cambios = argv[1:] or []
        if not cambios:
            print("uso: cascada_mapa.py afectados <dato> [<dato>...]")
            return 2
        res = afectados(cambios)
        for dep, info in sorted(res.items()):
            print("[%s] %s  (vía: %s)" % (info["modo"], info["titulo"], ", ".join(info["via"])))
        return 0
    if cmd == "check":
        errs = validar_mapa()
        if errs:
            print("\n".join("✗ " + e for e in errs))
            return 1
        print("✅ mapa consistente (%d datos, %d dependientes)" % (len(MAPA), len(DEPENDIENTES)))
        return 0
    print("uso: cascada_mapa.py [show|datos|afectados <dato>...|check]")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
