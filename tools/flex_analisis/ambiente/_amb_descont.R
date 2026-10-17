# SoupX (primario: rho con genes luminales en el nulo; sensibilidad: autoEstCont) y DecontX (z = tipo de A,
# background = gotas vacías) sobre una muestra. Entrada/salida: binarios del directorio de intercambio.
# REGLAS_AMBIENTE_v1.json. stdout: solo agregados.
suppressPackageStartupMessages({library(Matrix); library(jsonlite); library(SoupX); library(celda)})
args <- commandArgs(trailingOnly = TRUE)
d <- args[1]
meta <- fromJSON(file.path(d, "meta.json"))
lee <- function(nombre, ng, nc) {
  p <- readBin(file.path(d, paste0(nombre, "_p.bin")), "integer", n = nc + 1, size = 8)
  p <- as.numeric(p)
  nnz <- p[length(p)]
  i <- readBin(file.path(d, paste0(nombre, "_i.bin")), "integer", n = nnz, size = 4)
  x <- readBin(file.path(d, paste0(nombre, "_x.bin")), "double", n = nnz)
  sparseMatrix(i = i + 1, p = p, x = x, dims = c(ng, nc), repr = "C")
}
escribe <- function(M, nombre) {
  M <- as(M, "CsparseMatrix")
  M <- as(M, "generalMatrix")
  if (!is(M, "dgCMatrix")) M <- as(M, "dMatrix")
  writeBin(as.integer(M@p), file.path(d, paste0(nombre, "_p.bin")), size = 8)
  writeBin(as.integer(M@i), file.path(d, paste0(nombre, "_i.bin")), size = 4)
  writeBin(as.double(M@x), file.path(d, paste0(nombre, "_x.bin")))
}
ng <- meta$n_genes; nc <- meta$n_celulas; nv <- meta$n_vacias
toc <- lee("celulas", ng, nc)
vac <- lee("vacias", ng, nv)
genes <- make.unique(meta$genes)
rownames(toc) <- genes; rownames(vac) <- genes
colnames(toc) <- paste0("c", seq_len(nc)); colnames(vac) <- paste0("v", seq_len(nv))
tipo <- setNames(as.character(meta$tipo), colnames(toc))
sim <- meta$simbolos
info <- list(versiones = list(R = R.version.string, SoupX = as.character(packageVersion("SoupX")),
                              celda = as.character(packageVersion("celda")), Matrix = as.character(packageVersion("Matrix"))))

# ---------------- SoupX
soup <- rowSums(vac)
prof <- data.frame(row.names = genes, est = soup / sum(soup), counts = soup)
base <- function() {
  sc <- SoupChannel(tod = toc, toc = toc, calcSoupProfile = FALSE)
  sc <- setSoupProfile(sc, prof)
  setClusters(sc, tipo)
}
ge <- intersect(meta$genes_estima, sim)
info$genes_estima_presentes <- ge
info$genes_estima_ausentes <- setdiff(meta$genes_estima, sim)
gl <- list(LUM = genes[match(ge, sim)])
use <- matrix(tipo %in% meta$nulo, ncol = 1, dimnames = list(colnames(toc), "LUM"))
t0 <- Sys.time(); set.seed(meta$semilla)
sc <- base()
sc <- calculateContaminationFraction(sc, gl, use, forceAccept = TRUE, verbose = FALSE)
info$soupx_rho <- unname(sc$metaData$rho[1])
out <- adjustCounts(sc, method = "subtraction", roundToInt = TRUE, verbose = 0)
escribe(out, "soupx")
info$soupx_segundos <- as.numeric(difftime(Sys.time(), t0, units = "secs"))
cat("SoupX primario rho", round(info$soupx_rho, 4), "|", round(info$soupx_segundos), "s\n")

t0 <- Sys.time(); set.seed(meta$semilla)
sc2 <- base()
sc2 <- tryCatch(autoEstCont(sc2, doPlot = FALSE, forceAccept = TRUE, verbose = FALSE), error = function(e) {
  cat("autoEstCont falló:", conditionMessage(e), "\n"); NULL })
if (is.null(sc2)) {
  info$soupx_auto_rho <- NA; escribe(toc, "soupx_auto"); info$soupx_auto_fallo <- TRUE
} else {
  info$soupx_auto_rho <- unname(sc2$metaData$rho[1])
  out2 <- adjustCounts(sc2, method = "subtraction", roundToInt = TRUE, verbose = 0)
  escribe(out2, "soupx_auto")
}
info$soupx_auto_segundos <- as.numeric(difftime(Sys.time(), t0, units = "secs"))
cat("SoupX auto rho", round(info$soupx_auto_rho, 4), "|", round(info$soupx_auto_segundos), "s\n")

# ---------------- DecontX
t0 <- Sys.time(); set.seed(meta$semilla)
dx <- decontX(x = toc, z = tipo, background = vac, seed = meta$semilla, verbose = FALSE)
escribe(dx$decontXcounts, "decontx")
writeBin(as.double(dx$contamination), file.path(d, "contaminacion_decontx.bin"))
info$decontx_contaminacion_mediana <- median(dx$contamination)
info$decontx_contaminacion_por_tipo <- as.list(tapply(dx$contamination, tipo, median))
info$decontx_segundos <- as.numeric(difftime(Sys.time(), t0, units = "secs"))
cat("DecontX contaminación mediana", round(info$decontx_contaminacion_mediana, 4), "|", round(info$decontx_segundos), "s\n")
info$memoria_R_MB <- sum(gc()[, 6])
write(toJSON(info, auto_unbox = TRUE, digits = 8, na = "null"), file.path(d, "info_r.json"))
