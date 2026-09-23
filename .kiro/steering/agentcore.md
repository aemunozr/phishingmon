---
inclusion: always
---

# Preparacion para Amazon Bedrock AgentCore

**No implementar AgentCore todavia.** Este documento define como disenar hoy para
poder migrar manana con el menor esfuerzo, reemplazando infraestructura sin
reescribir la logica central.

## Idea central

```
HOY:                          MANANA:
Linux + Cron                  AWS + AgentCore
SQLite                        almacenamiento persistente
Filesystem                    almacenamiento de objetos
OpenCode                      MCP / herramientas
```

El objetivo es **reemplazar infraestructura, no reescribir la logica**. Para eso
se mantienen limites claros entre modulos (buenos contratos), no sobreingenieria.

## Separacion Collector / Analyzer

- El **Collector** (este proyecto) recolecta y prepara muestras. Termina dejando
  `analysis_status=PENDIENTE` y un marcador `READY` por muestra.
- El **Analyzer** (futuro) analiza. **No** debe necesitar Microsoft Graph,
  credenciales, cron, CLI, SQLite, rutas absolutas, OpenCode, stdin interactivo
  ni shell.

## Reglas de desacople (obligatorias)

- No acoplar la logica de analisis a: cron, CLI, SQLite, Microsoft Graph, rutas
  absolutas, OpenCode ni al shell.
- Los prompts van en archivos (`prompts/`), no incrustados en el codigo.
- La configuracion se centraliza en `config.py`; migrar a AWS deberia implicar
  solo cambiar la fuente de configuracion (por ejemplo, variables de entorno o
  Secrets Manager), no tocar la logica.

## Contrato del Analyzer futuro

La logica central de analisis debe poder exponerse como una funcion pura de
entrada/salida JSON:

```python
def analizar_muestra(entrada):
    ...
    return resultado
```

Entrada (JSON):

```json
{
  "sample_id": "",
  "sample_path": "",
  "metadata_file": "metadata.json"
}
```

Salida (JSON estructurado, referencia):

```json
{
  "sample_id": "",
  "verdict": "",
  "confidence": 0,
  "classification": "",
  "summary": "",
  "authentication": { "spf": "", "dkim": "", "dmarc": "" },
  "iocs": { "urls": [], "domains": [], "ips": [], "emails": [], "hashes": [] },
  "attachments": [],
  "redirects": [],
  "mitre_attack": [],
  "findings": [],
  "recommendations": [],
  "analysis_timestamp": ""
}
```

## Interfaz con OpenCode (hoy)

OpenCode consume una muestra sin acceder a Graph. Recibe solo un `sample_path`,
por ejemplo:

```
downloads/pending/20260922_160501_9fd472ab/
```

Dentro encuentra: el `.msg`, el `.eml`, `metadata.json`, `hashes.txt` y `READY`.

## Preparacion para MCP (futuro, no implementar)

Las funciones de analisis futuras deberian disenarse con entradas/salidas claras
para poder exponerse luego como herramientas MCP, por ejemplo:
`analizar_headers()`, `extraer_iocs()`, `analizar_url()`, `analizar_adjunto()`,
`consultar_threat_intelligence()`.

## No sobrearquitectura

No agregar anticipadamente Kafka, SQS, Lambda, EventBridge, ECS, Kubernetes,
DynamoDB, API Gateway, microservicios, event sourcing, CQRS ni Clean Architecture
compleja. La preparacion para AgentCore se logra con buenos limites entre modulos.
