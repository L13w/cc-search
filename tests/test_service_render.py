"""Pure-render tests for the service backends.

These tests verify the unit / task content. They DON'T install
anything — that requires platform-specific tools (systemctl /
PowerShell / Register-ScheduledTask) and a live user session.
"""
from __future__ import annotations

from claude_search.service import ServiceConfig
from claude_search.service import _systemd, _schtasks


def test_systemd_unit_minimal():
    unit = _systemd.render_unit(
        ServiceConfig(host="127.0.0.1", port=8765),
        exec_path="/home/u/.local/bin/claude-search",
    )
    assert "Description=claude-search local API + UI" in unit
    assert "ExecStart=/home/u/.local/bin/claude-search serve --host 127.0.0.1 --port 8765" in unit
    assert "Restart=on-failure" in unit
    assert "PYTHONUNBUFFERED=1" in unit
    assert "WantedBy=default.target" in unit


def test_systemd_unit_includes_log_file_when_set():
    unit = _systemd.render_unit(
        ServiceConfig(host="127.0.0.1", port=8765, log_file="/var/log/cs.log"),
        exec_path="/usr/bin/claude-search",
    )
    assert '--log-file "/var/log/cs.log"' in unit


def test_systemd_unit_supports_non_loopback_host():
    unit = _systemd.render_unit(
        ServiceConfig(host="0.0.0.0", port=9000),
        exec_path="/x/claude-search",
    )
    assert "--host 0.0.0.0" in unit
    assert "--port 9000" in unit


def test_schtasks_powershell_minimal():
    script = _schtasks.render_powershell(
        ServiceConfig(host="127.0.0.1", port=8765),
        pythonw=r"C:\pipx\venvs\claude-search\Scripts\pythonw.exe",
        log_file=r"C:\Users\u\AppData\Local\claude-search\Logs\serve.log",
    )
    assert r"C:\pipx\venvs\claude-search\Scripts\pythonw.exe" in script
    assert "-m claude_search.cli" in script
    assert "--host 127.0.0.1 --port 8765" in script
    assert "Register-ScheduledTask" in script
    assert "-AtLogOn" in script
    assert "-Force" in script
    assert "ClaudeSearch" in script


def test_schtasks_state_running_regex_handles_format_list_padding():
    """Regression: PowerShell pads State to the longest property name's width.

    The output looks like::

        TaskName : ClaudeSearch
        State    : Running

    (4 spaces before the colon). The detector must match that.
    """
    sample = "\nTaskName : ClaudeSearch\nState    : Running\n\n"
    assert _schtasks._STATE_RUNNING_RE.search(sample)
    # Other states must NOT match.
    assert not _schtasks._STATE_RUNNING_RE.search("State    : Ready")
    assert not _schtasks._STATE_RUNNING_RE.search("State    : Disabled")


def test_schtasks_powershell_log_file_precedes_subcommand():
    """`--log-file` is a top-level `main` flag; click rejects it after `serve`.

    Regression guard for the private repo exit-code-2
    bug where the task launched but click bailed because the option
    came after the subcommand name.
    """
    script = _schtasks.render_powershell(
        ServiceConfig(host="127.0.0.1", port=8765),
        pythonw=r"C:\python\pythonw.exe",
        log_file=r"C:\log.log",
    )
    log_idx = script.find("--log-file")
    serve_idx = script.find("serve --host")
    assert log_idx > 0 and serve_idx > 0
    assert log_idx < serve_idx, (
        "--log-file must precede the `serve` subcommand or click exits 2"
    )


def test_schtasks_powershell_escapes_log_path_quotes_for_powershell():
    """Inner double-quotes around the log path must be backtick-escaped.

    Without the backticks the outer -Argument "..." string would
    terminate at the first inner quote and the rest of the args become
    garbage tokens — which is exactly the bug that produced
    ERROR_DIRECTORY (0x8007010B) on Windows.
    """
    log = r"C:\Users\J Doe\AppData\Local\claude-search\serve.log"
    script = _schtasks.render_powershell(
        ServiceConfig(host="127.0.0.1", port=8765),
        pythonw=r"C:\python\pythonw.exe",
        log_file=log,
    )
    assert f'--log-file `"{log}`"' in script
    # Sanity: a bare unescaped `--log-file "<path>"` would mean we
    # regressed.
    assert f'--log-file "{log}"' not in script
