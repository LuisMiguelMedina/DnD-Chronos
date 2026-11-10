"""Discord bot entrypoint for DungeonChronicler."""

from __future__ import annotations

import asyncio
import json
import logging
import tempfile
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from src.ai_engine import (
    GeminiEngine,
    format_draft_preview,
    refine_draft_with_feedback,
    ChroniclerDraft,
)
from src.config import Settings, load_settings
from src.db import (
    get_review_by_message_id,
    init_db,
    save_pending_review,
    update_draft,
    update_review_status,
)
from src.obsidian_manager import ObsidianManager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("DungeonChronicler")

AUDIO_VIDEO_EXTENSIONS = {
    ".mp3",
    ".wav",
    ".ogg",
    ".m4a",
    ".flac",
    ".mp4",
    ".webm",
    ".mov",
    ".mkv",
}


class CorrectionModal(discord.ui.Modal, title="Corregir borrador"):
    feedback = discord.ui.TextInput(
        label="Indica qué corregir o añadir",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=2000,
    )

    def __init__(self, bot: ChroniclerBot, review_message_id: int) -> None:
        super().__init__()
        self.chronicler = bot
        self.review_message_id = review_message_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        review = get_review_by_message_id(
            self.chronicler.settings.database_path,
            self.review_message_id,
        )
        if not review or review.status != "pending":
            await interaction.followup.send("Este borrador ya no está pendiente.", ephemeral=True)
            return

        draft = ChroniclerDraft.from_dict(json.loads(review.draft_json))
        try:
            refined = await asyncio.to_thread(
                refine_draft_with_feedback,
                self.chronicler.engine,
                draft,
                str(self.feedback.value),
            )
        except Exception:
            logger.exception("Refine draft failed")
            await interaction.followup.send("No pude aplicar las correcciones.", ephemeral=True)
            return

        update_draft(self.chronicler.settings.database_path, self.review_message_id, refined.to_dict())
        preview = format_draft_preview(refined)
        channel = interaction.channel
        if channel:
            try:
                msg = await channel.fetch_message(self.review_message_id)
                await msg.edit(content=f"**Borrador actualizado**\n\n{preview}", view=ReviewView(self.chronicler))
            except discord.HTTPException:
                pass
        await interaction.followup.send("Borrador actualizado. Revisa el mensaje principal.", ephemeral=True)


class ReviewView(discord.ui.View):
    def __init__(self, bot: ChroniclerBot) -> None:
        super().__init__(timeout=60 * 60 * 24)
        self.chronicler = bot

    @discord.ui.button(label="Aprobar", style=discord.ButtonStyle.success)
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer()
        review = get_review_by_message_id(
            self.chronicler.settings.database_path,
            interaction.message.id,
        )
        if not review or review.status != "pending":
            await interaction.followup.send("Este borrador ya fue procesado.", ephemeral=True)
            return

        draft = ChroniclerDraft.from_dict(json.loads(review.draft_json))
        try:
            result = await asyncio.to_thread(
                self.chronicler.obsidian.write_session_draft,
                draft,
            )
        except OSError as exc:
            logger.exception("Obsidian write failed")
            await interaction.followup.send(f"Error al escribir en el vault: {exc}", ephemeral=True)
            return

        update_review_status(self.chronicler.settings.database_path, interaction.message.id, "approved")
        self.clear_items()
        paths = [result.session_path.name] + [p.name for p in result.npc_paths + result.place_paths]
        await interaction.message.edit(
            content=(
                f"**Aprobado y guardado en Obsidian**\n\n"
                f"{format_draft_preview(draft)}\n\n"
                f"_Archivos: {', '.join(paths[:8])}{'…' if len(paths) > 8 else ''}_"
            ),
            view=None,
        )
        await interaction.followup.send("Contenido exportado al vault.", ephemeral=True)

    @discord.ui.button(label="Corregir", style=discord.ButtonStyle.primary)
    async def correct(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(CorrectionModal(self.chronicler, interaction.message.id))


class ChroniclerBot(commands.Bot):
    def __init__(self, settings: Settings) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)
        self.settings = settings
        self.engine = GeminiEngine(
            api_key=settings.gemini_api_key,
            model_name=settings.gemini_model,
            api_base_url=settings.gemini_api_base_url,
            session_token=settings.gemini_session_token,
        )
        self.obsidian = ObsidianManager(settings.vault_path, settings.folders)

    async def setup_hook(self) -> None:
        guild_id = self.settings.discord_guild_id
        if guild_id:
            guild = discord.Object(id=guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()

    async def on_ready(self) -> None:
        logger.info("Logged in as %s", self.user)

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        await self.process_commands(message)
        if not message.attachments:
            return
        for att in message.attachments:
            suffix = Path(att.filename or "").suffix.lower()
            if suffix not in AUDIO_VIDEO_EXTENSIONS:
                continue
            status = await message.reply("Procesando sesión… esto puede tardar varios minutos.")
            try:
                draft = await self._process_attachment(att)
            except Exception:
                logger.exception("Media processing failed")
                await status.edit(content="No pude procesar el archivo. Revisa logs y la API de Gemini.")
                return

            preview = format_draft_preview(draft)
            review_msg = await message.reply(
                f"**Borrador de sesión**\n\n{preview}\n\n_Usa Aprobar para guardar en Obsidian o Corregir para ajustar._",
                view=ReviewView(self),
            )
            save_pending_review(
                self.settings.database_path,
                message_id=review_msg.id,
                channel_id=review_msg.channel.id,
                draft=draft.to_dict(),
                attachment_name=att.filename or "unknown",
            )

    async def _process_attachment(self, att: discord.Attachment) -> ChroniclerDraft:
        suffix = Path(att.filename or "media.bin").suffix or ".bin"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / f"upload{suffix}"
            await att.save(path)
            return await asyncio.to_thread(self.engine.process_media_file, path)


async def _canon(interaction: discord.Interaction, bot: ChroniclerBot, query: str) -> None:
    await interaction.response.defer(thinking=True)
    hits = await asyncio.to_thread(bot.obsidian.search_canon, query)
    if not hits:
        await interaction.followup.send(f"Sin resultados en el vault para `{query}`.")
        return
    lines = [f"**Canon: `{query}`**", ""]
    for rel, snippet in hits:
        lines.append(f"• `{rel}` — {snippet}")
    text = "\n".join(lines)
    if len(text) > 1900:
        text = text[:1900] + "…"
    await interaction.followup.send(text)


def build_bot() -> ChroniclerBot:
    settings = load_settings()
    init_db(settings.database_path)
    bot = ChroniclerBot(settings)

    @bot.tree.command(name="canon", description="Buscar en el vault de Obsidian")
    @app_commands.describe(query="Texto a buscar (NPC, lugar, evento)")
    async def canon_cmd(interaction: discord.Interaction, query: str) -> None:
        await _canon(interaction, bot, query)

    @bot.tree.command(name="chronicler_ping", description="Comprueba que el bot responde")
    async def ping_cmd(interaction: discord.Interaction) -> None:
        await interaction.response.send_message("DungeonChronicler en línea.")

    return bot


def main() -> None:
    bot = build_bot()
    bot.run(bot.settings.discord_token)


if __name__ == "__main__":
    main()
