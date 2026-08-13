# winrm_remote_powershell

A small [uv](https://docs.astral.sh/uv/)-based TUI tool that guides you through
connecting to a remote Windows machine over WinRM, using the `pywinrm` module.

## Features

- Guided 3-step flow: target -> credentials -> connect & verify
- Interactive PowerShell command loop over the WinRM session (`session.run_ps`)
  - `cd` works and the working directory persists between commands (tracked
    client-side; each command still runs in a fresh powershell.exe, so
    variables do not carry over)
  - Prompt shows the current remote directory: `[host] PS C:\Path> `
  - PowerShell progress/error CLIXML noise is cleaned up automatically
- NTLM (default) or Basic auth, HTTPS (5986, default) or HTTP (5985)
- Fast TCP pre-probe: a closed/filtered port fails in ~1s instead of a 30s
  traceback, and if the other standard WinRM port answers, the tool offers
  to fall back to it on the spot
- Safe password entry: read via `getpass` (masked system input) - never echoed,
  logged, or stored on disk; held in memory only for the session
- Does **not** touch the local `WSMan:\localhost\Client\TrustedHosts`
  (pywinrm talks WS-Man directly, unlike native `Enter-PSSession`)

## Usage

```cmd
uv run winrm_remote.py
```

or via the PowerShell launcher (double-click friendly):

```powershell
powershell -ExecutionPolicy Bypass -File start_local.ps1
```

Then follow the prompts:

1. **Target**: host IP/name, HTTPS (default) or HTTP, port, auth transport
   (`ntlm`/`basic`)
2. **Credentials**: username, then password via hidden system input
3. **Connect**: the tool verifies the connection, then drops you into a
   `[host] PS C:\...> ` loop. Type `exit` or `quit` to leave.

## Server prerequisites

Run the bundled setup script **on the server**, as Administrator:

```powershell
powershell -ExecutionPolicy Bypass -File setup_host_winrm.ps1
```

It enables PSRemoting (HTTP listener on 5985), Basic auth, `AllowUnencrypted`,
the WinRM firewall rules, and an HTTPS listener on 5986 (reusing a valid
server certificate, or creating a self-signed one when none exists), sets
the WinRM service to start automatically, and restarts it so the HTTPS
binding is live immediately. At the end it prints the active listeners,
probes both ports locally, and lists the machine's IPv4 addresses.

The client tool ignores certificate validation for HTTPS
(`server_cert_validation="ignore"`), so a self-signed cert works out of
the box - fine for trusted networks, but verify the host by other means.

If a port shows LISTENING on the server but clients still cannot connect,
the block is outside the machine: allow inbound TCP on the port in the
cloud security group / network ACL (the Windows firewall alone is not
enough on cloud VMs).

If a connection fails, the tool prints these hints automatically.

## Files

- `winrm_remote.py` - the tool (single file, PEP 723 inline deps; uv resolves
  `pywinrm`, `requests-ntlm`, `rich` automatically)
- `start_local.ps1` - launcher: runs the tool via uv from the script folder,
  keeps the window open (double-click friendly)
- `setup_host_winrm.ps1` - server-side WinRM setup (run as Administrator)
- `scripts/test_driver.py` - dev aid: runs the guided flow non-interactively
  with `getpass` patched, e.g.
  `printf '127.0.0.1\n\n\n\nadmin\n' | uv run --script scripts/test_driver.py`
- `scripts/test_units.py` - tests for output cleanup / cd handling using a
  fake session: `uv run --script scripts/test_units.py`
