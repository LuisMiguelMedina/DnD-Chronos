# DungeonChronicler

Bot de Discord que procesa audio o vídeo de sesiones de rol (Gemini), muestra un borrador con botones **Aprobar** / **Corregir**, y exporta notas estructuradas a un vault de Obsidian en disco.

## Requisitos

- Token de bot de Discord con **Message Content Intent** y permisos para adjuntos.
- `GEMINI_API_KEY` (nivel gratuito recomendado) o `GEMINI_SESSION_TOKEN` para un gateway propio.
- Ruta del vault de Obsidian accesible en el host (volumen Docker).

## Configuración

1. Copia `.env.example` a `.env` y completa las variables.
2. En `docker-compose.yml`, monta tu vault:

   ```yaml
   volumes:
     - /ruta/absoluta/a/tu/Vault:/vault
   ```

3. Carpetas dentro del vault (configurables por env):

   - `NPCs`, `Lugares`, `Sesiones` por defecto.

## Ejecución local (desarrollo)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export $(grep -v '^#' .env | xargs)
python -m src.bot
```

## Docker

```bash
docker compose up --build -d
```

## Uso en Discord

1. Sube un archivo de audio o vídeo a un canal donde esté el bot.
2. Espera el **borrador de sesión** con vista interactiva.
3. **Corregir** abre un modal para indicar cambios (vuelve a llamar a Gemini).
4. **Aprobar** escribe los `.md` en el vault.
5. `/canon <texto>` busca en el vault.

## Pruebas

```bash
pytest
```

## Arquitectura

| Módulo | Responsabilidad |
|--------|------------------|
| `src/bot.py` | Discord, adjuntos, botones, `/canon` |
| `src/ai_engine.py` | Gemini upload + JSON estructurado |
| `src/obsidian_manager.py` | YAML frontmatter y escritura en vault |
| `src/db.py` | SQLite para borradores pendientes |

## Preguntas abiertas (operación)

1. Confirma que el host donde corre Docker tiene la ruta del vault montada con lectura/escritura.
2. Ajusta `OBSIDIAN_FOLDER_*` si prefieres otra jerarquía en Obsidian.
