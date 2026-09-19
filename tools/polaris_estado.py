#!/usr/bin/env python3
"""polaris_estado.py — termómetro de configuración de Polaris (0-100).

Determinista (sin LLM). Mezcla señales AUTOMÁTICAS (launchd cargadas, venvs, tools,
HALT, procesos) con un CHECKLIST manual editable (polaris_estado.checklist.json) para
lo que no se puede medir solo (claves rotadas, accesos/APIs, backup físico, energía).

Uso:
  python3 tools/polaris_estado.py            # imprime la barra
  python3 tools/polaris_estado.py --send     # además se la manda a {{TITULAR}} por Telegram
                                             # (vía salida.report_to_titular; respeta HALT)
                                             # si llega a 100 → report_to_titular (una fiesta NO es un código rojo)

Para actualizar lo manual: edita polaris_estado.checklist.json (pon true lo que ya esté hecho).
"""
import os, sys, json, glob, subprocess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _casa import casa_base  # noqa: E402

# Casa base SIEMPRE, no el árbol desde el que se llame. Esto MIDE el sistema vivo (launchd, venvs,
# tools, HALT, checklist), así que derivarlo de `__file__` daba una nota de un árbol que no es el
# que corre 24/7: el 12-sep-2026 dio **93/100 desde un worktree y 98/100 desde casa base, el mismo
# segundo**, sin avisar de nada. Una métrica que miente es peor que no tenerla.
ROOT = casa_base()
TOOLS = os.path.join(ROOT, "tools")
sys.path.insert(0, TOOLS)  # para poder importar salida sin depender del cwd
CHECKLIST = os.path.join(TOOLS, "polaris_estado.checklist.json")


def _exists(rel):
    return os.path.exists(os.path.join(ROOT, rel))


def _halt():
    paths = [os.path.expanduser("~/.btp.HALT"), os.path.join(ROOT, ".HALT")]
    return any(os.path.exists(p) for p in paths)


def _proc(name):
    try:
        return subprocess.run(["pgrep", "-f", name], capture_output=True).returncode == 0
    except Exception:
        return False


def _keychain(svc):
    """¿Existe la clave en el Llavero? (no la lee, solo comprueba presencia)."""
    try:
        return subprocess.run(["security", "find-generic-password", "-s", svc],
                              capture_output=True, timeout=5).returncode == 0
    except Exception:
        return False


def _home(rel):
    return os.path.exists(os.path.expanduser(rel))


# Rutinas APARCADAS / no-MVP: existen como plist pero NO cuentan para el % de configuración
# (decisión de {{TITULAR}}, 22/6). Viven en el Backlog-Mejoras, no en el MVP. p.ej. la vía Meta de
# Instagram está bloqueada por verificación y va a backlog (los DMs ya los cubre ig_inbox.py).
PARKED_ROUTINES = {
    "com.btp.instagram",
    # X / redes: aparcados a propósito (decisión de {{TITULAR}} 28/6). El monitor de redes es
    # on-demand, NO daemon 24/7 → que B1 (vigía de roster) y el termómetro NO los marquen
    # como caídos. Reactivar = quitar de aquí + cargar el plist.
    "com.btp.x-mentions", "com.btp.x-dms", "com.btp.x-dms-watch",
    "com.btp.x-radar", "com.btp.x-guardados", "com.btp.x-centinela",
}

# Distinto de aparcar: esto es de OTRA MAQUINA. El repo es union del Air y el mini, asi que
# lleva plists que en el mini no pueden cargarse nunca (ni deben). Contarlos como «rutina
# caida» hacia que el termometro se quedase clavado en 99/100 sin que nadie pudiera arreglarlo,
# y un indicador que nunca llega a 100 acaba ignorandose. Se descuentan del TOTAL, no se
# marcan como caidos. Detectado el 2-sep-2026 buscando por que faltaba ese 1%.
DE_OTRA_MAQUINA = {
    # Las DOS patas del sync del playbook son del lado del Air. El 2-sep-2026 se encendio
    # com.btp.sync-playbook aqui para llegar al 100 y corrio limpio... sin hacer nada: «ssh:
    # Could not resolve hostname air». El mini NO tiene ningun Host en ~/.ssh/config, y no es
    # un olvido: el pull lo inicia SIEMPRE el Air porque el mini es fijo y el Air tiene IP
    # cambiante y suele estar dormido (un servidor no llama a un cliente que no sabe donde
    # esta). Dejarlo encendido serian dos ejecuciones vacias por hora, para siempre, y un 100
    # comprado con un daemon que no puede funcionar. Se descuenta, no se finge.
    "com.btp.sync-playbook-air",
    "com.btp.sync-playbook",
}
PARKED_ROUTINES = PARKED_ROUTINES | DE_OTRA_MAQUINA


def _launchd():
    try:
        out = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=10).stdout
    except Exception:
        out = ""
    loaded = {ln.split("\t")[-1].strip() for ln in out.splitlines()
              if ln.split("\t")[-1].strip().startswith("com.btp.")} - PARKED_ROUTINES
    plists = [p for p in glob.glob(os.path.join(TOOLS, "launchd", "*.plist"))
              if os.path.basename(p)[:-len(".plist")] not in PARKED_ROUTINES]
    total = len(plists) or 1
    return min(len(loaded) / total, 1.0), len(loaded), total


def _load_checklist():
    try:
        with open(CHECKLIST, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def compute():
    chk = _load_checklist()

    # Auto-detección: lo que SÍ se puede medir solo (Llavero, sesiones guardadas).
    # Así no depende de que yo marque el checklist a mano y nunca queda desfasado.
    auto = {
        "api_nvidia": _keychain("btp-nvidia-api"),
        "api_perplexity": _keychain("btp-perplexity-api"),
        "api_youtube": _keychain("btp-youtube-api"),
        "acceso_ig": _home("~/.agent-browser/ig_state.json"),
        "acceso_x_privado": _home("~/.agent-browser/x_state.json"),
        # backup: el USB montado + repo restic = la copia está operativa (si se
        # desenchufa el USB, vuelve a false solo, que es lo correcto).
        "backup_usb": os.path.exists("/Volumes/POLARIS-BACKUP/restic/config"),
    }

    def done(key, default=False):
        # verdadero si lo detectamos solos O si está marcado a mano en el checklist
        return bool(auto.get(key) or chk.get(key, default))

    def m(key, default=False):
        return 1.0 if done(key, default) else 0.0

    ld_ratio, ld_loaded, ld_total = _launchd()
    venv = sum(_exists(v) for v in [".venv", ".venv-biomcp", ".venv-cbioportal"]) / 3.0
    halt_ok = 0.0 if _halt() else 1.0

    cats = [
        ("Seguridad y muro", 25, [
            1.0 if _exists("tools/codigo_rojo.py") else 0.0,
            1.0 if _exists("tools/audit_constelacion.py") else 0.0,
            halt_ok,
            m("claves_rotadas"),
            m("muro_parche_commit"),
        ]),
        ("Infraestructura base", 15, [
            venv,
            1.0 if _exists(".mcp.json") else 0.0,
            1.0 if (_exists("tools/kb.py") and _exists("tools/coste.py")) else 0.0,
            # stitch_mcp_ok: APARCADO (bug $defs upstream, no-NED) → Backlog-Mejoras, fuera del MVP.
        ]),
        ("Rutinas 24/7", 20, [ld_ratio]),
        ("APIs y logins", 20, [
            m("api_nvidia"), m("api_perplexity"), m("api_youtube"),
            m("acceso_x_privado"), m("acceso_ig"), m("google_service_account"),
        ]),
        ("Continuidad y respaldo", 10, [
            1.0 if _proc("caffeinate") else 0.0,
            m("remoto_tailscale", True),
            m("backup_usb"),
            m("energia_sudo"),
            m("backup_clave_externa"),  # clave del restic guardada FUERA de Polaris
        ]),
        ("Constelación y cajas", 10, [
            1.0 if _exists("tools/audit_constelacion.py") else 0.0,
            m("cajas_commit"),
            m("caja_donaciones_borrador", True),
        ]),
    ]

    rows, acc, tw = [], 0, 0
    for label, w, sigs in cats:
        pct = round(100 * sum(sigs) / len(sigs))
        rows.append((label, pct, w))
        acc += pct * w
        tw += w
    overall = round(acc / tw)

    pend_keys = ["claves_rotadas", "api_nvidia", "api_perplexity", "api_youtube",
                 "acceso_x_privado", "acceso_ig", "google_service_account",
                 "backup_usb", "energia_sudo", "backup_clave_externa",
                 "muro_parche_commit", "cajas_commit"]
    pend = [k for k in pend_keys if not done(k)]
    return overall, rows, (ld_loaded, ld_total), pend


def bar(pct, width=20):
    fill = round(pct / 100 * width)
    return "█" * fill + "░" * (width - fill)


def render():
    overall, rows, (ld_loaded, ld_total), pend = compute()
    out = ["📊 Polaris — configuración: %d/100" % overall, bar(overall, 22) + "  %d%%" % overall, ""]
    for label, pct, _w in rows:
        out.append("%-22s %3d  %s" % (label, pct, bar(pct, 12)))
    out.append("")
    out.append("Rutinas: %d/%d launchd cargadas." % (ld_loaded, ld_total))
    if pend:
        out.append("Pendiente: " + ", ".join(pend))
    return overall, "\n".join(out)


def main():
    overall, report = render()
    print(report)
    if "--send" in sys.argv:
        try:
            import salida
            # Una celebración NO es un código rojo (ver salida.alerta_critica): ese canal atraviesa
            # el HALT y el silencio nocturno, y está reservado a que algo vaya MUY mal.
            if overall >= 100:
                salida.report_to_titular("🎉 ¡Polaris al 100%! Configuración completa.\n\n" + report,
                                        fuente="polaris-estado")
            else:
                salida.report_to_titular(report, fuente="polaris-estado")
        except Exception as e:
            print("[send] no se pudo enviar: %s" % e, file=sys.stderr)


if __name__ == "__main__":
    main()
