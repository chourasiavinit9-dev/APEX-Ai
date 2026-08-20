"""
security/constants.py — Re-exports from core.constants for backwards compat.
All security constants are canonical in core/constants.py.
"""
from core.constants import (
    JWT_SECRET_ENV,
    JWT_ALGORITHM,
    JWT_EXPIRY_MINUTES,
    RATE_LIMIT_MAX_ATTEMPTS,
    RATE_LIMIT_WINDOW_MINUTES,
    RATE_LIMIT_LOCKOUT_MINUTES,
    HONEYPOT_FIELD_NAME,
    MAX_INPUT_STRING_LENGTH,
    MAX_EMAIL_LENGTH,
    PASSWORD_MIN_LENGTH,
)
