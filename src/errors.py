"""Errores que el bot puede explicar en Discord sin mostrar detalle interno."""


class ConfigError(Exception):
    """La configuración está incompleta o no se puede usar."""


class MediaError(Exception):
    """El archivo o las notas no sirven para armar una sesión."""


class AIError(Exception):
    """El proveedor de IA no pudo producir un borrador."""


class AIResponseError(AIError):
    """La respuesta llegó, pero no tenía un borrador utilizable."""


class VaultError(Exception):
    """No se pudo leer o escribir el vault de Obsidian."""


class StateError(Exception):
    """No se pudo usar la base local de borradores."""


class DraftParseError(Exception):
    """El JSON del borrador no tiene la forma esperada."""


class DraftError(Exception):
    """La revisión del borrador no puede continuar."""


class DraftNotFound(DraftError):
    def __init__(self, message: str = "No encuentro ese borrador.") -> None:
        super().__init__(message)


class DraftNotPending(DraftError):
    def __init__(self, message: str = "Ese borrador no admite esta acción.") -> None:
        super().__init__(message)


class DraftBusy(DraftError):
    def __init__(self) -> None:
        super().__init__("Ya estoy guardando ese borrador.")


class NotAllowed(DraftError):
    def __init__(self) -> None:
        super().__init__(
            "Solo quien envió la sesión, o quien administra el servidor, puede revisar este borrador."
        )
