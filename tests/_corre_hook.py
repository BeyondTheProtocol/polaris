#!/usr/bin/env python3
"""tests/_corre_hook.py — lanza un hook COMO PROCESO con las costuras de prueba puestas.

POR QUÉ EXISTE (13-oct-26). `permiso_envio` ya no abre cualquier transcript: tiene que estar en
`~/.claude/projects` y llamarse `<sesión>.jsonl`. Los tests usan transcripts temporales, y la
costura no puede ser una variable de entorno que lea el código de producción: las tools las
lanza el agente con el entorno que quiera, y con `X=/tmp python3 tools/web_novedad.py`
volvería a valer un transcript escrito a mano. Así que la costura es un ATRIBUTO del módulo
(`permiso_envio.RAIZ_TRANSCRIPTS_TEST`, como `CLAVE_TEST`), que solo se puede poner en proceso.

Este lanzador es quien lo pone para los tests que corren el hook entero por subproceso. Las
variables `BTP_TEST_*` las lee ESTE fichero, que vive en tests/; ni los hooks ni las tools las
miran. Quien quisiera usarlo para saltarse el freno tendría que ejecutar a mano un hook, y eso
no decide nada: la decisión que cuenta es la del hook que lanza el harness.

Uso:  python3 tests/_corre_hook.py <ruta/al/hook.py>      (stdin = el payload del hook)
"""
import os
import runpy
import sys


def main():
    hook = os.path.abspath(sys.argv[1])
    raiz = os.path.dirname(os.path.dirname(os.path.dirname(hook)))      # <raíz>/.claude/hooks/x.py
    sys.path.insert(0, os.path.join(raiz, "tools"))
    import permiso_envio as P
    if not P._state_aislado():
        # Con el estado de casa base esto sería lanzar a mano el hook que abre el permiso.
        sys.exit("tests/_corre_hook.py solo corre con el estado aislado (BTP_STATE_DIR temporal)")
    if os.environ.get("BTP_TEST_RAIZ_TRANSCRIPTS"):
        P.RAIZ_TRANSCRIPTS_TEST = os.environ["BTP_TEST_RAIZ_TRANSCRIPTS"]
    # La pública de un firmante de prueba y el modo («hmac» | «ed25519»): así cada batería
    # corre en SU modo, esté o no fijada `permiso_envio.PUBLICA` en el árbol.
    if os.environ.get("BTP_TEST_PUBLICA"):
        P.PUBLICA_TEST = os.environ["BTP_TEST_PUBLICA"]
    if os.environ.get("BTP_TEST_MODO"):
        P.MODO_TEST = os.environ["BTP_TEST_MODO"]
    sys.argv = [hook] + sys.argv[2:]
    runpy.run_path(hook, run_name="__main__")


if __name__ == "__main__":
    main()
