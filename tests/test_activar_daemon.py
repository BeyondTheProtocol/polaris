#!/usr/bin/env python3
# test_activar_daemon.py - guardianes + reescritura de home al instalar (mejora #1). Mock TOTAL de
# launchd: nunca toca ~/Library/LaunchAgents real ni llama launchctl.
import os, sys, tempfile, plistlib, contextlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import activar_daemon as ad

_pass = 0
_fail = 0


def ok(cond, name):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  x %s" % name)


@contextlib.contextmanager
def _noop_lock(*a, **k):
    yield


class _FakeRun:
    """launchd de mentira. Simula lo que de verdad pasa: `bootstrap` CARGA el label y `bootout` lo
    descarga. Sin esto, el test no podía distinguir «el comando devolvió 0» de «el daemon está
    corriendo», que es justo la diferencia que dejó el lazo caído el 31-jul-26.

    `rc_bootstrap` fuerza que el bootstrap falle, para poder probar el reintento y el aviso.
    """

    def __init__(self, cargados=None, rc_bootstrap=0, carga_al_bootstrap=True):
        self.calls = []
        self.cargados = cargados if cargados is not None else set()
        self.rc_bootstrap = rc_bootstrap
        self.carga_al_bootstrap = carga_al_bootstrap

    def __call__(self, cmd, *a, **k):
        self.calls.append(cmd)
        rc = 0
        if "bootout" in cmd:
            self.cargados.discard(cmd[-1].split("/")[-1])
        elif "bootstrap" in cmd:
            rc = self.rc_bootstrap
            if rc == 0 and self.carga_al_bootstrap:
                import os as _os
                self.cargados.add(_os.path.basename(cmd[-1])[:-len(".plist")])

        class R:
            returncode = rc
            stdout = ""
            stderr = "Bootstrap failed: 5: Input/output error" if rc else ""
        return R()


def _wplist(path, label, wd, prog=None, out=None):
    body = {"Label": label, "WorkingDirectory": wd,
            "ProgramArguments": prog if prog is not None else [wd + "/.venv/bin/python", wd + "/tools/x.py"]}
    if out:
        body["StandardOutPath"] = out
    with open(path, "wb") as fh:
        plistlib.dump(body, fh)


def reescritura_tests():
    orig_home = os.environ.get("HOME")
    os.environ["HOME"] = "/Users/testhome"
    try:
        txt = "a /Users/titular/claudecode/x b /Users/polaris/claudecode/y"
        nuevo, cambios = ad._reescribir_home(txt)
        ok("/Users/titular/" not in nuevo and "/Users/polaris/" not in nuevo, "ambos homes conocidos -> $HOME local")
        ok(nuevo.count("/Users/testhome/") == 2, "las 2 rutas quedan en el home local")
        ok(len(cambios) == 2, "el diff registra los 2 prefijos reescritos")
        nuevo2, cambios2 = ad._reescribir_home(nuevo)
        ok(nuevo2 == nuevo and cambios2 == [], "idempotente: reescribir lo ya-local = no-op")
        n3, _ = ad._reescribir_home("/Users/ajeno/z /Users/titular/claudecode")
        ok("/Users/ajeno/z" in n3, "no reescribe /Users/<otro>/ desconocido (scope acotado)")
        d = tempfile.mkdtemp()
        p = os.path.join(d, "x.plist")
        _wplist(p, "com.btp.t", "/Users/titular/claudecode")
        rw, _ = ad._reescribir_home(open(p, encoding="utf-8").read())
        parsed = plistlib.loads(rw.encode("utf-8"))
        ok(parsed["WorkingDirectory"] == "/Users/testhome/claudecode", "el plist reescrito sigue siendo XML valido con el home local")
    finally:
        if orig_home is not None:
            os.environ["HOME"] = orig_home
        else:
            os.environ.pop("HOME", None)


def guardianes_tests():
    d = tempfile.mkdtemp()
    la = os.path.join(d, "LA")
    os.makedirs(la)
    o_LA, o_lock, o_lab, o_reg, o_run = ad.LA, ad._lock.lock, ad._labels_cargados, ad._registro, ad.subprocess.run
    ad.LA = la
    ad._lock.lock = _noop_lock
    fake = _FakeRun()
    ad.subprocess.run = fake
    try:
        pw = os.path.join(d, "w.plist")
        _wplist(pw, "com.btp.w", "/Users/x/.claude/worktrees/rama/claudecode")
        ad._labels_cargados = lambda: set()
        ad._registro = lambda: {}
        ok(ad.activar(pw) == 3, "plist con worktree -> guardian no-worktree (3)")

        pn = os.path.join(d, "n.plist")
        _wplist(pn, "com.btp.n", "/Users/titular/claudecode")
        ad._labels_cargados = lambda: {"com.btp.n"}
        ad._registro = lambda: {}
        ok(ad.activar(pn, reemplaza=False) == 4, "label ya cargado sin --reemplaza -> label-unico (4)")

        ad._labels_cargados = lambda: {"com.btp.gemelo"}
        ad._registro = lambda: {"com.btp.n": {"concern": "c"}, "com.btp.gemelo": {"concern": "c"}}
        ok(ad.activar(pn, reemplaza=False) == 5, "otro daemon activo con el mismo concern -> concern-unico (5)")

        ad._labels_cargados = lambda: set(fake.cargados)
        ad._registro = lambda: {}
        fake.calls = []
        ok(ad.activar(pn, dry=True) == 0, "--dry -> 0")
        ok(fake.calls == [], "--dry no llama a launchctl (ni bootstrap ni bootout)")
        ok(not os.path.exists(os.path.join(la, "com.btp.n.plist")), "--dry no escribe el destino")

        rc = ad.activar(pn, reemplaza=False)
        dest = os.path.join(la, "com.btp.n.plist")
        ok(rc == 0 and os.path.exists(dest), "install mockeado -> 0 y destino creado")
        cont = open(dest, encoding="utf-8").read()
        ok("/Users/titular/" not in cont, "el destino instalado ya NO tiene el home del portatil (reescrito)")
        ok(any("bootstrap" in c for c in fake.calls), "install llama a launchctl bootstrap")
        rc2 = ad.activar(pn, reemplaza=True)
        ok(rc2 == 0 and os.path.exists(dest + ".bak"), "reinstalar crea .bak del destino previo (rollback)")

        # ── Un tool de ENCENDIDO no puede apagar en silencio (31-jul-26) ───────────────────────
        # Con --reemplaza ya se hizo bootout: si el bootstrap falla y nos vamos, el daemon queda
        # DESCARGADO. Pasó de verdad: el dispatcher del lazo 24/7 estuvo ~30 s caído con un
        # «Bootstrap failed: 5: Input/output error», que es transitorio y se cura reintentando.
        avisos = []
        malo = _FakeRun(cargados={"com.btp.n"}, rc_bootstrap=1)
        ad.subprocess.run = malo
        ad._labels_cargados = lambda: set(malo.cargados)
        o_gritar = ad._gritar_si_quedo_apagado
        ad._gritar_si_quedo_apagado = lambda label, reemplaza: avisos.append((label, reemplaza))
        rc3 = ad.activar(pn, reemplaza=True)
        ok(rc3 == 6, "bootstrap que falla -> exit 6")
        ok(sum(1 for c in malo.calls if "bootstrap" in c) == 3, "reintenta el bootstrap 3 veces")
        ok(avisos == [("com.btp.n", True)], "y AVISA de que se quedo apagado tras el bootout")

        # bootstrap que dice OK pero no carga nada: verificar el EFECTO, no que el comando corrio.
        mentiroso = _FakeRun(cargados=set(), carga_al_bootstrap=False)
        ad.subprocess.run = mentiroso
        ad._labels_cargados = lambda: set(mentiroso.cargados)
        avisos.clear()
        ok(ad.activar(pn, reemplaza=True) == 6, "bootstrap que dice OK sin cargar -> exit 6")
        ok(avisos == [("com.btp.n", True)], "tambien avisa cuando el bootstrap miente")
        ad._gritar_si_quedo_apagado = o_gritar
    finally:
        ad.LA, ad._lock.lock, ad._labels_cargados, ad._registro, ad.subprocess.run = o_LA, o_lock, o_lab, o_reg, o_run


def main():
    reescritura_tests()
    guardianes_tests()
    print("RESULTADO activar_daemon (guardianes + reescritura home): %d OK, %d fallos" % (_pass, _fail))
    print("ACTIVAR_DAEMON EN VERDE" if _fail == 0 else "revisar fallos")
    return _fail


if __name__ == "__main__":
    sys.exit(main())
