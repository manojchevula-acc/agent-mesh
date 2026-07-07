"""Formatting package — typed JSON/markdown output + audit logging (Spec §12)."""

from .response_formatter import format_calc, format_error, format_response

__all__ = ["format_response", "format_error", "format_calc"]
