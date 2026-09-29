#!/usr/bin/env bash
#
# instalar_servidor.sh
# ====================
# Instalador TODO-EN-UNO para el servidor Linux (Ubuntu / Rocky Linux).
#
# Pensado para un Operador SIN conocimientos de Linux: deja el Collector
# corriendo AUTOMATICAMENTE cada 5 minutos (via cron + flock, como recomienda el
# README), sin tener que editar el crontab a mano.
#
# QUE HACE (en orden):
#   1. Comprueba que exista el archivo de configuracion .env (con credenciales).
#   2. Prepara el entorno virtual (.venv) e instala las dependencias.
#   3. Ejecuta una primera corrida de prueba (salvo que se pida omitir).
#   4. Programa la ejecucion automatica cada 5 minutos en el cron del usuario,
#      envuelta en flock para que no se solapen dos corridas.
#   5. Muestra como comprobar que quedo funcionando.
#
# USO (en el servidor, dentro de la carpeta del proyecto):
#     ./instalar_servidor.sh
#
# NOTA: NO requiere sudo. El cron se instala para el usuario actual (el que
# opera el Collector), que es lo recomendado (no usar root).
#
# Todos los mensajes van en espanol.

set -euo pipefail

# Situarse SIEMPRE en la carpeta donde vive este script (la del proyecto).
cd "$(dirname "$0")"
DIR_PROYECTO="$(pwd)"

echo "==================================================================="
echo " Instalador de phishing-msg-collector - Servidor Linux"
echo " Ejecucion automatica: cada 5 minutos (cron + flock)"
echo "==================================================================="

# ---------------------------------------------------------------------------
# 1. Verificar Python 3 y el modulo venv.
# ---------------------------------------------------------------------------
if ! command -v python3 >/dev/null 2>&1; then
    echo "ERROR: No se encontro Python 3." >&2
    echo "Accion sugerida: instalelo y reintente." >&2
    echo "  - Ubuntu/Debian:  sudo apt-get install -y python3 python3-venv" >&2
    echo "  - Rocky Linux:    sudo dnf install -y python3" >&2
    exit 1
fi
if ! python3 -c 'import ensurepip, venv' >/dev/null 2>&1; then
    VERSION_PY="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || echo "3")"
    echo "ERROR: Falta el modulo 'venv' de Python (necesario para crear el entorno)." >&2
    echo "Accion sugerida: instale el paquete y vuelva a ejecutar el instalador." >&2
    echo "  - Ubuntu/Debian:  sudo apt-get install -y python${VERSION_PY}-venv" >&2
    echo "    (si ese paquete no existe, pruebe: sudo apt-get install -y python3-venv)" >&2
    exit 1
fi
echo "[1/5] Python 3 y modulo venv disponibles."

# ---------------------------------------------------------------------------
# 2. Verificar que exista el archivo de configuracion .env.
# ---------------------------------------------------------------------------
if [ ! -f ".env" ]; then
    echo "ERROR: No se encontro el archivo de configuracion '.env'." >&2
    echo "Accion sugerida: cree la configuracion antes de instalar:" >&2
    echo "    cp .env.example .env" >&2
    echo "    chmod 600 .env" >&2
    echo "    nano .env   # complete TENANT_ID, CLIENT_ID, CLIENT_SECRET y MAILBOX" >&2
    exit 1
fi
echo "[2/5] Archivo de configuracion .env encontrado."

# ---------------------------------------------------------------------------
# 3. Preparar el entorno virtual (.venv) e instalar dependencias.
# ---------------------------------------------------------------------------
# Se valida que el venv este COMPLETO (activador presente); si quedo incompleto
# de un intento anterior, se recrea.
echo "[3/5] Preparando el entorno virtual (.venv) e instalando dependencias..."
if [ ! -f ".venv/bin/activate" ]; then
    if [ -d ".venv" ]; then
        echo "      Se encontro un .venv incompleto; se recreara."
        rm -rf .venv
    fi
    python3 -m venv .venv
fi
.venv/bin/pip install --quiet --upgrade pip || echo "      Aviso: no se pudo actualizar pip; se continua."
.venv/bin/pip install -r requirements.txt
echo "      Dependencias instaladas."

# ---------------------------------------------------------------------------
# 4. Primera corrida de prueba (salvo que se pida omitir).
# ---------------------------------------------------------------------------
# Con OMITIR_PRIMERA_CORRIDA=1 se salta (el cron hara la primera pasada solo).
if [ "${OMITIR_PRIMERA_CORRIDA:-0}" = "1" ]; then
    echo "[4/5] Se omite la primera corrida por peticion."
else
    echo "[4/5] Ejecutando una primera corrida de prueba..."
    .venv/bin/python main.py || echo "      Aviso: la primera corrida termino con avisos; revise el resumen."
fi

# ---------------------------------------------------------------------------
# 5. Programar la ejecucion automatica cada 5 minutos (cron + flock).
# ---------------------------------------------------------------------------
echo "[5/5] Programando la ejecucion automatica cada 5 minutos..."

mkdir -p logs
LOCK="/tmp/phishing-msg-collector.lock"
LINEA_CRON="*/5 * * * * /usr/bin/flock -n ${LOCK} ${DIR_PROYECTO}/ejecutar.sh >> ${DIR_PROYECTO}/logs/cron.log 2>&1"

chmod +x ejecutar.sh instalar_servidor.sh

# Se reescribe SOLO la linea de este Collector en el crontab del usuario,
# conservando cualquier otra tarea que ya tenga. Se identifica por la ruta del
# proyecto, para no duplicar la entrada si el instalador se corre otra vez.
CRON_ACTUAL="$(crontab -l 2>/dev/null || true)"
CRON_SIN_NUESTRA="$(printf '%s\n' "$CRON_ACTUAL" | grep -vF "${DIR_PROYECTO}/ejecutar.sh" || true)"
printf '%s\n%s\n' "$CRON_SIN_NUESTRA" "$LINEA_CRON" | grep -v '^$' | crontab -

echo "      Tarea programada en el cron del usuario '$(whoami)'."

# ---------------------------------------------------------------------------
# Resumen final.
# ---------------------------------------------------------------------------
echo "==================================================================="
echo " INSTALACION COMPLETA. El Collector correra cada 5 minutos."
echo "-------------------------------------------------------------------"
echo " Ver la tarea programada:"
echo "     crontab -l"
echo
echo " Ver el registro de las corridas automaticas:"
echo "     tail -f ${DIR_PROYECTO}/logs/cron.log"
echo
echo " Ver el estado del Collector (desde la base SQLite):"
echo "     .venv/bin/python main.py --status"
echo
echo " Detener la ejecucion automatica (quita la linea del cron):"
echo "     crontab -l | grep -vF '${DIR_PROYECTO}/ejecutar.sh' | crontab -"
echo "==================================================================="
