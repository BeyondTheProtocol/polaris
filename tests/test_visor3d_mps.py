#!/usr/bin/env python3
"""¿La GPU del Mac (MPS) da lo MISMO que la CPU en la red de MAMA-MIA?

Por qué existe: en CPU, UN parche de 128³ por esta red llegó a 15 GB de footprint en un mini de
16 GB y tumbó la máquina dos veces el 20-sep-26 (la conv3d de PyTorch en CPU materializa el
im2col). En MPS son ~1 GB y 0,2 s. Pero MPS tiene fama de devolver NaN en silencio, y una malla
sutilmente mala es peor que ninguna: esto lo COMPRUEBA en vez de suponerlo, con un parche
pequeño (64³) que en CPU sí cabe.

Se salta si no hay pesos descargados o no hay MPS: es un test de equivalencia numérica, no un
freno de muro.
"""
import os
import subprocess
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV = os.path.join(os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode"),
                    ".venv-imagen", "bin", "python")
MODELO = os.path.expanduser(
    "~/.cache/btp/modelos/mama-mia/full_image_dce_mri_tumor_segmentation")

try:
    import torch  # noqa: F401
except ImportError:
    if os.path.exists(VENV) and os.environ.get("_MPS_REEXEC") != "1":
        sys.exit(subprocess.call([VENV, os.path.abspath(__file__)],
                                 env=dict(os.environ, _MPS_REEXEC="1")))
    print("SKIP: sin .venv-imagen")
    sys.exit(77)

import torch  # noqa: E402

if not os.path.isdir(MODELO):
    print("SKIP: pesos de MAMA-MIA no descargados (`visor3d.py modelo --descarga`)")
    sys.exit(77)
if not torch.backends.mps.is_available():
    print("SKIP: esta máquina no tiene MPS")
    sys.exit(77)

for _v in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ[_v] = "4"
torch.set_num_threads(4)
from nnunetv2.inference.predict_from_raw_data import nnUNetPredictor  # noqa: E402

fallos = []


def check(cond, desc):
    print("  %s %s" % ("✅" if cond else "❌", desc))
    if not cond:
        fallos.append(desc)


p = nnUNetPredictor(device=torch.device("cpu"), verbose=False, allow_tqdm=False)
p.initialize_from_trained_model_folder(MODELO, use_folds=(0,),
                                       checkpoint_name="checkpoint_final.pth")
net = p.network.eval()
x = torch.randn((1, 1, 64, 64, 64), generator=torch.Generator().manual_seed(20260920))
with torch.no_grad():
    a = net.cpu()(x)
    a = (a[0] if isinstance(a, (list, tuple)) else a).float()
    b = net.to("mps")(x.to("mps"))
    b = (b[0] if isinstance(b, (list, tuple)) else b).float().cpu()

check(not bool(torch.isnan(b).any()), "la GPU no devuelve NaN")
dif_logit = float((a - b).abs().max())
check(dif_logit < 1e-3, "logits iguales a menos de 1e-3 (%.1e)" % dif_logit)
dif_prob = float((torch.softmax(a, 1) - torch.softmax(b, 1)).abs().max())
check(dif_prob < 1e-4, "  probabilidades iguales a menos de 1e-4 (%.1e)" % dif_prob)
igual = float((a.argmax(1) == b.argmax(1)).float().mean()) * 100
check(igual == 100.0, "  el 100 %% de los vóxeles recibe la misma etiqueta (%.4f %%)" % igual)

print("\nVEREDICTO: %s" % ("TODO CORRECTO" if not fallos else "%d FALLOS" % len(fallos)))
sys.exit(1 if fallos else 0)
