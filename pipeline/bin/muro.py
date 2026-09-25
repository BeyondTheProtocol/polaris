"""
muro.py — guardián de privacidad del pipeline de neoantígenos.

Regla innegociable (CLAUDE.md): los datos genómicos crudos (VCF/HLA/FASTQ) se
procesan SOLO en local. A las APIs externas solo va terminología GENÉRICA
(símbolo de gen, accession de proteína), NUNCA PII ni datos crudos.

Este módulo NO reemplaza al muro global del sistema (`tools/salida.py`): es una
capa de defensa local del pipeline que falla en RUIDOSO si algo intenta mandar
fuera lo que no debe. Hereda la filosofía, no la copia.
"""
from __future__ import annotations
import json
import os
import re

# Hosts a los que el pipeline tiene permiso de salir (solo datos genéricos).
HOSTS_PERMITIDOS = {
    "alphafold.ebi.ac.uk",   # estructura 3D por accession de proteína (genérico)
    "rest.ensembl.org",      # lookup de gen/transcrito por símbolo (genérico)
    "www.ebi.ac.uk",         # UniProt / proteins API (genérico) — OJO: ver RUTAS_PERMITIDAS
    "rest.uniprot.org",
}

# 15-jul-26 — permitir por HOST no basta: `www.ebi.ac.uk` sirve UniProt (genérico, OK)
# **y también** el EMBOSS/Needleman (`/Tools/services/rest/emboss_needle`), que es lo que
# usa el módulo B-antigen de SNAF para alinear — o sea, mandaría SECUENCIA DERIVADA DEL
# TUMOR de {{TITULAR}} a un servidor externo, y el muro lo dejaba pasar porque el host estaba
# en la lista. Se permite por host + PREFIJO DE RUTA.
RUTAS_PERMITIDAS = {
    "www.ebi.ac.uk": ("/proteins/api/", "/pdbe/", "/interpro/"),
}

# Rutas PROHIBIDAS siempre, aunque su host esté permitido (alineamiento remoto = egress
# de secuencia). El módulo B-antigen de SNAF queda VETADO por diseño: solo T-antigen,
# que corre 100% local con MHCflurry (el mismo motor que ya usa la Etapa D).
RUTAS_VETADAS = (
    "/tools/services/rest/emboss",   # EMBOSS Needle/Water — alineamiento remoto
    "/tools/psa/", "/tools/msa/",    # alineamiento por pares / múltiple
    "/tools/services/rest/clustalo",
    "/blast", "/tools/sss/",         # BLAST y similares
)

# Patrones que NUNCA deben viajar a una API externa (señales de dato crudo/PII).
# ESTRUCTURALES: no nombran a nadie, así que viven aquí y viajan al repo público.
_PROHIBIDO_ESTRUCTURAL = [
    re.compile(r"\b[ACGT]{12,}\b"),                 # cadena de bases larga = secuencia cruda
    re.compile(r"##fileformat=VCF", re.IGNORECASE),  # contenido de VCF
    re.compile(r"\bHLA-[ABC]\*\d", re.IGNORECASE),   # alelo HLA del paciente
]

# IDENTIFICATIVOS (nombre propio, términos vetados en público): NO pueden estar escritos
# aquí. 19-sep-2026: estaban, y este fichero se publicó tal cual en el repo público — el
# muro que veta una palabra la publicaba. Viven en un overlay gitignored, y cada instalación
# escribe los suyos. Sin overlay el muro sigue en pie con lo estructural, y `cargados()` lo
# dice para que un test local lo exija.
_OVERLAY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "config", "vetados.local.json")


def _identificativos(ruta: str = _OVERLAY):
    try:
        with open(ruta, encoding="utf-8") as fh:
            terminos = json.load(fh).get("terminos") or []
    except (OSError, ValueError):
        return []
    return [re.compile(r"\b%s\b" % re.escape(str(t)), re.IGNORECASE) for t in terminos if str(t).strip()]


def cargados() -> int:
    """Cuántos términos identificativos trae el overlay. 0 = solo queda lo estructural."""
    return len(_identificativos())


_PROHIBIDO = _PROHIBIDO_ESTRUCTURAL + _identificativos()


class MuroError(RuntimeError):
    """Se intentó sacar fuera algo que el muro prohíbe."""


def host_permitido(url: str) -> bool:
    """Permitido = host en la allowlist Y (si ese host tiene rutas acotadas) la ruta
    empieza por una permitida Y no cae en ninguna ruta VETADA.
    15-jul-26: antes bastaba el host, y por ahí se colaba el EMBOSS del EBI (alineamiento
    remoto = mandar secuencia del tumor fuera). Ver RUTAS_VETADAS."""
    m = re.match(r"https?://([^/]+)(/[^?#]*)?", url)
    if not m:
        return False
    host = m.group(1).lower()
    ruta = (m.group(2) or "/").lower()
    if host not in HOSTS_PERMITIDOS:
        return False
    for veto in RUTAS_VETADAS:
        if ruta.startswith(veto):
            return False
    permitidas = RUTAS_PERMITIDAS.get(host)
    if permitidas and not any(ruta.startswith(p) for p in permitidas):
        return False
    return True


def comprobar_salida(url: str, carga: str = "") -> None:
    """Lanza MuroError si la URL no está permitida o la carga huele a dato crudo/PII."""
    if not host_permitido(url):
        raise MuroError(
            f"MURO: salida bloqueada a host no permitido: {url}. "
            f"Solo se permite terminología genérica a {sorted(HOSTS_PERMITIDOS)}."
        )
    for pat in _PROHIBIDO:
        if pat.search(carga or ""):
            raise MuroError(
                "MURO: la carga de la petición contiene un patrón de dato crudo/PII "
                f"({pat.pattern}). Esto NUNCA sale a una API externa."
            )


def es_termino_generico(token: str) -> bool:
    """True si `token` parece terminología genérica segura (símbolo de gen / accession)."""
    token = token.strip()
    if re.fullmatch(r"[A-Z0-9]{1,15}", token):        # símbolo de gen tipo TP53, PIK3CA
        return True
    if re.fullmatch(r"[A-NR-Z][0-9][A-Z0-9]{3}[0-9]", token):  # accession UniProt tipo P04637
        return True
    return False
