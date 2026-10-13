#!/usr/bin/env python3
"""Trozo 1 de 3 de test_laminillas.py (ver tests/_trozos.py): corre SOLO su parte del original, sin copiar ningún test."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _trozos  # noqa: E402

_trozos.corre(__file__)
