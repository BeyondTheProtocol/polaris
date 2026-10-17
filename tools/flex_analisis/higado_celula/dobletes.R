# Dobletes (scDblFinder) y contaminación gen a gen (scCDC) del Flex del hígado (y scCDC del hueso).
# REGLAS_HIGADO_CELULA_v1.json: dobletes_scDblFinder y scCDC. Lo lanza _hc_dobletes.py dentro de un sandbox sin red.
# Uso: Rscript --vanilla dobletes.R <dir_cache> <scdbl|sccdc>
# Entrada: <pref>_{p,i,x}.bin (CSC genes x células, int32 little-endian), <pref>_dims.txt, <pref>_genes.txt,
# <pref>_tipo.txt. Salida en el mismo directorio; células por índice de fila (nunca barcodes).
args <- commandArgs(trailingOnly = TRUE)
cache <- args[1]
modo <- args[2]
SEMILLA <- 20261016L
suppressMessages(library(Matrix))

lee <- function(pref) {
  dims <- scan(file.path(cache, paste0(pref, "_dims.txt")), quiet = TRUE)
  p <- readBin(file.path(cache, paste0(pref, "_p.bin")), "integer", n = dims[2] + 1, size = 4, endian = "little")
  i <- readBin(file.path(cache, paste0(pref, "_i.bin")), "integer", n = dims[3], size = 4, endian = "little")
  x <- readBin(file.path(cache, paste0(pref, "_x.bin")), "integer", n = dims[3], size = 4, endian = "little")
  M <- new("dgCMatrix", i = i, p = p, x = as.numeric(x), Dim = as.integer(dims[1:2]))
  rownames(M) <- readLines(file.path(cache, paste0(pref, "_genes.txt")))
  colnames(M) <- paste0("c", seq_len(ncol(M)) - 1L)
  list(M = M, tipo = readLines(file.path(cache, paste0(pref, "_tipo.txt"))))
}

escribe_csc <- function(M, pref) {
  M <- as(M, "CsparseMatrix")
  writeBin(as.integer(M@p), file.path(cache, paste0(pref, "_p.bin")), size = 4, endian = "little")
  writeBin(as.integer(M@i), file.path(cache, paste0(pref, "_i.bin")), size = 4, endian = "little")
  writeBin(as.integer(round(M@x)), file.path(cache, paste0(pref, "_x.bin")), size = 4, endian = "little")
  writeLines(as.character(c(nrow(M), ncol(M), length(M@x))), file.path(cache, paste0(pref, "_dims.txt")))
}

if (modo == "scdbl") {
  suppressMessages({library(scDblFinder); library(SingleCellExperiment); library(BiocParallel)})
  d <- lee("higado")
  sce <- SingleCellExperiment(list(counts = d$M))
  corre <- function(dbr) {
    set.seed(SEMILLA)
    scDblFinder(sce, clusters = d$tipo, dbr = dbr, BPPARAM = SerialParam(RNGseed = SEMILLA), verbose = FALSE)
  }
  for (nm in c("dbr006", "dbrNULL")) {
    r <- if (nm == "dbr006") corre(0.06) else corre(NULL)
    out <- data.frame(idx = seq_len(ncol(r)) - 1L, clase = as.character(r$scDblFinder.class),
                      score = signif(r$scDblFinder.score, 8), origen = as.character(r$scDblFinder.mostLikelyOrigin),
                      ambiguo = as.logical(r$scDblFinder.originAmbiguous))
    write.csv(out, file.path(cache, paste0("scdbl_", nm, ".csv")), row.names = FALSE)
    cat(nm, "dobletes:", sum(out$clase == "doublet"), "de", nrow(out), "\n")
  }
  writeLines(c(as.character(packageVersion("scDblFinder")), as.character(packageVersion("xgboost")), R.version.string),
             file.path(cache, "scdbl_versiones.txt"))
} else if (modo == "sccdc") {
  suppressMessages({library(Seurat); library(scCDC)})
  for (t in c("higado", "hueso")) {
    d <- lee(t)
    so <- CreateSeuratObject(counts = d$M, min.cells = 0, min.features = 0)
    # Seurat 5 cambia «_» por «-» en AverageExpression y scCDC busca luego el nombre original con WhichCells:
    # etiquetas solo alfanuméricas (mismo agrupamiento, solo cambia el nombre).
    Idents(so) <- factor(gsub("[^A-Za-z0-9]", "", d$tipo))
    set.seed(SEMILLA)
    gcg <- tryCatch(suppressMessages(ContaminationDetection(so)), error = function(e) e)
    if (inherits(gcg, "error")) {
      writeLines(conditionMessage(gcg), file.path(cache, paste0("sccdc_", t, "_error.txt")))
      cat("scCDC", t, ": detección sin GCG o con error\n")
      next
    }
    write.csv(gcg, file.path(cache, paste0("sccdc_", t, "_gcg.csv")))
    set.seed(SEMILLA)
    so2 <- suppressMessages(suppressWarnings(ContaminationCorrection(so, rownames(gcg))))
    Mc <- GetAssayData(so2, assay = "Corrected", layer = "counts")
    stopifnot(identical(colnames(Mc), colnames(d$M)), identical(rownames(Mc), rownames(d$M)))
    escribe_csc(Mc, paste0("sccdc_", t))
    cat("scCDC", t, ": GCG", nrow(gcg), "\n")
  }
  writeLines(c(as.character(packageVersion("scCDC")), as.character(packageVersion("Seurat")), R.version.string),
             file.path(cache, "sccdc_versiones.txt"))
} else {
  stop("modo desconocido")
}
