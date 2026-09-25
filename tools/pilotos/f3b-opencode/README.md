# Piloto F3-B — OpenCode enjaulado tras el borde

Valida que un orquestador abierto (OpenCode) NO puede saltarse el muro: su única
vía a un modelo es el gateway del borde (`tools/borde_gateway.py`), sin claves de nube.

## Reproducir

```bash
# 1) arranca el gateway (loopback + token), a demanda, EN MODO ENDURECIDO
cd ~/claudecode
python3 tools/borde_gateway.py serve --port 8799 --solo-no-sensible   # imprime la ruta del token
#   --solo-no-sensible (≡ BORDE_GW_NO_SENSIBLE=1): la jaula NUNCA recibe nada sensible

# 2) exporta el token para OpenCode (no se versiona en la config)
export BORDE_GATEWAY_TOKEN=$(cat tools/state/borde_gateway/token)

# 3) ejecuta OpenCode DESDE este directorio (usa opencode.json de aquí)
cd ~/claudecode/tools/pilotos/f3b-opencode
opencode models        # debe listar SOLO: borde/local-ollama
opencode run --model borde/local-ollama "Resume en una frase qué es un grafo dirigido."

# 4) apaga el gateway al terminar (es a demanda)
pkill -f "borde_gateway.py serve"
```

## La jaula

`opencode.json`:
- define un único provider `borde` apuntando a `http://127.0.0.1:8799/v1`;
- `disabled_providers` apaga TODO lo demás (incl. el provider `opencode` con sus
  modelos zen gratis, que llegarían a la nube sin pasar por el borde);
- la apiKey sale de `BORDE_GATEWAY_TOKEN` (no se escribe en el fichero).

Sin claves de nube en el entorno ni en `~/.local/share/opencode/auth.json`, el
único camino a cualquier modelo es el gateway.

## Por qué el gateway arranca con `--solo-no-sensible`

Por defecto, el gateway atiende lo sensible si va a un destino de confianza
(Claude/local) y solo da 403 cuando el único destino sería no confiable. Eso vale
para Polaris, pero **NO para esta jaula**: aquí enjaulamos a un orquestador abierto
(OpenCode) en el que NO confiamos. No queremos que ese agente vea jamás contenido
clínico/sensible — ni siquiera el que Claude podría responder legítimamente.

Con `--solo-no-sensible` activo, el gateway clasifica cada petición ANTES de
enrutar y, si sale sensible, la **rechaza con 403 muro_denied sea cual sea el
destino (incluido Claude)**, sin filtrar el contenido y sellando un `deny` en el
ledger. Endurece el muro: la jaula es un carril estrictamente NO-sensible. Lo
no-sensible pasa con normalidad (200). El flag es opt-in (default OFF), así que
el comportamiento de Polaris fuera de la jaula no cambia.

## Evidencia (23/6/26)

- Tarea no-clínica → `local-ollama`, $0.0000, ledger seq 608-609 (allow).
- Toda llamada del gateway queda sellada en `tools/state/borde/ledger-*.jsonl`
  (cadena íntegra) y en `tools/state/borde_gateway/log-*.jsonl` (metadatos).
- Canario (variante HGVS/HLA SINTÉTICA, sin PII): clasificado sensible. En modo
  `--solo-no-sensible` (el de la jaula) → **403 muro_denied AUNQUE Claude esté
  arriba y disponible**, sin fuga de contenido, con `deny` sellado en el ledger.
  (Sin el flag, el mismo canario iría a Claude cleared y solo daría 403 si el único
  destino fuese no confiable.) Una tarea limpia sigue dando 200 con el flag puesto.
  Cubierto por `tests/test_borde_gateway.py`.
