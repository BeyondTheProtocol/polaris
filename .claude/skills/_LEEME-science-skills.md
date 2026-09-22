# Science Skills (DeepMind) — guardarraíl de uso

Instaladas el 5/7/26 desde `google-deepmind/science-skills` (Apache-2.0, auditadas en
`_cajita/science-skills-pilot/PILOTO-RESUMEN.md`). Añaden lo que biomcp/cBioPortal NO cubren:
**proteína, estructura, rutas y quimioinformática.**

Skills vivas: `uniprot_database`, `alphafold_database_fetch_and_analyze`, `pdb_database`,
`foldseek_structural_search`, `string_database`, `reactome_database`, `chembl_database`,
`pubchem_database`, `opentargets_database`, `interpro_database`, `human_protein_atlas_database`
(+ `credentials`). Necesitan `uv` (ya instalado en `~/.local/bin`).

## 🔒 REGLA DE MURO (leer antes de usarlas)
Estas skills **egresan por su cuenta** (llaman a UniProt/PDB/EBI/etc. directamente con
`polite-http`) — **NO pasan por `borde.py`**. Por tanto son **carril N0/N1 exclusivamente**:
- ✅ Permitido: nombres de gen (BRCA1), accesiones (P38398), IDs de estructura, términos públicos.
- 🚫 PROHIBIDO: cualquier dato **N2** (nombre de {{TITULAR}} + fecha + hospital + relato). El borde
  no las vigila, así que la disciplina la pone el agente. Ver regla de niveles de sensibilidad
  en `CLAUDE.md` (`feedback-niveles-sensibilidad-datos`).
- `foldseek` **sube tu fichero de entrada** a EBI → solo estructuras públicas, nada sensible.

## Excluida a propósito
`alphagenome_single_variant_analysis` — manda coordenadas de variante a un servidor de Google.
NO instalada. Si algún día se quiere, es decisión de {{TITULAR}} (y aun así, sin identificadores).

## Revertir
Borra estas carpetas de `.claude/skills/` y el piloto `_cajita/science-skills-pilot/`.
