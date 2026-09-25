#!/usr/bin/env python3
"""Red educada para las tools de Beyond the Protocol (defensa anti-baneo).

Objetivo: portarse como un cliente legítimo ante las APIs → reintentos con backoff
exponencial + jitter, respetando el header Retry-After, y throttle por host. Así
evitamos falsos positivos de "bot" y bloqueos por rate-limit. (Ver
00_FUENTE-DE-VERDAD/04 · IA/Seguridad-Anti-Bloqueo-Cuentas-2026-06-20.md)
"""
import json
import random
import select
import sys
import time
import urllib.error
import urllib.request

RETRY_STATUS = {429, 500, 502, 503, 504, 529}


def _retry_after(err, default):
    try:
        ra = err.headers.get("Retry-After")
        if ra:
            return min(float(ra), 120.0)
    except Exception:
        pass
    return default


def with_retries(fn, *, tries=5, base=1.0, cap=60.0, on_status=RETRY_STATUS, deadline=None):
    """Ejecuta fn(); ante HTTPError reintentable o fallo de red, espera (Retry-After
    o backoff exponencial con jitter) y reintenta. Relanza el último error si agota.

    `deadline` (opcional, `time.monotonic()` absoluto): tope TOTAL entre todos los intentos y
    esperas. Sin él, 5 intentos de 180 s más backoff podían dejar una llamada colgada ~15 min
    (visto con NVIDIA el 22-sep-26). Si la espera ya no cabe antes del tope, se relanza el error
    en vez de dormir. Quien no lo pasa sigue exactamente igual que antes."""
    last = None
    for i in range(tries):
        try:
            return fn()
        except urllib.error.HTTPError as e:
            if e.code not in on_status or i == tries - 1:
                raise
            espera = _retry_after(e, min(cap, base * (2 ** i)) + random.uniform(0, base))
            if deadline is not None and time.monotonic() + espera >= deadline:
                raise
            time.sleep(espera)
            last = e
        except urllib.error.URLError as e:
            if i == tries - 1:
                raise
            espera = min(cap, base * (2 ** i)) + random.uniform(0, base)
            if deadline is not None and time.monotonic() + espera >= deadline:
                raise
            time.sleep(espera)
            last = e
    if last:
        raise last


def stream_chat(url, body, headers, *, timeout=300, tries=3):
    """POST a una API de chat-completions OpenAI-compatible con stream=True y
    ensambla el texto de la respuesta. Mantener la conexión VIVA (bytes fluyendo)
    evita el corte por inactividad (`RemoteDisconnected`) que sufren las
    generaciones largas en modo NO-streaming.

    Devuelve (texto, usage): `usage` es el dict de uso si la API lo manda en el
    último chunk (coste exacto), o {} si no. Solo se reintenta ESTABLECER la
    conexión; un corte a media transmisión devuelve el texto parcial ya recibido
    (mejor que perderlo todo). Lanza HTTPError/URLError si ni siquiera conecta —
    el llamante lo captura para enseñar el error de la API (401, 429, etc.)."""
    body = dict(body)
    body["stream"] = True
    data = json.dumps(body).encode()

    def _open():
        req = urllib.request.Request(url, data=data, headers=headers)
        return urllib.request.urlopen(req, timeout=timeout)

    resp = with_retries(_open, tries=tries)
    parts, usage = [], {}
    try:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                chunk = json.loads(payload)
            except Exception:
                continue
            if isinstance(chunk.get("usage"), dict):
                usage = chunk["usage"]
            choices = chunk.get("choices") or [{}]
            delta = (choices[0] or {}).get("delta") or {}
            piece = delta.get("content")
            if isinstance(piece, str):
                parts.append(piece)
    except Exception:
        pass  # corte a media transmisión → devolvemos lo acumulado
    finally:
        try:
            resp.close()
        except Exception:
            pass
    return "".join(parts), usage


_last = {}


def throttle(key, min_interval):
    """Garantiza al menos `min_interval` segundos entre llamadas con la misma `key`
    (host) dentro de este proceso. Úsalo en bucles sobre una API."""
    now = time.monotonic()
    delta = now - _last.get(key, 0.0)
    if delta < min_interval:
        time.sleep(min_interval - delta)
    _last[key] = time.monotonic()


def stdin_canalizado(espera=1.0):
    """Texto que llega por stdin (`echo x | tool.py`), o "" si no hay.

    POR QUÉ (22-sep-26): los clientes hacían `if not sys.stdin.isatty(): sys.stdin.read()`. Lanzados
    desde un agente (Bash de Claude Code, un subproceso, `&`), stdin no es una terminal pero tampoco
    se cierra nunca, y el `read()` se quedaba esperando PARA SIEMPRE. Parecía que el modelo se
    colgaba; era la herramienta leyendo una entrada que no iba a llegar. Ahora solo se lee si hay
    algo que leer (o fin de fichero) dentro de `espera` segundos.
    """
    try:
        if sys.stdin is None or sys.stdin.isatty():
            return ""
        listo, _, _ = select.select([sys.stdin], [], [], espera)
        return sys.stdin.read() if listo else ""
    except Exception:
        return ""
