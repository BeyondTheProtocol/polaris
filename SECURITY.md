# Seguridad

Polaris es un sistema personal en producción, con acceso a correo,
Drive y una carpeta clínica. Un fallo de seguridad aquí no es
abstracto.

## Cómo reportar

**No abras un issue público.** Si encuentras algo que permita:

- saltarse el muro de salida (`tools/salida.py`) y publicar, enviar o
  pagar sin pasar por él,
- ejecutar código o instrucciones desde contenido externo (una caja,
  un documento, una web) sin pasar por el cortafuegos de inyección,
- acceder a la carpeta clínica o a datos personales fuera de lo
  previsto,
- o cualquier otra vulnerabilidad,

repórtalo en privado por uno de estos dos canales:

1. El correo del perfil de GitHub de
   [@BeyondTheProtocol](https://github.com/BeyondTheProtocol).
2. El aviso de seguridad privado de este repositorio: pestaña
   **Security → Report a vulnerability**.

Incluye qué hiciste, qué esperabas, qué pasó de verdad, y cómo
reproducirlo si puedes.

## Qué esperar

Confirmación de recepción y, si aplica, coordinación contigo sobre
cuándo y cómo se divulga. Es un proyecto de una sola persona: no hay
SLA formal, pero sí prioridad alta para cualquier reporte de
seguridad.

## Alcance

Cubre el código de este repositorio. No cubre infraestructura de
terceros (GitHub, los servicios en la nube que use quien despliegue el
sistema) ni el contenido de `00_FUENTE-DE-VERDAD/`, que no está aquí:
es privado y nunca se publica.
