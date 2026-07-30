"""Compatibility exports for the expanded Enneagram result profiles."""

from .profiles import TYPE_PROFILES


# Both machine and web formatters now consume the same full, structured copy.
TYPE_DESCRIPTIONS = TYPE_PROFILES
WEB_TYPE_DESCRIPTIONS = TYPE_PROFILES
