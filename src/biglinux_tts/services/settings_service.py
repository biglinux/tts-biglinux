"""
Settings service — load, save, and manage application settings.

Wraps config.py load/save with debounced persistence and migration.
"""

from __future__ import annotations

import logging

from biglinux_tts.config import AppSettings, load_settings, save_settings
from biglinux_tts.utils.async_utils import Debouncer

logger = logging.getLogger(__name__)


class SettingsService:
    """
    Settings management with debounced saving.

    Provides a single source of truth for all app settings.
    """

    def __init__(self) -> None:
        self._settings: AppSettings | None = None
        self._save_debouncer = Debouncer(500, self._do_save)

    def get(self) -> AppSettings:
        """Get current settings (lazy load)."""
        if self._settings is None:
            self._settings = load_settings()
            logger.debug("Settings loaded")
        return self._settings

    def save(self, settings: AppSettings | None = None) -> None:
        """Schedule a debounced save."""
        if settings is not None:
            self._settings = settings
        self._save_debouncer.trigger()

    def save_now(self) -> None:
        """Save immediately (for shutdown)."""
        self._save_debouncer.cancel()
        self._do_save()

    def reset_to_defaults(self) -> AppSettings:
        """Reset all settings to defaults."""
        self._settings = AppSettings()
        self.save_now()
        logger.info("Settings reset to defaults")
        return self._settings

    def _do_save(self) -> None:
        """Perform the actual save."""
        if self._settings is not None:
            save_settings(self._settings)
