from src.bot import ChroniclerBot, CorrectionModal, DraftView
from src.config import load_settings
from src.models import VaultFolders
from src.obsidian_manager import ObsidianVault
from src.state_store import StateStore


class IdleProvider:
    async def aclose(self):
        return None


def test_commands_and_persistent_review_buttons(tmp_path):
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    settings = load_settings(
        {
            "DISCORD_TOKEN": "token",
            "GEMINI_API_KEY": "key",
            "OBSIDIAN_VAULT_PATH": str(vault_path),
            "DATABASE_PATH": str(tmp_path / "state.db"),
        }
    )
    store = StateStore(settings.database_path)
    bot = ChroniclerBot(
        settings,
        ObsidianVault(vault_path, VaultFolders()),
        store,
        IdleProvider(),
    )
    assert sorted(command.name for command in bot.tree.get_commands()) == ["canon", "preguntar", "resumir"]
    view = DraftView()
    assert view.timeout is None
    assert view.is_persistent()
    assert sorted(item.custom_id for item in view.children) == [
        "chronicler:approve",
        "chronicler:correct",
    ]
    modal = CorrectionModal(123)
    assert modal.message_id == 123
    assert modal.title == "Corregir resumen"
    store.close()
