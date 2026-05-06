"""Cross-platform service management for claude-search.

Wraps the platform's native scheduler so the user gets:

  claude-search service install
  claude-search service start | stop | restart
  claude-search service status
  claude-search service logs [-f]
  claude-search service uninstall [--purge]

Linux uses systemd user units. Windows uses Task Scheduler with an
AtLogon trigger, registered via PowerShell. Neither requires admin.

The actual platform shims live in ``_systemd`` and ``_schtasks``;
this module just dispatches.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Iterator

SERVICE_NAME = "claude-search"


@dataclass
class ServiceConfig:
    host: str
    port: int
    log_file: str | None = None


@dataclass
class ServiceStatus:
    installed: bool
    running: bool
    detail: str  # multi-line human-readable status


class UnsupportedPlatform(RuntimeError):
    pass


def _backend():
    if sys.platform.startswith("linux"):
        from . import _systemd
        return _systemd
    if sys.platform == "win32":
        from . import _schtasks
        return _schtasks
    raise UnsupportedPlatform(
        f"service install is only implemented for Linux and Windows; "
        f"got {sys.platform!r}. On macOS you can run `claude-search serve` "
        "from the terminal or wrap it in a launchd plist by hand."
    )


def install(config: ServiceConfig) -> list[str]:
    """Install + start. Returns post-install hint lines for the CLI to print."""
    return _backend().install(config)


def uninstall() -> None:
    _backend().uninstall()


def start() -> None:
    _backend().start()


def stop() -> None:
    _backend().stop()


def restart() -> None:
    _backend().restart()


def status() -> ServiceStatus:
    return _backend().status()


def tail_logs(*, follow: bool = False, lines: int = 200) -> Iterator[str]:
    """Stream log lines. Follow=True blocks indefinitely."""
    return _backend().tail_logs(follow=follow, lines=lines)
