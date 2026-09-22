#!/usr/bin/env python3
"""tools/kb_embed.py — capa vectorial LOCAL para la búsqueda híbrida de kb.py.

Egress-0: el modelo (ONNX) vive en tools/models/multilingual-e5-small-onnx/, corre en CPU vía
onnxruntime, y NUNCA hace una petición de red en tiempo de consulta (la descarga fue un paso
ONE-TIME de instalación, ya hecha; ver DESCARGA más abajo). Nada de esto reemplaza el air-gap
de tools/borde.py: 'multilingual-e5-small' está en la allowlist EMBEDDINGS_LOCALES_OK por si
algún día un carril externo intenta invocarlo — este módulo en sí no llama a borde.py porque no
hay ningún egress que mediar (todo el cómputo es local, no hay "destino").

MODELO: intfloat/multilingual-e5-small, variante ONNX int8 (model_qint8_avx512_vnni.onnx,
~118 MB en disco; el nombre menciona VNNI/x86 pero es un grafo ONNX estándar — se probó y
corre bien en Apple Silicon vía CPUExecutionProvider). 384-dim, multilingüe (soporta ES).
E5 exige el prefijo "query: " / "passage: " en el texto (parte del entrenamiento del modelo,
no un capricho nuestro) — sin él la calidad de la similitud cae notablemente.

FALLBACK DURO: si onnxruntime/tokenizers/numpy no están instalados (p. ej. el venv por
defecto del repo, que NO los trae — solo .venv-embed los tiene) o el modelo no está
descargado, `disponible()` devuelve False y kb.py debe degradar a FTS5 puro. Ninguna función
de este módulo lanza fuera de _load() — todo detrás de try/except, fail-soft.

DESCARGA (one-time, YA hecha para esta rama — no la repitas en runtime):
  huggingface_hub.hf_hub_download('intfloat/multilingual-e5-small', 'onnx/model_qint8_avx512_vnni.onnx', ...)
  + tokenizer.json / sentencepiece.bpe.model / config.json / special_tokens_map.json / tokenizer_config.json
  → tools/models/multilingual-e5-small-onnx/
"""
import os

ROOT = os.path.dirname(os.path.abspath(__file__))
MODEL_DIR = os.path.join(ROOT, "models", "multilingual-e5-small-onnx")
MODEL_FILE = os.path.join(MODEL_DIR, "model_qint8_avx512_vnni.onnx")
TOKENIZER_FILE = os.path.join(MODEL_DIR, "tokenizer.json")
DIM = 384
MAX_LEN = 512

_session = None
_tokenizer = None
_import_error = None


def _load():
    """Carga perezosa (una vez por proceso) de sesión ONNX + tokenizer. Fail-soft: cualquier
    fallo (import ausente, fichero de modelo ausente, ONNX corrupto) deja _session=None y
    _import_error con el motivo; nunca propaga la excepción a los callers de disponible()."""
    global _session, _tokenizer, _import_error
    if _session is not None or _import_error is not None:
        return
    try:
        if not (os.path.exists(MODEL_FILE) and os.path.exists(TOKENIZER_FILE)):
            raise FileNotFoundError("modelo de embeddings no descargado en " + MODEL_DIR)
        import onnxruntime as ort
        from tokenizers import Tokenizer
        sess = ort.InferenceSession(MODEL_FILE, providers=["CPUExecutionProvider"])
        tok = Tokenizer.from_file(TOKENIZER_FILE)
        tok.enable_truncation(max_length=MAX_LEN)
        tok.enable_padding()
        _session, _tokenizer = sess, tok
    except Exception as e:  # noqa: BLE001 — fallback duro, ver docstring del módulo
        _import_error = str(e)


def disponible():
    """True si la capa vectorial puede usarse (modelo cargado y operativo)."""
    _load()
    return _session is not None


def _mean_pool(hidden, mask):
    import numpy as np
    m = mask[:, :, None].astype("float32")
    summed = (hidden * m).sum(axis=1)
    counts = m.sum(axis=1)
    counts[counts < 1e-9] = 1e-9
    return summed / counts


def embed_texts(texts, prefix="passage: "):
    """[str] -> np.ndarray float32 (n, 384), normalizado L2 (para que el coseno sea un simple
    producto punto). None si la capa vectorial no está disponible (caller debe comprobar
    disponible() antes, o manejar None). prefix: 'query: ' al buscar, 'passage: ' al indexar
    (convención de entrenamiento de e5 — mezclar los prefijos degrada la similitud)."""
    if not disponible() or not texts:
        return None
    import numpy as np
    encs = _tokenizer.encode_batch([prefix + t for t in texts])
    maxlen = max(len(e.ids) for e in encs) if encs else 1
    ids = np.array([e.ids + [0] * (maxlen - len(e.ids)) for e in encs], dtype="int64")
    mask = np.array([e.attention_mask + [0] * (maxlen - len(e.attention_mask)) for e in encs], dtype="int64")
    typ = np.zeros_like(ids)
    out = _session.run(None, {"input_ids": ids, "attention_mask": mask, "token_type_ids": typ})[0]
    pooled = _mean_pool(out, mask)
    norms = np.linalg.norm(pooled, axis=1, keepdims=True)
    norms[norms < 1e-9] = 1e-9
    return (pooled / norms).astype("float32")


def embed_query(text):
    """Atajo: embebe UNA query (prefijo 'query: '). Devuelve np.ndarray (384,) o None."""
    v = embed_texts([text], prefix="query: ")
    return v[0] if v is not None else None
