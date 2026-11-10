# DungeonChronicler

Copiloto de Discord para narradores de rol. Escucha el audio o el video de la mesa (o lee una transcripción), redacta un borrador y espera a que alguien lo apruebe. Solo entonces escribe notas Markdown en un vault de Obsidian montado en la misma máquina.

El audio no se guarda. En el vault entra el texto aprobado. SQLite solo recuerda el borrador para que los botones sigan vivos si el bot se reinicia.

## Decisiones

El contenedor no adivina dónde está Obsidian. `docker-compose` monta `OBSIDIAN_VAULT_HOST_PATH` en `/vault`, y el bot escribe solo dentro de esa carpeta. Si el servidor no puede montar el vault, este bot no puede guardar las notas.

Las carpetas por defecto son `NPCs`, `Lugares`, `Sesiones`, `Objetos`, `Facciones` y `Tramas`. Se cambian con `VAULT_DIR_*` sin tocar el código. Una sesión aprobada también actualiza `Sesiones/Índice.md`.

## Qué hace

1. Alguien usa `/resumir` con un archivo, escribe las notas de la sesión en ese comando, o menciona al bot en un mensaje que trae audio, video o un `.txt`/`.md`.
2. El proveedor de IA devuelve un borrador: resumen, personajes, lugares, objetos, facciones, tramas e incertidumbres.
3. Discord muestra el borrador con **Aprobar** y **Corregir**. Corregir no vuelve a enviar el audio: revisa el texto.
4. Aprobar crea o actualiza las notas. Si el personaje ya existía, no pisa la descripción; agrega una aparición de esa sesión.
5. `/canon` busca en el vault sin distinguir mayúsculas ni acentos.

Pueden aprobar o corregir quien envió la sesión y quien administra el servidor.

## Proveedores de IA

`AI_PROVIDER=gemini` usa la API de Google con `GEMINI_API_KEY`. El modelo por defecto es `gemini-3.8-flash`; cámbialo con `GEMINI_MODEL` si tu clave no lo tiene (por ejemplo `gemini-2.5-flash`).

Si tienes una pasarela compatible con el SDK de Gemini, deja `AI_PROVIDER=gemini` y define `GEMINI_BASE_URL` más `AI_SESSION_TOKEN`. La clave puede omitirse en ese caso.

`AI_PROVIDER=session` manda `AI_SESSION_TOKEN` como `Authorization: Bearer` a un proveedor privado. Hace falta `AI_BASE_URL` y `AI_MODEL`.

- `AI_SESSION_API_STYLE=openai` hace `POST {AI_BASE_URL}/chat/completions`. Acepta texto, mp3 y wav. El video no entra por este estilo.
- `AI_SESSION_API_STYLE=chronicle` hace `POST {AI_BASE_URL}/v1/chronicle` con este JSON:

```json
{
  "campaign": "Campaña",
  "today": "2026-11-10",
  "notes": "",
  "transcript": null,
  "filename": "mesa.mp3",
  "mime_type": "audio/mpeg",
  "media_base64": null,
  "correction": null,
  "draft": null,
  "instruction": "instrucciones y pedido"
}
```

La respuesta puede ser el borrador directo o `{"draft": { ... }}`. En una corrección, `correction` trae el texto del narrador, `draft` el borrador anterior y `media_base64` va vacío. Sirve para envolver una API privada que no hable el formato de Gemini ni el de OpenAI.

## Discord

1. Crea una aplicación en el portal de desarrolladores y copia el token del bot a `DISCORD_TOKEN`.
2. Activa el intent privilegiado **Message Content Intent**. Hace falta para leer un audio enviado mencionando al bot. `/resumir` y `/canon` funcionan sin él.
3. Invítalo con los scopes `bot` y `applications.commands`, y con permiso para ver canales, leer el historial, enviar mensajes, insertar enlaces y adjuntar archivos.
4. Opcional: pon el id del servidor en `DISCORD_GUILD_ID` para que los comandos aparezcan enseguida. Sin ese valor, Discord puede tardar en propagar los comandos globales. Si antes los registraste en global y ahora usas un servidor, borra los globales viejos en el portal para que no salgan duplicados.

## Configuración

Copia `.env.example` a `.env` y completa el token, la clave de IA y la ruta del vault.

| Variable | Para qué |
| --- | --- |
| `OBSIDIAN_VAULT_PATH` | Vault cuando corres el bot fuera de Docker. |
| `OBSIDIAN_VAULT_HOST_PATH` | Carpeta del host que Compose monta en `/vault`. |
| `DATABASE_PATH` | SQLite con los borradores. |
| `CAMPAIGN_NAME` | Campaña si el comando no dice otra. |
| `CHRONICLER_TIMEZONE` | Fecha de referencia de la sesión. Compose usa `America/Mexico_City` si no la cambias. |
| `WATCH_CHANNEL_IDS` | Canales donde un audio se procesa aunque no mencionen al bot. |
| `MAX_MEDIA_BYTES` | Tope de descarga. Por defecto 25 MB, nunca más de 100 MB. |

Con el proveedor Gemini, los archivos de hasta `INLINE_MEDIA_BYTES` (15 MB) viajan dentro de la petición. Los más grandes se suben a la Files API y el bot pide borrarlos al terminar. La retención de ese texto depende de la cuenta de Google: en el nivel gratuito revisa AI Studio. El estilo `chronicle` manda el archivo en base64 y tampoco lo escribe en el vault.

## Docker

La carpeta del vault tiene que existir antes de levantar Compose. Si la ruta no existe, Docker crea un directorio vacío y las notas no caen en tu vault de verdad.

```bash
cp .env.example .env
# edita el token, la clave y OBSIDIAN_VAULT_HOST_PATH
docker compose up --build -d
```

Por defecto el proceso corre como root dentro del contenedor para poder escribir el volumen. Si prefieres el uid de tu usuario, descomenta `user:` en `docker-compose.yml` y asegúrate de que ese usuario pueda escribir el vault y el volumen `chronicler-data`.

## Desarrollo

Python 3.12.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
mkdir -p vault data
cp .env.example .env
python -m pytest
python -m src
```

## Verificación manual

1. Sube un audio con `/resumir` y comprueba que el mensaje queda en borrador, con incertidumbres si el audio no se entiende.
2. Pulsa **Corregir**, cambia un nombre y confirma que el mensaje se actualiza sin escribir en el vault.
3. Pulsa **Aprobar** y abre Obsidian: la sesión está en `Sesiones/`, y los personajes o lugares enlazan a sus notas. Una segunda aparición no borra lo que hayas editado a mano.
4. Usa `/canon` con un nombre, también sin acentos.

Las pruebas automáticas cubren el parser de la IA con la red simulada, la escritura del vault y la regla de que nadie guarda canon sin aprobación. No abren una sesión real de Discord.
