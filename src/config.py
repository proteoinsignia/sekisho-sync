"""
config.py — Sekisho Sync shared configuration contract.

Variable registry
─────────────────────────────────────────────────────────────────────────────
Variable            Default                      Component(s)  Validation
─────────────────────────────────────────────────────────────────────────────
SEKISHO_BASE        /var/lib/sekisho             sync, extract  absolute path
SEKISHO_TIMEOUT     45                           sync           int, 10–600
SEKISHO_SLEEP_POLL  5                            sync           int, 1–300
MEMOS_DIR           $SEKISHO_BASE/extract        viewer         absolute path
HOST                0.0.0.0                      viewer         IPv4 address or 'localhost'
PORT                5000                         viewer         int, 1024–65535
EXTRACT_SCRIPT      (empty — button hidden)      viewer         path exists + executable
EXTRACT_ARGS        --sekisho --prefix-date      viewer         str (not validated)
EXTRACT_TIMEOUT     120                          viewer         int, 10–600
PAGE_SIZE           100                          viewer         int, 1–1000
MAX_PAGE_SIZE       1000                         viewer         int, 1–5000
PREVIEW_CHARS       240                          viewer         int, 40–2000

Rules
─────
- If a variable is absent → use default, no complaint.
- If a variable is present but malformed → raise ConfigError immediately.
- No silent clamps, no fallback-on-bad-value.
- EXTRACT_SCRIPT: absence is valid (disables button). Presence requires the
  path to exist and be executable.
- Caller is responsible for catching ConfigError and logging/exiting.
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


class ConfigError(Exception):
    """Raised when a config variable is present but invalid."""


# ── Primitive parsers ────────────────────────────────────────────────────────

def _parse_int(name: str, raw: str, lo: int, hi: int) -> int:
    """Parse int from raw string; raise ConfigError if not an int or out of range."""
    try:
        value = int(raw.strip())
    except ValueError:
        raise ConfigError(f"{name}={raw!r} is not a valid integer")
    if not (lo <= value <= hi):
        raise ConfigError(f"{name}={value} out of range [{lo}, {hi}]")
    return value


def _parse_abs_path(name: str, raw: str) -> Path:
    """Parse absolute path; raise ConfigError if relative."""
    p = Path(raw.strip())
    if not p.is_absolute():
        raise ConfigError(f"{name}={raw!r} must be an absolute path")
    return p


def _parse_host(name: str, raw: str) -> str:
    """
    Validate bind address for Flask.
    Accepts: valid IPv4 address, 'localhost', or '0.0.0.0'.
    Rejects: empty strings, hostnames, IPv6 (not needed for this use case).
    """
    value = raw.strip()
    if not value:
        raise ConfigError(f"{name} is set but empty")

    if value == "localhost":
        return value

    parts = value.split(".")
    if len(parts) != 4:
        raise ConfigError(
            f"{name}={value!r} is not a valid IPv4 address or 'localhost'"
        )
    for part in parts:
        if not part.isdigit():
            raise ConfigError(
                f"{name}={value!r} is not a valid IPv4 address (non-numeric octet: {part!r})"
            )
        octet = int(part)
        if not (0 <= octet <= 255):
            raise ConfigError(
                f"{name}={value!r} is not a valid IPv4 address (octet {octet} out of range)"
            )
    return value


def _parse_extract_script(name: str, raw: str) -> Optional[Path]:
    """
    Parse EXTRACT_SCRIPT.
    - Empty string → None (button hidden, no error).
    - Non-empty → must exist and be executable.
    """
    if not raw:
        return None
    p = Path(raw.strip())
    if not p.exists():
        raise ConfigError(f"{name}={raw!r} does not exist")
    if not os.access(p, os.X_OK):
        raise ConfigError(f"{name}={raw!r} is not executable")
    return p


# ── Config objects ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SyncConfig:
    """Configuration for sekisho_sync.py."""
    sekisho_base:  Path
    timeout:       int   # seconds waiting for Palm per attempt
    sleep_poll:    int   # seconds between attempts


@dataclass(frozen=True)
class ViewerConfig:
    """Configuration for palm_memo_viewer.py."""
    memos_dir:       Path
    host:            str
    port:            int
    extract_script:  Optional[Path]  # None = Sync button hidden
    extract_args:    str
    extract_timeout: int
    page_size:       int
    max_page_size:   int
    preview_chars:   int


# ── Loaders ──────────────────────────────────────────────────────────────────

def _get(name: str, default: str) -> tuple[str, bool]:
    """
    Returns (value, was_set).
    was_set=False means the variable was absent → caller uses default.
    was_set=True  means it was present → caller must validate.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default, False
    return raw, True


def load_sync_config() -> SyncConfig:
    """
    Load and validate configuration for sekisho_sync.py.
    Raises ConfigError if any present variable is malformed.
    """
    raw_base, base_set = _get("SEKISHO_BASE", "/var/lib/sekisho")
    sekisho_base = _parse_abs_path("SEKISHO_BASE", raw_base) if base_set \
                   else Path(raw_base)

    raw_timeout, timeout_set = _get("SEKISHO_TIMEOUT", "45")
    timeout = _parse_int("SEKISHO_TIMEOUT", raw_timeout, 10, 600)

    raw_poll, poll_set = _get("SEKISHO_SLEEP_POLL", "5")
    sleep_poll = _parse_int("SEKISHO_SLEEP_POLL", raw_poll, 1, 300)

    return SyncConfig(
        sekisho_base=sekisho_base,
        timeout=timeout,
        sleep_poll=sleep_poll,
    )


def load_viewer_config() -> ViewerConfig:
    """
    Load and validate configuration for palm_memo_viewer.py.
    Raises ConfigError if any present variable is malformed.
    """
    # Derive MEMOS_DIR default from SEKISHO_BASE if available
    base_raw, _ = _get("SEKISHO_BASE", "/var/lib/sekisho")
    default_memos = str(Path(base_raw) / "extract")

    raw_memos, memos_set = _get("MEMOS_DIR", default_memos)
    memos_dir = _parse_abs_path("MEMOS_DIR", raw_memos) if memos_set \
                else Path(raw_memos)

    raw_host, _ = _get("HOST", "0.0.0.0")
    host = _parse_host("HOST", raw_host)

    raw_port, _ = _get("PORT", "5000")
    port = _parse_int("PORT", raw_port, 1024, 65535)

    raw_script, _ = _get("EXTRACT_SCRIPT", "")
    extract_script = _parse_extract_script("EXTRACT_SCRIPT", raw_script.strip())

    raw_args, _ = _get("EXTRACT_ARGS", "--sekisho --prefix-date")
    extract_args = raw_args.strip()

    raw_etimeout, _ = _get("EXTRACT_TIMEOUT", "120")
    extract_timeout = _parse_int("EXTRACT_TIMEOUT", raw_etimeout, 10, 600)

    raw_page, _ = _get("PAGE_SIZE", "100")
    page_size = _parse_int("PAGE_SIZE", raw_page, 1, 1000)

    raw_max_page, _ = _get("MAX_PAGE_SIZE", "1000")
    max_page_size = _parse_int("MAX_PAGE_SIZE", raw_max_page, 1, 5000)

    raw_preview, _ = _get("PREVIEW_CHARS", "240")
    preview_chars = _parse_int("PREVIEW_CHARS", raw_preview, 40, 2000)

    return ViewerConfig(
        memos_dir=memos_dir,
        host=host,
        port=port,
        extract_script=extract_script,
        extract_args=extract_args,
        extract_timeout=extract_timeout,
        page_size=page_size,
        max_page_size=max_page_size,
        preview_chars=preview_chars,
    )
