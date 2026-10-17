# CopyKAT (navinlabcode/copykat @01924c09, v1.2.5) sobre la entrada que exporta `flex_a.py malignas infercnv`.
# Uso: Rscript --vanilla _copykat.R <higado|hueso> <n_cores> [<trozo> <n_trozos>]
# Con trozos (hígado: la corrida entera agotó 16 GB): las células no-referencia se reparten en
# n_trozos por posición (i %% n_trozos), y cada trozo corre con la MISMA referencia completa.
# Lee y escribe solo en ~/Clinico-PRIVADO/flex/analisis_A/copykat_<tejido>/. Semilla 20261016.
suppressPackageStartupMessages({library(Matrix); library(copykat)})
args <- commandArgs(trailingOnly = TRUE)
tejido <- args[1]
ncores <- as.integer(ifelse(length(args) > 1, args[2], "4"))
trozo <- as.integer(ifelse(length(args) > 2, args[3], "0"))
ntrozos <- as.integer(ifelse(length(args) > 3, args[4], "1"))
d <- file.path(Sys.getenv("HOME"), "Clinico-PRIVADO", "flex", "analisis_A", paste0("copykat_", tejido))
setwd(d)
set.seed(20261016)
M <- readMM("matriz.mtx")
genes <- read.csv("genes.csv")[[1]]
cells <- read.csv("celulas.csv")[[1]]
ref <- read.csv("referencia.csv")[[1]]
M <- as.matrix(M)
storage.mode(M) <- "numeric"
# símbolos duplicados: se suman
rownames(M) <- genes
if (any(duplicated(genes))) M <- rowsum(M, genes)
colnames(M) <- cells
if (ntrozos > 1) {
  noref <- which(!(cells %in% ref))
  mio <- noref[(seq_along(noref) - 1) %% ntrozos == trozo]
  M <- M[, c(mio, which(cells %in% ref))]
  sub <- file.path(d, paste0("trozo", trozo)); dir.create(sub, showWarnings = FALSE); setwd(sub)
}
cat("CopyKAT", tejido, "genes", nrow(M), "celulas", ncol(M), "referencia", length(ref), "\n")
res <- copykat(rawmat = M, id.type = "S", ngene.chr = 5, win.size = 25, KS.cut = 0.1,
               sam.name = paste0(tejido, ifelse(ntrozos > 1, paste0("_t", trozo), "")), distance = "euclidean", norm.cell.names = ref,
               output.seg = "FALSE", plot.genes = "FALSE", genome = "hg20", n.cores = ncores)
pred <- res$prediction
cat("predicciones:\n"); print(table(pred$copykat.pred))
