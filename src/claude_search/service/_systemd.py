"""Linux service backend using a systemd --user unit.

Writes the unit to ``~/.config/systemd/user/claude-search.service`` and
manages it via ``systemctl --user``. No sudo required for the install
itself; ``loginctl enable-linger`` is the one piece that needs sudo
and is left to the user with a printed hint.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Iterator

from . import SERVICE_NAME, ServiceConfig, ServiceStatus

UNIT_NAME = f"{SERVICE_NAME}.service"


def _unit_dir() -> Path:
    return Path.home() / ".config" / "systemd" / "user"


def _unit_path() -> Path:
    return _unit_dir() / UNIT_NAME


def _resolve_exec() -> str:
    """Find the absolute path to the claude-search entry point.

    Tries in order:
      1. shutil.which("claude-search") — the pipx shim on ~/.local/bin
      2. sys.argv[0] resolved absolutely — covers dev venv installs and
         any other case where the user invoked us via a non-PATH path
    """
    import sys
    p = shutil.which("claude-search")
    if p:
        return p
    if sys.argv and sys.argv[0]:
        candidate = Path(sys.argv[0]).resolve()
        if candidate.is_file():
            return str(candidate)
    raise RuntimeError(
        "couldn't find the claude-search executable. If you installed via "
        "pipx, run `pipx ensurepath` and reopen your shell."
    )


def render_unit(config: ServiceConfig, *, exec_path: str | None = None) -> str:
    """Pure render — used by tests + install."""
    exe = exec_path or "/usr/bin/claude-search"
    args = f"serve --host {config.host} --port {config.port}"
    if config.log_file:
        args += f' --log-file "{config.log_file}"'
    return (
        "[Unit]\n"
        "Description=claude-search local API + UI\n"
        "After=default.target\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        f"ExecStart={exe} {args}\n"
        "Restart=on-failure\n"
        "RestartSec=2\n"
        "Environment=PYTHONUNBUFFERED=1\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def _systemctl(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["systemctl", "--user", *args],
        check=check,
        capture_output=True,
        text=True,
    )


def _linger_enabled() -> bool:
    user = os.environ.get("USER") or os.environ.get("LOGNAME") or ""
    if not user:
        return False
    return Path("/var/lib/systemd/linger") .joinpath(user).exists()


def install(config: ServiceConfig) -> list[str]:
    _unit_dir().mkdir(parents=True, exist_ok=True)
    exec_path = _resolve_exec()
    unit = render_unit(config, exec_path=exec_path)
    _unit_path().write_text(unit, encoding="utf-8")

    _systemctl("daemon-reload")
    _systemctl("enable", "--now", UNIT_NAME)

    hints = [
        f"installed: {_unit_path()}",
        f"running on http://{config.host}:{config.port}/",
        f"  status:    claude-search service status",
        f"  logs:      claude-search service logs -f",
        f"  uninstall: claude-search service uninstall",
    ]
    if not _linger_enabled():
        user = os.environ.get("USER") or "<you>"
        hints.append("")
        hints.append(
            "note: without linger, the service stops when you log out. "
            "to survive logout/reboot:"
        )
        hints.append(f"  sudo loginctl enable-linger {user}")
    return hints


def uninstall() -> None:
    if _unit_path().exists():
        _systemctl("disable", "--now", UNIT_NAME, check=False)
        _unit_path().unlink(missing_ok=True)
        _systemctl("daemon-reload", check=False)


def start() -> None:
    _systemctl("start", UNIT_NAME)


def stop() -> None:
    _systemctl("stop", UNIT_NAME)


def restart() -> None:
    _systemctl("restart", UNIT_NAME)


def status() -> ServiceStatus:
    if not _unit_path().exists():
        return ServiceStatus(installed=False, running=False, detail="(not installed)")
    res = _systemctl("status", UNIT_NAME, check=False)
    detail = (res.stdout or "") + (res.stderr or "")
    # systemctl returns 0 only when active. Non-zero with the unit
    # present means installed-but-not-running.
    running = res.returncode == 0
    return ServiceStatus(installed=True, running=running, detail=detail.rstrip())


def tail_logs(*, follow: bool = False, lines: int = 200) -> Iterator[str]:
    cmd = ["journalctl", "--user", "-u", UNIT_NAME, "-n", str(lines), "--no-pager"]
    if follow:
        cmd.append("-f")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            yield line.rstrip("\n")
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
