"""Notification adapters for WIFI-GUARD."""

from .adapters.email import EmailNotifier
from .adapters.ntfy import NtfyNotifier

__all__ = ["EmailNotifier", "NtfyNotifier"]
