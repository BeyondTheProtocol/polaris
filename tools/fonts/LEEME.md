# Las tipografías del design system (copia local)

Los `.woff` de esta carpeta son las **mismas** que ya publica helptitular.com. No se
instalaron en el sistema ni se bajaron de la red: se **copiaron** del build de la web

    /Users/polaris/projects/titular-{{APELLIDO}}-case/.output/public/_og-static-fonts/

Están aquí porque `.output/` es un artefacto de build y se borra en cualquier momento.

| Fichero | Familia | Para qué (DS §3) | Licencia |
|---|---|---|---|
| `Fraunces-600-normal.woff` | Fraunces | display: titulares y cifras grandes | OFL 1.1 |
| `Fraunces-400-normal.woff` | Fraunces | display en peso normal | OFL 1.1 |
| `JetBrains_Mono-400-normal.woff` | JetBrains Mono | eyebrows, etiquetas, datos | OFL 1.1 |

Ambas familias son **SIL Open Font License 1.1**, que permite incrustarlas en un
documento. `tools/anatomia_mapa.py` las mete en el HTML como `@font-face` en base64,
así que la pieza sigue siendo autocontenida: **sin red, sin CDN, sin Google Fonts**.

No se versionan: `*.woff` está en `.gitignore` (aquí no entran binarios). Si faltan, el
Mapa cae a los fallbacks que declara el propio DS (Georgia / mono del sistema) y avisa
por stderr en vez de fingir que la tipografía es la buena.

Comprobar o reinstalar:

    python3 tools/anatomia_mapa.py fuentes
    python3 tools/anatomia_mapa.py fuentes --copiar-de <ruta_al_build>
