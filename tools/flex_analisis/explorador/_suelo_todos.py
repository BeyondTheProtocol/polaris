"""Suelo ambiental y referencia emparejada para TODOS los genes del explorador, no solo los 77 de la web.

Por qué existe: el plan «Loupe público» (aprobado por {{TITULAR}} el 17-oct-2026) abre los 18.132 genes del
explorador a científicos. El comité médico puso una condición: ningún gen sale sin su suelo ambiental y su
referencia emparejada, porque sin suelo el «% detectado» se lee como positividad. Hasta ahora `exporta.py`
los calculaba solo para GENES_WEB.

Cablearlo NO son unas líneas: el contrato de hoy no tiene sitio para genes × grupos (VAR_COLS cerrado,
UNS_KEYS en allow-list, validador fail-closed, contrato web sin campo). Hay que ampliar `_contrato.py`, su
validador y sus mutantes, y decidir el destino (columnas de var o tabla aparte que el navegador carga una vez).

Es puro (numpy + scipy.sparse), sin rutas ni datos: lo llama `exporta.py` con lo que ya tiene en memoria y lo
prueba `tests/test_flex_suelo_todos.py` con un fixture sintético. La fórmula es LA MISMA que el bucle de
`exporta.py` (definición única del cierre), aplicada gen a gen con las mismas operaciones en el mismo orden,
así que para los genes de la web el resultado es idéntico bit a bit tras redondear. `coincide_con_web`
lo comprueba (fail-closed) y `valida_completo` impide que salga un gen sin suelo.
"""
import math

import numpy as np

ND_SUELO, ND_REF = 3, 4  # los mismos redondeos que exporta.py (_r(…, 3) y _r(…, 4))


def _r(x, nd):
    """Como exporta._r: round() de Python sobre float; None si no es finito."""
    x = float(x)
    if math.isnan(x) or math.isinf(x):
        return None
    return round(x, nd)


def calcula(X, p_amb, rho, umi, delta_adn, nu, grupo, g_ids, ref, w, excl_sin_sonda):
    """Suelo por gen y grupo, y referencia emparejada por gen, para todos los genes de X.

    X: matriz células × genes (scipy.sparse). Los demás argumentos, los mismos objetos que usa el bucle de
    `exporta.py` en ese punto. Devuelve un dict con:
      grupos      lista de g_ids (orden de las columnas de `suelo`)
      suelo       array genes × grupos (float; NaN en genes excluidos)
      referencia  array genes (float; NaN en genes excluidos)
      excluidos   índices de genes sin sonda en el modo (no se calculan)
    """
    n_cel, n_gen = X.shape
    for nombre, v, n in (("p_amb", p_amb, n_gen), ("nu", nu, n_gen), ("excl_sin_sonda", excl_sin_sonda, n_gen),
                         ("umi", umi, n_cel), ("delta_adn", delta_adn, n_cel), ("grupo", grupo, n_cel),
                         ("ref", ref, n_cel)):
        if len(v) != n:
            raise ValueError("%s: longitud %d, se esperaba %d" % (nombre, len(v), n))
    if len(w) != int(np.asarray(ref).sum()):
        raise ValueError("w: un peso por célula de referencia")
    Xc = X.tocsc()
    mascaras = [(np.asarray(grupo) == gid) for gid in g_ids]
    umi_g = [umi[m] for m in mascaras]
    adn_g = [delta_adn[m] for m in mascaras]
    hay = [bool(m.any()) for m in mascaras]
    den = max(w.sum(), 1e-12)
    suelo = np.full((n_gen, len(g_ids)), np.nan)
    refd = np.full(n_gen, np.nan)
    excluidos = np.where(np.asarray(excl_sin_sonda, bool))[0]
    for j in range(n_gen):
        if excl_sin_sonda[j]:
            continue
        for k in range(len(g_ids)):
            if not hay[k]:
                suelo[j, k] = 0.0
                continue
            lam = p_amb[j] * rho * umi_g[k] + adn_g[k] * nu[j]
            suelo[j, k] = _r(np.mean(1 - np.exp(-lam)), ND_SUELO)
        v = np.asarray(Xc[:, j].todense()).ravel().astype(float)
        refd[j] = _r(np.sum(w * (v[ref] > 0)) / den, ND_REF)
    return {"grupos": list(g_ids), "suelo": suelo, "referencia": refd, "excluidos": [int(i) for i in excluidos]}


def coincide_con_web(res, simb, suelo_web, ref_web):
    """Lista de discrepancias entre `calcula` y los dicts del bucle de la web (gen → {grupo: suelo}, gen → ref).
    Vacía = el cálculo extendido reproduce exactamente lo que ya validó el acta para los genes de la web."""
    idx = {s: i for i, s in enumerate(simb)}
    col = {g: k for k, g in enumerate(res["grupos"])}
    malos = []
    for g, por_grupo in suelo_web.items():
        j = idx.get(g)
        if j is None:
            malos.append("%s: no está en var" % g)
            continue
        for gid, val in por_grupo.items():
            if gid not in col or res["suelo"][j, col[gid]] != val:
                malos.append("%s/%s: suelo %r frente a %r" % (g, gid, None if gid not in col else res["suelo"][j, col[gid]], val))
        if res["referencia"][j] != ref_web.get(g):
            malos.append("%s: referencia %r frente a %r" % (g, res["referencia"][j], ref_web.get(g)))
    return malos


def valida_completo(res, n_gen):
    """Fail-closed: todo gen no excluido tiene suelo en cada grupo y referencia; los excluidos, NaN."""
    excl = set(res["excluidos"])
    s, r = res["suelo"], res["referencia"]
    if s.shape != (n_gen, len(res["grupos"])) or r.shape != (n_gen,):
        raise ValueError("forma inesperada: suelo %s, referencia %s" % (s.shape, r.shape))
    falta = [j for j in range(n_gen) if j not in excl and (np.isnan(s[j]).any() or np.isnan(r[j]))]
    if falta:
        raise ValueError("%d genes sin suelo o sin referencia (p. ej. índice %d)" % (len(falta), falta[0]))
    sobra = [j for j in excl if not (np.isnan(s[j]).all() and np.isnan(r[j]))]
    if sobra:
        raise ValueError("%d genes excluidos con valor (p. ej. índice %d)" % (len(sobra), sobra[0]))
    fuera = (s[~np.isnan(s)] < 0).any() or (s[~np.isnan(s)] > 1).any() or (r[~np.isnan(r)] < 0).any()
    if fuera:
        raise ValueError("suelo o referencia fuera de [0, 1]")
    return True
