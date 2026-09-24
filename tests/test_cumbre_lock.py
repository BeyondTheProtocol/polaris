#!/usr/bin/env python3
"""Tests de concurrencia para cumbre.py con lock."""

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tools.cumbre as cumbre


class TestCumbreLock(unittest.TestCase):
    """Tests que verifican que el lock previene pérdida de escrituras."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.tmpdir.name)
        
        self.orig_state_dir = cumbre.STATE_DIR
        self.orig_state_file = cumbre.STATE_FILE
        self.orig_lock_file = cumbre.LOCK_FILE
        self.orig_semilla_file = cumbre.SEMILLA_FILE
        
        cumbre.STATE_DIR = self.state_dir
        cumbre.STATE_FILE = self.state_dir / "cumbre.json"
        cumbre.LOCK_FILE = self.state_dir / "cumbre.lock"
        cumbre.SEMILLA_FILE = self.state_dir / "cumbre.semilla.json"

    def tearDown(self):
        cumbre.STATE_DIR = self.orig_state_dir
        cumbre.STATE_FILE = self.orig_state_file
        cumbre.LOCK_FILE = self.orig_lock_file
        cumbre.SEMILLA_FILE = self.orig_semilla_file
        self.tmpdir.cleanup()

    def test_dos_escrituras_concurrentes_no_pierden_datos(self):
        """Dos hilos que mutan campos distintos: ambos cambios sobreviven."""
        cumbre.init({"version": 1})
        
        def mutar_campo1():
            return cumbre.mutar({"campo1": "valor1"})
        
        def mutar_campo2():
            return cumbre.mutar({"campo2": "valor2"})
        
        with ThreadPoolExecutor(max_workers=2) as executor:
            f1 = executor.submit(mutar_campo1)
            f2 = executor.submit(mutar_campo2)
            
            r1 = f1.result()
            r2 = f2.result()
        
        estado_final = cumbre.obtener()
        self.assertIn("campo1", estado_final)
        self.assertIn("campo2", estado_final)
        self.assertEqual(estado_final["campo1"], "valor1")
        self.assertEqual(estado_final["campo2"], "valor2")

    def test_estado_borrado_no_se_rellena_con_semilla(self):
        """Estado borrado manualmente NO se recrea automáticamente (fail-closed)."""
        cumbre.init({"version": 1, "datos": "originales"})
        cumbre.borrar()
        
        with self.assertRaises(FileNotFoundError):
            cumbre.ensure()
        
        self.assertFalse(cumbre.STATE_FILE.exists())

    def test_ensure_con_estado_existente(self):
        """ensure() devuelve estado existente sin cambios."""
        cumbre.init({"version": 1, "datos": "test"})
        estado = cumbre.ensure()
        
        self.assertEqual(estado["version"], 1)
        self.assertEqual(estado["datos"], "test")

    def test_mutar_sin_estado_falla(self):
        """mutar() sin estado inicializado falla claramente."""
        with self.assertRaises(FileNotFoundError):
            cumbre.mutar({"campo": "valor"})

    def test_lock_timeout(self):
        """Lock con timeout si no se puede adquirir."""
        cumbre.init({})
        
        import fcntl
        lock_fd = open(cumbre.LOCK_FILE, "w")
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        
        try:
            with self.assertRaises(TimeoutError):
                cumbre.mutar({"campo": "valor"})
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            lock_fd.close()

    def test_escrituras_secuenciales_correctas(self):
        """Múltiples escrituras secuenciales preservan todos los cambios."""
        cumbre.init({})
        
        for i in range(10):
            cumbre.mutar({f"campo_{i}": f"valor_{i}"})
        
        estado = cumbre.obtener()
        for i in range(10):
            self.assertIn(f"campo_{i}", estado)
            self.assertEqual(estado[f"campo_{i}"], f"valor_{i}")


if __name__ == "__main__":
    unittest.main()
