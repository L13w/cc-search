"""Windows service backend using a Scheduled Task.

Registers a ``ClaudeSearch`` task triggered AtLogon, running
``pythonw.exe -m claude_search.cli serve …`` to suppress the console
flash that ``claude-search.exe`` would produce.

All operations go through PowerShell because Register-ScheduledTask
is the modern surface; ``schtasks.exe`` works but its XML and
error-handling are clunkier.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterator

from ..paths import default_log_file
from . import SERVICE_NAME, ServiceConfig, ServiceStatus

TASK_NAME = "ClaudeSearch"

# PowerShell's Format-List pads property names to the longest in the
# selection ("TaskName" = 8 chars), so the State row reads as e.g.
# `State    : Running`. Match any whitespace gap to keep the parser
# robust against future column changes.
_STATE_RUNNING_RE = re.compile(r"^\s*State\s*:\s*Running\s*$", re.MULTILINE)


def _resolve_pythonw() -> str:
    """Find pythonw.exe in the same venv/install as the running python.

    pipx installs it in <venv>/Scripts/pythonw.exe alongside python.exe;
    a system Python install has it next to python.exe in its install
    dir. ``sys.executable`` is python(.exe) — swap the suffix.
    """
    py = Path(sys.executable)
    candidate = py.with_name("pythonw.exe")
    if candidate.exists():
        return str(candidate)
    # Fallback: scan PATH.
    found = shutil.which("pythonw.exe")
    if found:
        return found
    raise RuntimeError(
        "couldn't find pythonw.exe next to your Python install. "
        f"Looked at {candidate}. If you installed via pipx, run "
        "`pipx environment` and verify the venv is intact."
    )


def render_powershell(config: ServiceConfig, *, pythonw: str | None = None,
                       log_file: str | None = None) -> str:
    """Pure render — used by tests + install.

    PowerShell-quoting note: the executable path goes in PowerShell
    double-quotes, but the argument string contains its own
    double-quotes (around the log path, in case it has spaces). Those
    inner quotes are escaped for PowerShell with a backtick (``\\``) so
    PowerShell preserves them as literal `"` characters in the value
    it stores in the task's Arguments field — which is what
    pythonw.exe sees on the command line.
    """
    pw = pythonw or r"C:\Python\pythonw.exe"
    log = log_file or str(default_log_file())
    # `--log-file` is a flag on the top-level `main` group, not on
    # `serve`, so click only accepts it BEFORE the subcommand name.
    # Inner quotes are PowerShell-escaped with backticks so the outer
    # `-Argument "..."` string survives parsing intact.
    args = (
        f'-m claude_search.cli '
        f'--log-file `"{log}`" '
        f'serve --host {config.host} --port {config.port}'
    )
    return (
        f"$action = New-ScheduledTaskAction "
        f'-Execute "{pw}" '
        f'-Argument "{args}"\n'
        f"$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME\n"
        f"$settings = New-ScheduledTaskSettingsSet "
        f"-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries "
        f"-StartWhenAvailable -RestartCount 3 "
        f"-RestartInterval (New-TimeSpan -Minutes 1)\n"
        f"$principal = New-ScheduledTaskPrincipal "
        f"-UserId $env:USERNAME -LogonType Interactive\n"
        f'Register-ScheduledTask -TaskName "{TASK_NAME}" -Action $action '
        f"-Trigger $trigger -Settings $settings -Principal $principal -Force"
    )


def _powershell(script: str, *, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        check=check,
        capture_output=True,
        text=True,
    )


def install(config: ServiceConfig) -> list[str]:
    pw = _resolve_pythonw()
    log_path = Path(config.log_file) if config.log_file else default_log_file()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    config = ServiceConfig(host=config.host, port=config.port, log_file=str(log_path))
    script = render_powershell(config, pythonw=pw, log_file=str(log_path))
    res = _powershell(script)
    if res.returncode != 0:
        raise RuntimeError(
            f"Register-ScheduledTask failed:\n{res.stdout}\n{res.stderr}"
        )

    # Start it now (AtLogon won't fire until next login otherwise).
    _powershell(f'Start-ScheduledTask -TaskName "{TASK_NAME}"', check=False)

    return [
        f"installed Scheduled Task: {TASK_NAME}",
        f"running on http://{config.host}:{config.port}/",
        f"  status:    claude-search service status",
        f"  logs:      claude-search service logs -f  ({log_path})",
        f"  uninstall: claude-search service uninstall",
    ]


def uninstall() -> None:
    _powershell(
        f'Unregister-ScheduledTask -TaskName "{TASK_NAME}" -Confirm:$false',
        check=False,
    )


def start() -> None:
    res = _powershell(f'Start-ScheduledTask -TaskName "{TASK_NAME}"')
    if res.returncode != 0:
        raise RuntimeError(res.stderr.strip())


def stop() -> None:
    res = _powershell(f'Stop-ScheduledTask -TaskName "{TASK_NAME}"')
    if res.returncode != 0:
        raise RuntimeError(res.stderr.strip())


def restart() -> None:
    stop()
    # Brief settle before restart.
    time.sleep(0.5)
    start()


def status() -> ServiceStatus:
    res = _powershell(
        f'Get-ScheduledTask -TaskName "{TASK_NAME}" '
        f'| Select-Object -Property TaskName, State '
        f'| Format-List | Out-String',
        check=False,
    )
    out = (res.stdout or "").strip()
    if res.returncode != 0 or "Cannot find" in (res.stderr or "") or not out:
        return ServiceStatus(installed=False, running=False, detail="(not installed)")
    info = _powershell(
        f'Get-ScheduledTaskInfo -TaskName "{TASK_NAME}" | Format-List | Out-String',
        check=False,
    )
    detail = out + "\n" + (info.stdout or "")
    # State property values: Ready, Running, Disabled, Queued, Unknown.
    running = bool(_STATE_RUNNING_RE.search(out))
    return ServiceStatus(installed=True, running=running, detail=detail.rstrip())


def tail_logs(*, follow: bool = False, lines: int = 200) -> Iterator[str]:
    log_path = default_log_file()
    if not log_path.exists():
        yield f"(no log file at {log_path})"
        return
    # Static tail of the last N lines, then optional follow.
    with open(log_path, "r", encoding="utf-8", errors="replace") as fp:
        try:
            fp.seek(0, os.SEEK_END)
            size = fp.tell()
            chunk = min(size, max(8192, lines * 200))
            fp.seek(size - chunk)
            tail_text = fp.read()
            existing = tail_text.splitlines()[-lines:]
            for line in existing:
                yield line
            if not follow:
                return
            # Follow mode: poll for new lines.
            while True:
                line = fp.readline()
                if not line:
                    time.sleep(0.5)
                    continue
                yield line.rstrip("\n")
        except KeyboardInterrupt:
            return
