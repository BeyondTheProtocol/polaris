// Carga tumoral hepática: qué fracción del hígado ocupan las lesiones detectadas. Sale de los
// dos volúmenes que ya trae estudio.json, así que vale para los estudios ya generados. Es
// EXPLORATORIA como el volumen del que sale: no es un índice pronóstico validado.
export function fraccionHepaticaPct (tumoralMl, higadoMl) {
  const t = Number(tumoralMl)
  const h = Number(higadoMl)
  if (tumoralMl == null || higadoMl == null || !isFinite(t) || !isFinite(h) || h <= 0 || t < 0) return null
  return (100 * t) / h
}
