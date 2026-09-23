---
inclusion: always
---

# Estructura del proyecto

Se mantienen pocos archivos y pocas carpetas. Cada modulo tiene una unica
responsabilidad clara.

```
phishing-msg-collector/
├── .kiro/
│   ├── steering/
│   │   ├── product.md
│   │   ├── tech.md
│   │   ├── structure.md
│   │   ├── security.md
│   │   ├── operations.md
│   │   └── agentcore.md
│   └── specs/
│       └── phishing-msg-collector/
│           ├── requirements.md
│           ├── design.md
│           └── tasks.md
│
├── main.py            # CLI + carga config + llama collector + resumen
├── collector.py       # Orquesta: Graph -> SQLite -> sample
├── graph_client.py    # Toda la comunicacion con Microsoft Graph (solo GET)
├── database.py        # Toda la interaccion con SQLite (sin ORM)
├── sample.py          # Guardado de evidencia, MSG->EML, metadata, hashes, READY
├── config.py          # Carga centralizada de toda la configuracion (.env)
├── utils.py           # Utilidades comunes (logging, tiempo, helpers)
│
├── requirements.txt
├── .env.example
├── .gitignore
├── README.md
├── ejecutar.sh        # Lanzador para cron (umask 027 + venv + main.py)
│
├── data/
│   └── phishing_collector.db
│
├── downloads/
│   └── pending/       # Una carpeta por muestra (sample_id)
│
├── logs/
│   ├── phishing_collector.log
│   └── cron.log
│
└── prompts/
    └── phishing_analysis.md
```

No crear mas carpetas sin necesidad real. Evitar `src/`, `core/`, `domain/`,
`repositories/`, `interfaces/`, `adapters/`, `controllers/`, `services/`,
`factories/`, salvo que el proyecto crezca mucho en el futuro.

## Responsabilidad de cada modulo

- **config.py**: unica fuente de configuracion. Lee el `.env` y expone valores
  tipados. Ningun otro modulo debe llamar `os.getenv` directamente. Esto facilita
  la futura migracion a AWS/AgentCore (solo cambia la fuente de config).

- **graph_client.py**: toda la comunicacion con Microsoft Graph. Funciones:
  `obtener_token()`, `buscar_mensajes()`, `obtener_adjuntos()`,
  `descargar_file_attachment()`, `descargar_item_attachment()`. Solo GET. No
  contiene logica de SQLite.

- **database.py**: toda la interaccion con SQLite. Funciones simples como
  `inicializar_base()`, `attachment_existe()`, `registrar_attachment()`,
  `actualizar_descarga()`, `actualizar_conversion()`, `registrar_error()`,
  `buscar_sha256()`, `obtener_ultima_revision()`, `actualizar_ultima_revision()`,
  `obtener_status()`, `obtener_historial()`. Consultas parametrizadas. Sin ORM.

- **sample.py**: operaciones sobre la muestra en el filesystem:
  `crear_directorio_muestra()`, `sanitizar_nombre()`, `guardar_msg()`,
  `guardar_eml()`, `calcular_sha256()`, `convertir_msg_a_eml()`,
  `crear_metadata()`, `crear_hashes_txt()`, `crear_ready()`. No conoce las
  credenciales de Graph.

- **collector.py**: orquesta el flujo Graph -> SQLite -> sample. No contiene
  logica de IA ni de analisis.

- **main.py**: solo CLI, carga de configuracion, invocacion del collector y
  resumen. Sin logica de negocio grande.

- **utils.py**: helpers comunes (configuracion de logging en espanol, manejo de
  fechas UTC, etc.).

## Convenciones

- Documentacion, comentarios importantes y mensajes en **espanol**.
- Nombres de tecnologias oficiales pueden quedar en ingles (Microsoft Graph,
  Client ID, Mail.Read, SQLite, AgentCore, MCP).
- Todo lo que contiene secretos o datos locales va en `.gitignore`.
