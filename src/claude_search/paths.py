from __future__ import annotations

from pathlib import Path

import platformdirs

APP_NAME = "claude-search"


def default_projects_dir() -> Path:
    """Return the default Claude Code projects directory.

    Linux:   ~/.claude/projects
    Windows: %USERPROFILE%\\.claude\\projects
    macOS:   ~/.claude/projects
    """
    return Path.home() / ".claude" / "projects"


def default_data_dir() -> Path:
    """Return the per-user data directory used to store the index DB."""
    return Path(platformdirs.user_data_dir(APP_NAME))


def default_db_path() -> Path:
    """Return the default index DB path."""
    return default_data_dir() / "index.sqlite"


def default_log_dir() -> Path:
    """Return the per-user log directory.

    Linux:   ~/.local/state/claude-search/log
    Windows: %LOCALAPPDATA%\\claude-search\\Logs
    """
    return Path(platformdirs.user_log_dir(APP_NAME))


def default_log_file() -> Path:
    """Default log file used by the Windows scheduled task.

    Linux's systemd unit captures stdout into journald instead, so the
    log file path isn't needed there — but this still works as the
    target for `--log-file` if someone wants one.
    """
    return default_log_dir() / "serve.log"


def decode_project_dirname(dirname: str) -> str:
    """Best-effort recovery of an original project path from its encoded directory name.

    Claude Code encodes project paths by replacing path separators with '-'.
    The original separators are not recoverable unambiguously (a real '-' in a
    path collides with the encoding), so this is a fallback used only when the
    JSONL events themselves don't carry a `cwd`.
    """
    if not dirname:
        return ""
    if dirname.startswith("-"):
        return "/" + dirname[1:].replace("-", "/")
    return dirname.replace("-", "/")
