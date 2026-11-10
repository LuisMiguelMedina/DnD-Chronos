"""Bot de Discord: escucha la sesión, muestra el borrador y espera aprobación."""

import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import discord
from discord import app_commands

from src.ai_engine import GeminiProvider, build_provider
from src.canon_qa import (
    build_canon_request,
    canon_qa_rules,
    gather_sources,
    vault_only_answer,
)
from src.config import Settings, load_settings, today_in
from src.errors import AIError, ConfigError, DraftError, MediaError, StateError, VaultError
from src.media import classify, detect_kind, prepare_media
from src.obsidian_manager import ObsidianVault
from src.presentation import (
    DraftMessage,
    build_draft_message,
    build_saved_message,
    format_commit,
    format_search,
)
from src.state_store import StateStore
from src.textutil import clean_name, clip
from src.vector_index import VaultVectorIndex, build_vector_index, search_canon
from src.workflow import approve_draft, load_review, revise_draft_for_message

log = logging.getLogger("dungeonchronicler")
_BAD_TOKEN_MESSAGE = "Discord rechazó DISCORD_TOKEN. Revisa el token en .env."


class DraftView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Aprobar",
        style=discord.ButtonStyle.success,
        custom_id="chronicler:approve",
    )
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        bot = _bot(interaction)
        message = interaction.message
        if message is None:
            await interaction.response.send_message(
                "No encuentro el mensaje de este borrador.",
                ephemeral=True,
            )
            return
        try:
            load_review(
                bot.store,
                message.id,
                user_id=interaction.user.id,
                manage_guild=_can_manage(interaction),
            )
        except DraftError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.defer()
        try:
            result = await asyncio.to_thread(
                approve_draft,
                bot.store,
                bot.vault,
                message.id,
                interaction.user.id,
                _can_manage(interaction),
            )
        except (DraftError, VaultError) as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        except Exception as exc:
            log.error("Fallo al guardar el canon (%s)", type(exc).__name__)
            await interaction.followup.send(_generic_failure(), ephemeral=True)
            return
        if bot.vector_index is not None:
            try:
                await asyncio.to_thread(bot.vector_index.index_commit, result)
            except Exception as exc:
                log.warning("No pude actualizar Qdrant (%s)", type(exc).__name__)
        record = bot.store.get_by_message(message.id)
        if record is not None:
            saved = build_saved_message(record.draft, draft_id=record.id, campaign=record.campaign)
            try:
                await message.edit(embed=to_embed(saved), view=None)
            except discord.HTTPException:
                log.error("No pude actualizar el mensaje aprobado")
        await interaction.followup.send(format_commit(result))

    @discord.ui.button(
        label="Corregir",
        style=discord.ButtonStyle.primary,
        custom_id="chronicler:correct",
    )
    async def correct(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        bot = _bot(interaction)
        message = interaction.message
        if message is None:
            await interaction.response.send_message(
                "No encuentro el mensaje de este borrador.",
                ephemeral=True,
            )
            return
        try:
            record = load_review(
                bot.store,
                message.id,
                user_id=interaction.user.id,
                manage_guild=_can_manage(interaction),
            )
        except DraftError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        if record.status != "pending":
            text = (
                "Ese resumen ya está en el vault."
                if record.status == "approved"
                else "Ese borrador no se puede corregir ahora."
            )
            await interaction.response.send_message(text, ephemeral=True)
            return
        await interaction.response.send_modal(CorrectionModal(message.id))


class CorrectionModal(discord.ui.Modal, title="Corregir resumen"):
    correccion = discord.ui.TextInput(
        label="Qué hay que cambiar",
        style=discord.TextStyle.paragraph,
        placeholder="El NPC se llama Elara, no Elera. La cueva queda al norte.",
        max_length=4000,
        required=True,
    )

    def __init__(self, message_id: int) -> None:
        super().__init__()
        self.message_id = message_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        bot = _bot(interaction)
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            record, draft = await revise_draft_for_message(
                bot.store,
                bot.provider,
                self.message_id,
                str(self.correccion.value),
                user_id=interaction.user.id,
                manage_guild=_can_manage(interaction),
                today=today_in(bot.settings.timezone),
            )
            channel = interaction.channel
            if channel is None:
                channel = await bot.fetch_channel(record.channel_id)
            message = await channel.fetch_message(self.message_id)
            embed = to_embed(
                build_draft_message(draft, draft_id=record.id, campaign=record.campaign)
            )
            await message.edit(content=None, embed=embed, view=DraftView())
        except (AIError, DraftError, VaultError, StateError) as exc:
            await interaction.edit_original_response(content=str(exc))
            return
        except Exception as exc:
            log.error("Fallo al corregir el borrador (%s)", type(exc).__name__)
            await interaction.edit_original_response(content=_generic_failure())
            return
        await interaction.edit_original_response(
            content="Actualicé el borrador. Revísalo y aprueba cuando cuadre con la mesa."
        )


class ChroniclerBot(discord.Client):
    def __init__(
        self,
        settings: Settings,
        vault: ObsidianVault,
        store: StateStore,
        provider,
        vector_index: VaultVectorIndex | None = None,
    ) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents, allowed_mentions=discord.AllowedMentions.none())
        self.settings = settings
        self.vault = vault
        self.store = store
        self.provider = provider
        self.vector_index = vector_index
        self.tree = app_commands.CommandTree(self)
        register_commands(self)

    async def setup_hook(self) -> None:
        self.add_view(DraftView())
        if self.settings.guild_id:
            guild = discord.Object(id=self.settings.guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()
        log.info("Comandos sincronizados")

    async def on_ready(self) -> None:
        log.info("Conectado como %s", self.user)

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or self.user is None or not message.attachments:
            return
        mentioned = self.user in message.mentions
        watched = message.channel.id in self.settings.watch_channel_ids
        if not mentioned and not watched:
            return
        attachment, error, saw_candidate = _pick_attachment(
            message.attachments,
            max_bytes=self.settings.max_media_bytes,
        )
        if attachment is None:
            if mentioned or saw_candidate:
                await message.reply(
                    error or "Adjunta un audio, un video o un texto (.txt o .md).",
                    mention_author=False,
                )
            return
        kind = detect_kind(attachment.filename, attachment.content_type)
        placeholder = (
            "Estoy leyendo la sesión. El borrador aparecerá en este mensaje."
            if kind == "text"
            else "Estoy escuchando la sesión. El borrador aparecerá en este mensaje."
        )
        reply = await message.reply(placeholder, mention_author=False)
        await self._fill_draft(
            reply,
            author_id=message.author.id,
            guild_id=message.guild.id if message.guild else None,
            notes=_notes_from_message(message, self.user),
            campaign=self.settings.campaign_name,
            loader=attachment.read,
            filename=attachment.filename,
            content_type=attachment.content_type,
        )

    async def close(self) -> None:
        try:
            await self.provider.aclose()
        finally:
            self.store.close()
            await super().close()

    async def _fill_draft(
        self,
        message: discord.Message,
        *,
        author_id: int,
        guild_id: int | None,
        notes: str,
        campaign: str,
        loader,
        filename: str | None,
        content_type: str | None,
    ) -> None:
        try:
            prepared = None
            if loader is None:
                if len(notes.strip()) < 20:
                    raise MediaError(
                        "Adjunta un audio, un video o un texto, o escribe notas de la sesión."
                    )
            else:
                data = await loader()
                prepared = prepare_media(
                    filename or "sesion",
                    content_type,
                    data,
                    max_bytes=self.settings.max_media_bytes,
                )
            draft = await self.provider.structure_session(
                campaign=campaign,
                notes=notes,
                today=today_in(self.settings.timezone),
                media=None if prepared is None else prepared.data,
                mime_type=None if prepared is None else prepared.mime_type,
                filename=None if prepared is None else prepared.filename,
                transcript=None if prepared is None else prepared.transcript,
            )
            record = self.store.save_pending(
                message_id=message.id,
                channel_id=message.channel.id,
                guild_id=guild_id,
                author_id=author_id,
                campaign=campaign,
                draft=draft,
                source_filename=None if prepared is None else prepared.filename,
            )
            embed = to_embed(build_draft_message(draft, draft_id=record.id, campaign=campaign))
            try:
                await message.edit(content=None, embed=embed, view=DraftView())
            except Exception:
                self.store.delete(record.id)
                raise
        except (AIError, MediaError, VaultError, DraftError, StateError) as exc:
            await _fail(message, str(exc))
        except Exception as exc:
            log.error("Fallo al redactar el borrador (%s)", type(exc).__name__)
            await _fail(message, _generic_failure())


def register_commands(bot: ChroniclerBot) -> None:
    @bot.tree.command(
        name="resumir",
        description="Genera un borrador de la sesión a partir de audio, video o texto",
    )
    @app_commands.describe(
        archivo="Audio, video o transcripción .txt/.md",
        notas="Contexto para el cronista: qué ignorar o cómo llamar la sesión",
        campana="Sustituye la campaña configurada",
    )
    async def resumir(
        interaction: discord.Interaction,
        archivo: discord.Attachment | None = None,
        notas: str = "",
        campana: str = "",
    ) -> None:
        notas = notas or ""
        campaign = clean_name(campana or "", 80) or bot.settings.campaign_name
        if archivo is not None:
            try:
                classify(
                    archivo.filename,
                    archivo.content_type,
                    archivo.size,
                    max_bytes=bot.settings.max_media_bytes,
                )
            except MediaError as exc:
                await interaction.response.send_message(str(exc), ephemeral=True)
                return
        elif len(notas.strip()) < 20:
            await interaction.response.send_message(
                "Adjunta un audio, un video o un texto, o escribe notas de la sesión.",
                ephemeral=True,
            )
            return
        await interaction.response.defer()
        message = await interaction.original_response()
        kind = None if archivo is None else detect_kind(archivo.filename, archivo.content_type)
        listening = (
            "Estoy leyendo la sesión. El borrador aparecerá en este mensaje."
            if archivo is None or kind == "text"
            else "Estoy escuchando la sesión. El borrador aparecerá en este mensaje."
        )
        await message.edit(content=listening)
        await bot._fill_draft(
            message,
            author_id=interaction.user.id,
            guild_id=interaction.guild_id,
            notes=notas,
            campaign=campaign,
            loader=None if archivo is None else archivo.read,
            filename=None if archivo is None else archivo.filename,
            content_type=None if archivo is None else archivo.content_type,
        )

    @bot.tree.command(name="canon", description="Busca nombres y texto en el vault")
    @app_commands.describe(consulta="Nombre o fragmento a buscar")
    async def canon(interaction: discord.Interaction, consulta: str) -> None:
        if len(consulta.strip()) < 2:
            await interaction.response.send_message(
                "Escribe al menos dos caracteres para buscar.",
                ephemeral=True,
            )
            return
        await interaction.response.defer()
        try:
            hits = await asyncio.to_thread(search_canon, bot.vault, bot.vector_index, consulta, 5)
        except VaultError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        if not hits:
            await interaction.followup.send("No hay nada en el vault que coincida con esa consulta.")
            return
        await interaction.followup.send(format_search(consulta, hits))

    @bot.tree.command(
        name="preguntar",
        description="Pregunta sobre el canon: el bot responde con contexto del vault",
    )
    @app_commands.describe(consulta="Qué quieres saber de la campaña")
    async def preguntar(interaction: discord.Interaction, consulta: str) -> None:
        text = consulta.strip()
        if len(text) < 3:
            await interaction.response.send_message(
                "Escribe al menos tres caracteres para preguntar.",
                ephemeral=True,
            )
            return
        if not isinstance(bot.provider, GeminiProvider):
            await interaction.response.send_message(
                "Las preguntas con contexto requieren AI_PROVIDER=gemini.",
                ephemeral=True,
            )
            return
        await interaction.response.defer()
        try:
            sources = await asyncio.to_thread(
                gather_sources,
                bot.vault,
                bot.vector_index,
                text,
            )
        except Exception as exc:
            log.error("No pude leer el vault (%s)", type(exc).__name__)
            await interaction.followup.send(
                "No pude leer el vault. Revisa que esté montado.",
                ephemeral=True,
            )
            return
        if not sources:
            await interaction.followup.send(
                "No encontré notas en el vault sobre eso. "
                "Prueba otro nombre o aprueba una sesión que lo mencione."
            )
            return
        request = build_canon_request(text, sources)
        try:
            answer = await bot.provider.answer_canon_question(
                campaign=bot.settings.campaign_name,
                question=text,
                request=request,
                system=canon_qa_rules(bot.settings.campaign_name),
            )
            await interaction.followup.send(clip(answer, 1900))
        except AIError:
            await interaction.followup.send(vault_only_answer(text, sources))

    @bot.tree.error
    async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        original = getattr(error, "original", error)
        text = _public_error(original)
        log.error("Comando falló: %s", type(original).__name__)
        try:
            if interaction.response.is_done():
                await interaction.followup.send(text, ephemeral=True)
            else:
                await interaction.response.send_message(text, ephemeral=True)
        except discord.HTTPException:
            log.error("No pude avisar del fallo en Discord")


def to_embed(message: DraftMessage) -> discord.Embed:
    embed = discord.Embed(title=message.title, description=message.description, color=message.color)
    for field in message.fields:
        embed.add_field(name=field.name, value=field.value, inline=False)
    embed.set_footer(text=message.footer)
    return embed


def main() -> None:
    try:
        settings = load_settings()
    except ConfigError as exc:
        raise SystemExit(str(exc)) from None
    _setup_logging(settings.timezone)
    if not settings.vault_path.is_dir():
        raise SystemExit(
            f"No existe el vault en {settings.vault_path}. Monta la carpeta de Obsidian en esa ruta."
        )
    log.info("Proveedor de IA: %s", settings.ai_provider)
    log.info("Vault: %s", settings.vault_path)
    log.info("Campaña: %s", settings.campaign_name)
    vault = ObsidianVault(settings.vault_path, settings.folders)
    try:
        store = StateStore(settings.database_path)
        provider = build_provider(settings)
        vector_index = build_vector_index(settings, vault)
    except (ConfigError, AIError, StateError) as exc:
        raise SystemExit(str(exc)) from None
    if vector_index is not None:
        log.info("Qdrant: %s (colección %s)", settings.qdrant_url, settings.qdrant_collection)
    bot = ChroniclerBot(settings, vault, store, provider, vector_index)
    try:
        bot.run(settings.discord_token, log_handler=None)
    except discord.LoginFailure:
        raise SystemExit(_BAD_TOKEN_MESSAGE) from None
    except discord.HTTPException as exc:
        if exc.status == 401:
            raise SystemExit(_BAD_TOKEN_MESSAGE) from None
        raise


def _bot(interaction: discord.Interaction) -> ChroniclerBot:
    client = interaction.client
    if not isinstance(client, ChroniclerBot):
        raise RuntimeError("El cliente de Discord no es DungeonChronicler.")
    return client


def _can_manage(interaction: discord.Interaction) -> bool:
    permissions = getattr(interaction, "permissions", None)
    return bool(permissions and permissions.manage_guild)


def _notes_from_message(message: discord.Message, bot_user: discord.ClientUser) -> str:
    content = message.content or ""
    content = content.replace(f"<@{bot_user.id}>", " ").replace(f"<@!{bot_user.id}>", " ")
    return " ".join(content.split())


def _pick_attachment(attachments, *, max_bytes: int):
    last_error = None
    saw_candidate = False
    for attachment in attachments:
        if detect_kind(attachment.filename, attachment.content_type) is None:
            continue
        saw_candidate = True
        try:
            classify(
                attachment.filename,
                attachment.content_type,
                attachment.size,
                max_bytes=max_bytes,
            )
        except MediaError as exc:
            last_error = str(exc)
            continue
        return attachment, None, True
    return None, last_error, saw_candidate


async def _fail(message: discord.Message, text: str) -> None:
    try:
        await message.edit(content=text, embed=None, view=None)
    except discord.HTTPException:
        log.error("No pude avisar del fallo en Discord")


def _public_error(exc: Exception) -> str:
    if isinstance(exc, (AIError, MediaError, VaultError, DraftError, StateError, ConfigError)):
        return str(exc)
    return _generic_failure()


def _generic_failure() -> str:
    return "No pude completar eso. El detalle quedó en el registro del bot."


def _setup_logging(timezone_name: str) -> None:
    class ZoneFormatter(logging.Formatter):
        def __init__(self) -> None:
            super().__init__("%(asctime)s %(levelname)s %(name)s %(message)s")
            self.zone = ZoneInfo(timezone_name)

        def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
            moment = datetime.fromtimestamp(record.created, self.zone)
            label = moment.tzname() or timezone_name
            return moment.strftime("%Y-%m-%d %H:%M:%S ") + label

    handler = logging.StreamHandler()
    handler.setFormatter(ZoneFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    logging.getLogger("discord").setLevel(logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("google").setLevel(logging.WARNING)
