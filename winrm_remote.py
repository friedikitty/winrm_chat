# /// script
# requires-python = ">=3.9"
# dependencies = [
#   "pywinrm>=0.4.3",
#   "requests-ntlm>=1.2.0",
#   "rich>=13.0",
# ]
# ///
"""
winrm_remote.py - Guided TUI for connecting to a remote Windows machine via WinRM.

Run with uv (dependencies are resolved automatically):

    uv run winrm_remote.py

Security notes:
  - The password is read with getpass (masked system input) and is only
    held in memory for the duration of the session. It is never logged,
    saved, or echoed.
  - The pywinrm client talks WS-Man directly and does NOT require the
    local machine's WSMan TrustedHosts list to be modified.

Server-side prerequisites (see the bundled setup_host_winrm.ps1 -- run it as
Administrator on the server; it prints the active listeners when done):
  - Enable-PSRemoting -Force -SkipNetworkProfileCheck
  - For Basic auth over HTTP: winrm set winrm/config/service/auth '@{Basic="true"}'
    and winrm set winrm/config/service '@{AllowUnencrypted="true"}'
  - Firewall: allow inbound 5985 (HTTP) or 5986 (HTTPS)
  - HTTPS: the script also creates a 5986 listener, reusing a valid server
    certificate or creating a self-signed one when none exists.
"""

import getpass
import re
import socket
import sys

import requests
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

import winrm
from winrm.exceptions import WinRMError, WinRMTransportError

console = Console()

TRANSPORTS = ("ntlm", "basic")


def decode_output(data: bytes) -> str:
    """Decode remote output. Chinese Windows hosts often emit GBK."""
    if not data:
        return ""
    for encoding in ("utf-8", "gbk"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


CD_RE = re.compile(r"^\s*(?:cd|chdir|sl|Set-Location)\b", re.IGNORECASE)
CLIXML_ERROR_RE = re.compile(r'<S S="Error">(.*?)</S>', re.DOTALL)
CLIXML_ESCAPE_RE = re.compile(r"_x([0-9A-Fa-f]{4})_")
CWD_MARKER = "__WINRM_REMOTE_CWD__="


def clean_stderr(text: str) -> str:
    """Extract readable errors from PowerShell CLIXML output.

    PowerShell serializes error/progress streams to stderr as CLIXML.
    Return only the error text; progress noise is dropped.
    """
    if "#< CLIXML" not in text and "<Objs" not in text:
        return text
    errors = []
    for raw in CLIXML_ERROR_RE.findall(text):
        unescaped = CLIXML_ESCAPE_RE.sub(lambda m: chr(int(m.group(1), 16)), raw)
        unescaped = unescaped.strip()
        if unescaped:
            errors.append(unescaped)
    return "\n".join(errors)


def ps_quote(value: str) -> str:
    """Single-quote a string for safe embedding in a PowerShell command."""
    return "'" + value.replace("'", "''") + "'"


def wrap_command(command: str, cwd: str) -> str:
    """Run every command from the tracked working directory.

    pywinrm starts a fresh powershell.exe per call, so the cwd is kept
    client-side and restored remotely before each command. Progress
    records are silenced to avoid CLIXML noise on stderr.
    """
    prefix = "$ProgressPreference='SilentlyContinue'; "
    if cwd:
        prefix += f"Set-Location {ps_quote(cwd)}; "
    return prefix + command


def get_remote_cwd(session: winrm.Session) -> str:
    """Initial working directory of the remote shell."""
    try:
        result = session.run_ps("$ProgressPreference='SilentlyContinue'; "
                                "(Get-Location).Path")
        if result.status_code == 0:
            return decode_output(result.std_out).strip()
    except Exception:
        pass
    return ""


def parse_cd_output(stdout: str):
    """Split a cd command's output into (new_cwd or None, visible output)."""
    new_cwd = None
    lines = []
    for line in stdout.splitlines():
        if line.startswith(CWD_MARKER):
            candidate = line[len(CWD_MARKER):].strip()
            if candidate:
                new_cwd = candidate
        else:
            lines.append(line)
    visible = "\n".join(lines)
    if visible:
        visible += "\n"
    return new_cwd, visible


def build_session(host: str, port: int, use_ssl: bool, transport: str,
                  username: str, password: str) -> winrm.Session:
    scheme = "https" if use_ssl else "http"
    url = f"{scheme}://{host}:{port}/wsman"
    return winrm.Session(
        url,
        auth=(username, password),
        transport=transport,
        server_cert_validation="ignore" if use_ssl else "validate",
    )


def step_target() -> dict:
    """Step 1: collect target host and transport options."""
    console.print(Panel.fit(
        "[bold]Step 1/3 -- Target machine[/bold]\n"
        "WinRM listens on [cyan]5985 (HTTP)[/cyan] or [cyan]5986 (HTTPS)[/cyan].",
        title="WinRM Remote", border_style="cyan"))

    host = Prompt.ask("[bold]Remote host[/bold] (IP or hostname)").strip()
    if not host:
        console.print("[red]Host cannot be empty.[/red]")
        sys.exit(1)

    use_ssl = Confirm.ask("Use HTTPS (port 5986)?", default=True)
    default_port = 5986 if use_ssl else 5985
    port_raw = Prompt.ask("Port", default=str(default_port)).strip()
    try:
        port = int(port_raw)
    except ValueError:
        console.print(f"[red]Invalid port '{port_raw}'.[/red]")
        sys.exit(1)

    table = Table(title="Authentication transport", show_header=False)
    table.add_row("ntlm", "Negotiate/NTLM -- default for workgroup/local accounts")
    table.add_row("basic", "Basic -- only if the server enabled it (prefer HTTPS)")
    console.print(table)
    transport = Prompt.ask("Transport", choices=list(TRANSPORTS), default="ntlm")

    return {"host": host, "port": port, "use_ssl": use_ssl, "transport": transport}


def step_credentials() -> tuple:
    """Step 2: username via prompt, password via masked system input."""
    console.print(Panel.fit(
        "[bold]Step 2/3 -- Credentials[/bold]\n"
        "The password is entered via masked system input ([cyan]getpass[/cyan]):\n"
        "nothing is echoed, logged, or stored on disk.",
        border_style="cyan"))

    username = Prompt.ask("[bold]Username[/bold] (e.g. Administrator or HOST\\user)").strip()
    if not username:
        console.print("[red]Username cannot be empty.[/red]")
        sys.exit(1)

    password = getpass.getpass("Password (input hidden): ")
    if not password:
        console.print("[red]Password cannot be empty.[/red]")
        sys.exit(1)

    return username, password


def check_port(host: str, port: int, timeout: float = 5.0) -> bool:
    """Fast TCP probe so a closed/filtered port fails in seconds, not 30."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def print_connection_hints(host: str, port: int, use_ssl: bool) -> None:
    alt_port = 5985 if use_ssl else 5986
    console.print(
        "[yellow]Hints:[/yellow]\n"
        "  - First: on the server, run this tool's setup script as Administrator\n"
        "    (it is bundled next to winrm_remote.py):\n"
        "      powershell -ExecutionPolicy Bypass -File setup_host_winrm.ps1\n"
        "    It enables WinRM, opens the firewall, and prints the active listeners.\n"
        "  - If it still fails, check on the server:\n"
        "      winrm enumerate winrm/config/listener\n"
        "      Get-Service WinRM\n"
        f"  - Try the other transport (HTTP 5985 / HTTPS 5986; you chose "
        f"{'HTTPS' if use_ssl else 'HTTP'}, maybe the server only listens "
        f"on {alt_port}).\n"
        "  - A timeout (not 'refused') means packets are being dropped: if "
        "this is a cloud VM, check the provider's security group / network\n"
        "    ACL for the port, not just the Windows firewall.")
    if use_ssl:
        console.print(
            "  - [yellow]HTTPS note:[/yellow] the current setup_host_winrm.ps1 "
            "creates an HTTPS listener too (reusing a server cert, or a\n"
            "    self-signed one). Re-run it on the server, then retry HTTPS.")


AUTH_HINTS = (
    "[yellow]Hints:[/yellow]\n"
    "  - First: on the server, run this tool's setup script as Administrator\n"
    "    (bundled next to winrm_remote.py):\n"
    "      powershell -ExecutionPolicy Bypass -File setup_host_winrm.ps1\n"
    "  - Check that the chosen auth transport is enabled on the server\n"
    "    (winrm get winrm/config/service/auth), and for Basic over HTTP that\n"
    "    AllowUnencrypted=true."
)


def step_connect(opts: dict, username: str, password: str) -> winrm.Session:
    """Step 3: create the session and verify it with a test command."""
    console.print(Panel.fit(
        "[bold]Step 3/3 -- Connect & verify[/bold]",
        border_style="cyan"))

    with console.status(f"Probing {opts['host']}:{opts['port']} ..."):
        reachable = check_port(opts["host"], opts["port"])
    if not reachable:
        console.print(
            f"[red]Cannot reach {opts['host']}:{opts['port']}[/red] "
            "(TCP connect failed within 5s).")

        # Offer to fall back to the other standard WinRM port when it
        # answers -- e.g. a cloud security group may only allow 5985.
        alt_ssl = not opts["use_ssl"]
        alt_port = 5986 if alt_ssl else 5985
        alt_name = "HTTPS" if alt_ssl else "HTTP"
        with console.status(f"Probing {opts['host']}:{alt_port} ..."):
            alt_reachable = check_port(opts["host"], alt_port)

        if alt_reachable and Confirm.ask(
                f"{alt_name} port {alt_port} IS reachable. "
                f"Connect with {alt_name} instead?", default=True):
            opts = {**opts, "use_ssl": alt_ssl, "port": alt_port}
        else:
            print_connection_hints(opts["host"], opts["port"], opts["use_ssl"])
            sys.exit(1)

    session = build_session(password=password, username=username, **opts)
    target = f"{'https' if opts['use_ssl'] else 'http'}://{opts['host']}:{opts['port']}"

    with console.status(f"Connecting to {target} (transport={opts['transport']}) ..."):
        try:
            result = session.run_ps("$env:COMPUTERNAME")
        except WinRMTransportError as e:
            console.print(f"[red]Transport error:[/red] {e}")
            console.print(AUTH_HINTS)
            sys.exit(1)
        except requests.exceptions.RequestException as e:
            console.print(f"[red]Network error:[/red] {e}")
            print_connection_hints(opts["host"], opts["port"], opts["use_ssl"])
            sys.exit(1)
        except WinRMError as e:
            console.print(f"[red]WinRM error:[/red] {e}")
            sys.exit(1)

    if result.status_code != 0:
        console.print(f"[red]Auth or execution failed[/red] "
                      f"(status {result.status_code}): {decode_output(result.std_err)}")
        sys.exit(1)

    remote_name = decode_output(result.std_out).strip()
    console.print(f"[green]Connected.[/green] Remote computer: [bold]{remote_name}[/bold]")
    return session


def command_loop(session: winrm.Session, host: str) -> None:
    """Interactive PowerShell loop over the WinRM session."""
    console.print(
        "\n[bold]Remote PowerShell ready.[/bold] "
        "Type a command, or [cyan]exit[/cyan]/[cyan]quit[/cyan] to leave.\n"
        "[dim]cd works and the working directory persists between commands; "
        "variables do not (each command runs in a fresh powershell.exe).[/dim]")

    cwd = get_remote_cwd(session)

    while True:
        try:
            command = console.input(
                f"\n[bold green][{host}][/bold green] PS {cwd}> ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\nLeaving session.")
            break

        if not command:
            continue
        if command.lower() in ("exit", "quit"):
            break

        is_cd = bool(CD_RE.match(command))
        script = wrap_command(command, cwd)
        if is_cd:
            # Report the resulting location back via a marker line so the
            # client-side cwd can be updated. If the cd fails, the marker
            # just echoes the unchanged directory.
            script += f'; Write-Output "{CWD_MARKER}$((Get-Location).Path)"'

        try:
            result = session.run_ps(script)
        except (WinRMError, WinRMTransportError,
                requests.exceptions.RequestException) as e:
            console.print(f"[red]Execution failed:[/red] {e}")
            continue

        stdout = decode_output(result.std_out)
        stderr = clean_stderr(decode_output(result.std_err))

        if is_cd and stdout:
            new_cwd, stdout = parse_cd_output(stdout)
            if new_cwd:
                cwd = new_cwd

        if stdout:
            console.print(stdout, end="")
        if stderr:
            console.print(f"[red]{stderr}[/red]", end="")
        if result.status_code != 0:
            console.print(f"[dim](exit code {result.status_code})[/dim]")


def main() -> None:
    console.print(Panel.fit(
        "[bold cyan]WinRM Remote PowerShell[/bold cyan]\n"
        "Guided connection to a remote Windows machine via pywinrm.",
        border_style="bright_blue"))

    opts = step_target()
    username, password = step_credentials()
    session = step_connect(opts, username, password)

    try:
        command_loop(session, opts["host"])
    finally:
        # Drop credential references as soon as the session ends.
        password = None
        session = None
        console.print("Session closed. Credentials cleared from memory.",
                      style="dim")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\nAborted.", style="yellow")
