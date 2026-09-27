"""
main.py
-------
Punto de entrada del Collector. Solo se encarga de:

  - cargar la configuracion (config.py),
  - interpretar los argumentos de linea de comandos (CLI),
  - resolver la ventana de tiempo a consultar,
  - invocar al Collector,
  - mostrar un resumen y aplicar la politica de checkpoint.

No contiene logica de negocio grande (esa vive en collector.py).

Uso (ver README.md):
    python main.py                 # ventana automatica (desde la ultima revision)
    python main.py --today
    python main.py --hours 24
    python main.py --date 2026-09-22
    python main.py --dry-run
    python main.py --force
    python main.py --status
    python main.py --history
    python main.py --debug
    python main.py --help

RECORDATORIO: SOLO LECTURA. Nunca modifica el buzon.
"""

import argparse
import logging
import sys
from datetime import datetime, time, timedelta, timezone

from config import Config
from database import Database
from graph_client import GraphClient, GraphError
from collector import Collector
from utils import a_iso, ahora_utc, configurar_logging, desde_iso

logger = logging.getLogger(__name__)


def crear_parser():
    """Define los argumentos de la CLI (sin abreviaturas confusas)."""
    p = argparse.ArgumentParser(
        prog="phishing-msg-collector",
        description="Recolector READ ONLY de correos de phishing (Microsoft Graph).",
    )
    # Modos de ventana (mutuamente excluyentes).
    ventana = p.add_mutually_exclusive_group()
    ventana.add_argument("--today", action="store_true",
                         help="Revisar los correos del dia de hoy (UTC).")
    ventana.add_argument("--hours", type=int, metavar="N",
                         help="Revisar las ultimas N horas.")
    ventana.add_argument("--date", type=str, metavar="AAAA-MM-DD",
                         help="Revisar un dia especifico (UTC).")

    # Modos de operacion.
    p.add_argument("--dry-run", action="store_true",
                   help="Prueba sin efectos: no descarga, no crea EML, no modifica la base.")
    p.add_argument("--force", action="store_true",
                   help="Reprocesar aunque el adjunto ya exista en la base.")
    p.add_argument("--status", action="store_true",
                   help="Mostrar el estado (desde SQLite) y salir.")
    p.add_argument("--history", action="store_true",
                   help="Mostrar los ultimos registros y salir.")
    p.add_argument("--debug", action="store_true",
                   help="Mostrar mas detalle tecnico (incluye tracebacks).")
    return p


def resolver_ventana(args, cfg, base):
    """
    Devuelve (desde_dt, hasta_dt) segun el modo elegido.

    - --today: [inicio de hoy UTC, ahora].
    - --hours N: [ahora - N horas, ahora].
    - --date AAAA-MM-DD: [inicio del dia, fin del dia] (UTC).
    - (default): [ultima revision - LOOKBACK, ahora]; si no hay historial,
      [ahora - FIRST_RUN_HOURS, ahora].
    """
    ahora = ahora_utc()

    if args.today:
        inicio = datetime.combine(ahora.date(), time.min, tzinfo=timezone.utc)
        return inicio, ahora

    if args.hours is not None:
        if args.hours <= 0:
            _salir_error("El valor de --hours debe ser mayor que 0.")
        return ahora - timedelta(hours=args.hours), ahora

    if args.date:
        try:
            dia = datetime.strptime(args.date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            _salir_error("El formato de --date debe ser AAAA-MM-DD (ej: 2026-09-22).")
        inicio = datetime.combine(dia.date(), time.min, tzinfo=timezone.utc)
        fin = datetime.combine(dia.date(), time.max, tzinfo=timezone.utc)
        return inicio, fin

    # Modo automatico (para cron).
    ultima = desde_iso(base.obtener_ultima_revision())
    if ultima is None:
        return ahora - timedelta(hours=cfg.first_run_hours), ahora
    return ultima - timedelta(minutes=cfg.lookback_minutes), ahora


def _salir_error(mensaje):
    """Muestra un error entendible y termina con codigo 1."""
    print(f"\nERROR: {mensaje}\n")
    sys.exit(1)


def mostrar_status(base):
    """Imprime el estado desde SQLite (modo --status)."""
    s = base.obtener_status()
    ultima = s["ultima_revision"] or "(nunca)"
    print("=" * 60)
    print("ESTADO PHISHING MSG COLLECTOR")
    print("=" * 60)
    print("")
    print(f"Ultima revision exitosa : {ultima}")
    print(f"Elementos registrados   : {s['total']}")
    print(f"Descargados             : {s['descargados']}")
    print(f"Pendientes              : {s['pendientes']}")
    print(f"Errores                 : {s['errores']}")
    print(f"Conversiones correctas  : {s['conversiones_ok']}")
    print("=" * 60)


def mostrar_history(base):
    """Imprime los ultimos registros (modo --history)."""
    filas = base.obtener_historial(limite=20)
    print("Fecha             Asunto                              Estado")
    print("-" * 72)
    for f in filas:
        fecha = (f["received_datetime"] or "")[:16].replace("T", " ")
        asunto = (f["suspicious_subject"] or f["container_subject"] or "(sin asunto)")
        asunto = asunto[:34].ljust(34)
        estado = f["conversion_status"] or f["download_status"] or ""
        print(f"{fecha:16}  {asunto}  {estado}")


def main(argv=None):
    args = crear_parser().parse_args(argv)

    # Configuracion (valida obligatorios; puede terminar el programa).
    cfg = Config()

    # Logging en espanol (consola + archivo).
    configurar_logging(cfg.log_dir, cfg.log_level, debug=args.debug)

    base = Database(cfg.database_path)
    base.inicializar_base()

    try:
        # Modos que solo consultan SQLite y salen.
        if args.status:
            mostrar_status(base)
            return 0
        if args.history:
            mostrar_history(base)
            return 0

        logger.info("Inicio de ejecucion")
        desde_dt, hasta_dt = resolver_ventana(args, cfg, base)

        cliente = GraphClient(
            cfg.tenant_id, cfg.client_id, cfg.client_secret, cfg.mailbox,
            max_download_bytes=cfg.max_attachment_size_bytes,
        )
        collector = Collector(cfg, cliente, base)

        try:
            resumen, checkpoint_ok = collector.ejecutar(
                desde_dt, hasta_dt, dry_run=args.dry_run, force=args.force
            )
        except GraphError as exc:
            # Fallo total de Graph: NO se actualiza last_successful_check.
            logger.error("Fallo la consulta a Microsoft Graph: %s", exc)
            if args.debug:
                logger.exception("Detalle del error de Graph")
            print(f"\nERROR: {exc}\n")
            return 1

        # Politica de checkpoint: solo si NO es dry-run y el listado fue exitoso.
        if not args.dry_run and checkpoint_ok:
            base.actualizar_ultima_revision(a_iso(hasta_dt))

        # Resumen final.
        logger.info("==== RESUMEN ====")
        logger.info("Correos en la ventana        : %d", resumen.correos)
        logger.info("Adjuntos candidatos          : %d", resumen.adjuntos_candidatos)
        if args.dry_run:
            logger.info("(DRY-RUN) Adjuntos que se descargarian: %d", resumen.nuevos)
        else:
            logger.info("Adjuntos nuevos              : %d", resumen.nuevos)
            logger.info("Omitidos por dedup           : %d", resumen.omitidos_dedup)
            logger.info("Muestras guardadas           : %d", resumen.guardados)
            logger.info("EML generados                : %d", resumen.eml_ok)
            logger.info("Muestras ya observadas (hash): %d", resumen.ya_observadas)
            logger.info("Errores                      : %d", resumen.errores)
        logger.info("El buzon no fue modificado (SOLO LECTURA).")
        return 0

    except KeyboardInterrupt:
        print("\nInterrumpido por el usuario.")
        return 130
    except Exception as exc:  # noqa: BLE001
        # Error inesperado: mensaje claro; traceback solo con --debug.
        logger.error("Ocurrio un error inesperado: %s", exc)
        if args.debug:
            logger.exception("Detalle del error inesperado")
        print(f"\nERROR: {exc}\n")
        return 1
    finally:
        base.cerrar()


if __name__ == "__main__":
    sys.exit(main())
