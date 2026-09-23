#!/bin/bash
# =============================================================================
#  ejecutar.sh  -  Lanzador de phishing-msg-collector para cron (Linux)
# =============================================================================
#  - Establece umask 027 para que los archivos/directorios creados NO sean
#    accesibles por otros usuarios (permisos tipo 750/640).
#  - Se situa en la carpeta del proyecto.
#  - Activa el entorno virtual .venv y ejecuta main.py.
#
#  Uso manual:      ./ejecutar.sh
#  Hacer ejecutable: chmod +x ejecutar.sh
#
#  En cron se recomienda envolver con flock para evitar ejecuciones
#  concurrentes (ver README.md), por ejemplo cada 5 minutos:
#
#    */5 * * * * /usr/bin/flock -n /tmp/phishing-msg-collector.lock \
#      /opt/phishing-msg-collector/ejecutar.sh \
#      >> /opt/phishing-msg-collector/logs/cron.log 2>&1
# =============================================================================

set -euo pipefail

# Permisos seguros para lo que se cree durante la ejecucion.
umask 027

# Ir a la carpeta donde vive este script (raiz del proyecto), sin importar
# desde donde lo invoque cron.
cd "$(dirname "$0")"

# Activar el entorno virtual.
if [ ! -f ".venv/bin/activate" ]; then
    echo "ERROR: no existe .venv. Crealo con: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
    exit 1
fi
# shellcheck disable=SC1091
source .venv/bin/activate

# Ejecutar el Collector (modo automatico: ventana desde la ultima revision).
python main.py
