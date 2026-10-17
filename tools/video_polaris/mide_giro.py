"""¿Dónde tropieza el giro de un visor 3D? Mide cuánto cambia la imagen de un fotograma al siguiente en la secuencia
(public/<dir>/0001.jpg…): un giro fluido da un cambio parejo; un fotograma repetido da casi cero y el siguiente, el doble;
y el salto del último al primero dice si el bucle cierra. Uso: python mide_giro.py <dir> [<dir> ...]"""
import os, sys
import numpy as np
from PIL import Image

PUB = "/Users/polaris/claudecode/.claude/worktrees/video-orellana-v9/tools/video_polaris/remotion/public"
for d in sys.argv[1:]:
    fs = sorted(x for x in os.listdir(os.path.join(PUB, d)) if x.endswith(".jpg"))
    ims = [np.asarray(Image.open(os.path.join(PUB, d, f)).convert("L").resize((240, 135)), dtype=np.float32) for f in fs]
    dif = np.array([np.abs(ims[(k + 1) % len(ims)] - ims[k]).mean() for k in range(len(ims))])
    med = float(np.median(dif[:-1]))
    raros = [(k + 1, round(float(v), 2)) for k, v in enumerate(dif[:-1]) if v < 0.35 * med or v > 2.2 * med]
    print(f"{d}: {len(fs)} fotogramas · cambio típico {med:.2f} · del último al primero {dif[-1]:.2f} ({dif[-1] / med:.1f}× lo típico)")
    print(f"   fotogramas raros (nº, cambio al siguiente): {raros[:40]}{' …' if len(raros) > 40 else ''}  [{len(raros)} en total]")
    # ¿cuánto de vuelta da? el fotograma más parecido al primero, lejos del principio
    lejos = [(float(np.abs(ims[k] - ims[0]).mean()), k + 1) for k in range(len(ims) // 3, len(ims))]
    print(f"   el más parecido al primero, pasado el primer tercio: nº {min(lejos)[1]} (diferencia {min(lejos)[0]:.2f}; el siguiente al primero difiere {dif[0]:.2f})")
