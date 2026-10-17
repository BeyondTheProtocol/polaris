# Post hoc 2, P3 (POSTHOC_2_PLAN.json): scDblFinder en la configuración de la viñeta para Flex (dbr.sd = 1) y sin
# clusters con removeUnidentifiable = FALSE (dobletes homotípicos), más el cruce de las dos. NO decide ningún veredicto.
# Lo lanza _hc_posthoc2.py (paso p3_dobletes de flex_hc_posthoc2) dentro del sandbox sin red.
# Uso: Rscript --vanilla dobletes_posthoc2.R <dir_entrada> <dir_salida> [modo ...]
# Entrada: los binarios que exportó el paso canónico (<dir_entrada>/higado_{p,i,x}.bin, _dims, _genes, _tipo).
# Salida: <dir_salida>/scdbl_<modo>.csv; células por índice de fila (nunca barcodes).
args <- commandArgs(trailingOnly = TRUE)
din <- args[1]
dout <- args[2]
modos <- if (length(args) >= 3) args[3:length(args)] else c("dbrsd1", "sinclusters", "sinclusters_dbrsd1")
SEMILLA <- 20261016L
suppressMessages({library(Matrix); library(scDblFinder); library(SingleCellExperiment); library(BiocParallel)})

lee <- function(pref) {
  dims <- scan(file.path(din, paste0(pref, "_dims.txt")), quiet = TRUE)
  p <- readBin(file.path(din, paste0(pref, "_p.bin")), "integer", n = dims[2] + 1, size = 4, endian = "little")
  i <- readBin(file.path(din, paste0(pref, "_i.bin")), "integer", n = dims[3], size = 4, endian = "little")
  x <- readBin(file.path(din, paste0(pref, "_x.bin")), "integer", n = dims[3], size = 4, endian = "little")
  M <- new("dgCMatrix", i = i, p = p, x = as.numeric(x), Dim = as.integer(dims[1:2]))
  rownames(M) <- readLines(file.path(din, paste0(pref, "_genes.txt")))
  colnames(M) <- paste0("c", seq_len(ncol(M)) - 1L)
  list(M = M, tipo = readLines(file.path(din, paste0(pref, "_tipo.txt"))))
}

d <- lee("higado")
sce <- SingleCellExperiment(list(counts = d$M))

corre <- function(m) {
  set.seed(SEMILLA)
  bp <- SerialParam(RNGseed = SEMILLA)
  if (m == "dbrsd1") {
    scDblFinder(sce, clusters = d$tipo, dbr = 0.06, dbr.sd = 1, BPPARAM = bp, verbose = FALSE)
  } else if (m == "sinclusters") {
    scDblFinder(sce, clusters = NULL, dbr = 0.06, removeUnidentifiable = FALSE, BPPARAM = bp, verbose = FALSE)
  } else if (m == "sinclusters_dbrsd1") {
    scDblFinder(sce, clusters = NULL, dbr = 0.06, dbr.sd = 1, removeUnidentifiable = FALSE, BPPARAM = bp, verbose = FALSE)
  } else {
    stop("modo desconocido")
  }
}

columna <- function(r, k) {
  v <- SummarizedExperiment::colData(r)[[k]]
  if (is.null(v)) rep(NA, ncol(r)) else v
}

for (m in modos) {
  r <- corre(m)
  out <- data.frame(idx = seq_len(ncol(r)) - 1L, clase = as.character(r$scDblFinder.class),
                    score = signif(r$scDblFinder.score, 8), origen = as.character(columna(r, "scDblFinder.mostLikelyOrigin")),
                    ambiguo = as.logical(columna(r, "scDblFinder.originAmbiguous")))
  write.csv(out, file.path(dout, paste0("scdbl_", m, ".csv")), row.names = FALSE)
  cat(m, "dobletes:", sum(out$clase == "doublet"), "de", nrow(out), "\n")
}
writeLines(c(as.character(packageVersion("scDblFinder")), as.character(packageVersion("xgboost")), R.version.string),
           file.path(dout, "scdbl_versiones.txt"))
