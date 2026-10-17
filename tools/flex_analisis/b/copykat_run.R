#!/usr/bin/env Rscript
# CopyKAT (segundo método CNV del analista B). Lo lanza `pasos.py cnv` dentro de la ventanilla.
# Uso: Rscript copykat_run.R <dir_entrada> <nombre_muestra> <n_cores>
#   <dir_entrada>/counts.mtx  (genes x células, Matrix Market, UMI crudos)
#   <dir_entrada>/genes.txt   (símbolos, una línea por fila de la matriz)
#   <dir_entrada>/cells.txt   (ids OPACOS c000001…, una línea por columna)
#   <dir_entrada>/ref.txt     (ids de las células de referencia no epiteliales)
# Salida: <dir_entrada>/copykat_prediction.txt (cell.names, copykat.pred). Nada por stdout
# salvo agregados.
suppressMessages({library(Matrix); library(copykat)})
args <- commandArgs(trailingOnly = TRUE)
d <- args[1]; nombre <- args[2]; ncores <- as.integer(args[3])
set.seed(20261016)
m <- as.matrix(readMM(file.path(d, "counts.mtx")))
rownames(m) <- readLines(file.path(d, "genes.txt"))
colnames(m) <- readLines(file.path(d, "cells.txt"))
ref <- readLines(file.path(d, "ref.txt"))
setwd(d)
res <- copykat(rawmat = m, id.type = "S", ngene.chr = 5, win.size = 25, KS.cut = 0.1,
               sam.name = nombre, distance = "euclidean", norm.cell.names = ref,
               output.seg = "FALSE", plot.genes = "FALSE", genome = "hg20", n.cores = ncores)
pred <- data.frame(res$prediction)
write.table(pred, file.path(d, "copykat_prediction.txt"), sep = "\t", quote = FALSE, row.names = FALSE)
cat(sprintf("copykat %s: n=%d aneuploid=%d diploid=%d not.defined=%d\n", nombre, nrow(pred),
            sum(pred$copykat.pred == "aneuploid"), sum(pred$copykat.pred == "diploid"),
            sum(!pred$copykat.pred %in% c("aneuploid", "diploid"))))
