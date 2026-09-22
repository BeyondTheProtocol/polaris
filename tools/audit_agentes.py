#!/usr/bin/env python3
"""tools/audit_agentes.py — ¿QUÉ AGENTE Y QUÉ SKILL SE USAN DE VERDAD, Y CON QUÉ MODELO?

Por qué existe (auditoría 14-jul-26): existía `audit_herramientas.py` (uso+salud del
arsenal) y `audit_comites.py` (registro), pero NADIE medía los 38 agentes ni las 14
skills. Consecuencia real: las 11 science-skills de DeepMind llevaban 10 días instaladas
con CERO usos —porque ningún charter las nombraba— y nadie se entero. Y el `model:` de
las fichas MIENTE para el lazo 24/7: el `--model` del plist pisa el frontmatter, asi que
Vega decia `sonnet` mientras corria en `haiku`.

Esto es el gemelo de `audit_herramientas.py` para agentes y skills. Determinista, sin
LLM, sin red, sin coste.

Uso:
  python3 tools/audit_agentes.py            # informe legible
  python3 tools/audit_agentes.py --json     # para el lazo
  python3 tools/audit_agentes.py --dias 30  # ventana (por defecto 30)
"""
import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta

REPO = os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode")
AGENTES = os.path.join(REPO, ".claude", "agents")
SKILLS = os.path.join(REPO, ".claude", "skills")
LA = os.path.expanduser("~/Library/LaunchAgents")

# los transcripts de las sesiones (donde se ve quien invoco a quien)
PROJ = os.path.expanduser("~/.claude/projects")


def _frontmatter(path):
    """(name, model, estado) del .md de un agente."""
    try:
        txt = open(path, encoding="utf-8", errors="replace").read(4000)
    except Exception:
        return None, None, None
    def g(k):
        m = re.search(r"^%s:\s*(.+)$" % k, txt, re.M)
        return m.group(1).strip().split("#")[0].strip() if m else None
    return g("name") or os.path.basename(path)[:-3], g("model"), g("estado")


def _modelo_real_en_el_lazo():
    """{agente: modelo} segun los plists INSTALADOS (que son los que mandan: el --model
    de la CLI pisa el frontmatter del .md)."""
    real = {}
    if not os.path.isdir(LA):
        return real
    for f in os.listdir(LA):
        if not (f.startswith("com.btp.") and f.endswith(".plist")):
            continue
        p = os.path.join(LA, f)
        def x(key):
            r = subprocess.run(["plutil", "-extract", "EnvironmentVariables." + key,
                                "raw", p], capture_output=True, text=True)
            return r.stdout.strip() if r.returncode == 0 else None
        ag, mod = x("BTP_AGENT"), x("BTP_MODEL")
        if ag and mod:
            real.setdefault(ag, set()).add(mod)
    return real


def _usos_daemon(dias):
    """Pasadas que hizo un agente COMO DAEMON, del registro de observabilidad.

    19-sep-2026 — el agujero que hacía inútil a este auditor: `_usos` solo mira los transcripts
    de sesión, donde aparece quien invoca a otro con `subagent_type`. Un agente que corre desde
    launchd NO deja rastro ahí, así que salía con CERO usos llevando meses trabajando a diario.
    Con ese cero se llegó a proponer retirar al `orquestador`, que corre cada mañana. Un medidor
    que solo ve una de las dos puertas no mide: engaña."""
    corte = datetime.now() - timedelta(days=dias)
    out = Counter()
    d = os.path.join(REPO, "tools", "state", "observabilidad")
    if not os.path.isdir(d):
        return out
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".jsonl"):
            continue
        ruta = os.path.join(d, fn)
        try:
            if datetime.fromtimestamp(os.path.getmtime(ruta)) < corte:
                continue
            with open(ruta, encoding="utf-8", errors="replace") as fh:
                for linea in fh:
                    try:
                        reg = json.loads(linea)
                    except ValueError:
                        continue
                    agente = reg.get("agente") or reg.get("agent")
                    if agente:
                        out[agente] += 1
        except OSError:
            continue
    return out


def _usos(dias):
    """Cuenta invocaciones reales de agentes y skills en los transcripts."""
    corte = datetime.now() - timedelta(days=dias)
    ag, sk = Counter(), Counter()
    if not os.path.isdir(PROJ):
        return ag, sk
    for root, _, files in os.walk(PROJ):
        for fn in files:
            if not fn.endswith(".jsonl"):
                continue
            p = os.path.join(root, fn)
            try:
                if datetime.fromtimestamp(os.path.getmtime(p)) < corte:
                    continue
                txt = open(p, encoding="utf-8", errors="replace").read()
            except Exception:
                continue
            for m in re.finditer(r'"subagent_type"\s*:\s*"([\w-]+)"', txt):
                ag[m.group(1)] += 1
            for m in re.finditer(r'"name"\s*:\s*"Skill".{0,120}?"skill"\s*:\s*"([\w_-]+)"',
                                 txt, re.S):
                sk[m.group(1)] += 1
    return ag, sk


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dias", type=int, default=30)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    usos_ag, usos_sk = _usos(a.dias)
    # Las dos puertas suman: invocado en sesión + pasadas como daemon.
    usos_daemon = _usos_daemon(a.dias)
    for agente, n in usos_daemon.items():
        usos_ag[agente] += n
    real = _modelo_real_en_el_lazo()

    agentes, incoherentes, huerfanos = [], [], []
    for f in sorted(os.listdir(AGENTES)) if os.path.isdir(AGENTES) else []:
        if not f.endswith(".md"):
            continue
        name, model, estado = _frontmatter(os.path.join(AGENTES, f))
        n = usos_ag.get(name, 0)
        mreal = sorted(real.get(name, [])) if name in real else []
        fila = {"agente": name, "model_ficha": model, "model_lazo": mreal,
                "estado": estado, "usos_%dd" % a.dias: n}
        agentes.append(fila)
        if mreal and model and model not in mreal:
            incoherentes.append(fila)
        if n == 0:
            huerfanos.append(fila)

    skills = []
    for s in sorted(os.listdir(SKILLS)) if os.path.isdir(SKILLS) else []:
        if s.startswith("_"):
            continue
        skills.append({"skill": s, "usos_%dd" % a.dias: usos_sk.get(s, 0)})

    if a.json:
        print(json.dumps({"agentes": agentes, "skills": skills,
                          "incoherentes": incoherentes,
                          "huerfanos": [h["agente"] for h in huerfanos]},
                         ensure_ascii=False, indent=2))
        return 0

    print("AUDIT DE AGENTES Y SKILLS — ventana: %d días\n" % a.dias)

    print("⚠️  EL MODELO DE LA FICHA MIENTE (el plist pisa el frontmatter):")
    if not incoherentes:
        print("   (ninguno — coherente)")
    for i in incoherentes:
        print("   · %-22s ficha dice %-7s → el lazo lo corre en %s"
              % (i["agente"], i["model_ficha"], ",".join(i["model_lazo"])))

    print("\n🕸️  AGENTES SIN USAR en %d días (¿acercan a NED? entonces ENCHUFAR, no retirar):" % a.dias)
    if not huerfanos:
        print("   (ninguno)")
    for h in huerfanos:
        print("   · %s" % h["agente"])

    print("\n🔬 SKILLS — uso real:")
    for s in sorted(skills, key=lambda x: -x["usos_%dd" % a.dias]):
        n = s["usos_%dd" % a.dias]
        marca = "  " if n else "🕸️"
        print("   %s %-42s %d" % (marca, s["skill"], n))

    print("\n📊 AGENTES más usados:")
    for x in sorted(agentes, key=lambda z: -z["usos_%dd" % a.dias])[:8]:
        print("   %-22s %3d" % (x["agente"], x["usos_%dd" % a.dias]))

    print("\nRESUMEN: %d agentes · %d skills · %d con modelo incoherente · %d sin usar"
          % (len(agentes), len(skills), len(incoherentes), len(huerfanos)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
