"""Imprime las tablas A-D y secundarias como agregados legibles (desde tablas.json / secundarias.json)."""
import json

from _comun import out


def _f(d):
    if isinstance(d, str):
        return d
    m = d.get("media_cp10k_positivas")
    s = f"{d['frac']*100:.1f}% [{d['ic95'][0]*100:.1f}-{d['ic95'][1]*100:.1f}] k={d['k']}/n={d['n']}"
    s += f" cp10k+={m:.2f}" if m else " cp10k+=—"
    if d.get("nota"):
        s += " (" + d["nota"] + ")"
    sp = d.get("sensib_solo_spliced")
    gl = d.get("sensib_GEM_limpio")
    s += f" | spliced {sp['frac']*100:.1f}%" if sp else " | spliced —"
    s += f" | GEMlimpio {gl['frac']*100:.1f}% (n={gl['n']})" if gl else ""
    if d.get("estabilidad_rango") is not None:
        s += f" | rango defs {d['estabilidad_rango']*100:.1f}pp"
    return s


def run(*a):
    T = json.load(open(out("tablas.json")))
    for t in ("higado", "hueso"):
        r = T[t]
        print(f"=== {t}: población {r['poblacion_usada']} n={r['n_malignas']} | consenso espec {r['n_consenso_espec']} | defs {r['n_por_definicion']}")
        for k, v in r.items():
            if ":" in k:
                print(f"  {k:14s} {_f(v)}")
        print("  B sondas específicas:")
        for g, v in r["B_sondas_especificas"].items():
            print("   ", g, v if isinstance(v, str) else f"{v['frac']*100:.1f}% [{v['ic95'][0]*100:.1f}-{v['ic95'][1]*100:.1f}] k={v['k']} ({v['n_sondas_especificas']} sondas)")
        if "modulos" in r:
            m = r["modulos"]
            print("  módulos:", {k: (round(v, 3) if isinstance(v, float) else v) for k, v in m.items()})
    print("D:", T["D_veredicto"])
