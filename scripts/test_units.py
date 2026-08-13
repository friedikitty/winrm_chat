# /// script
# requires-python = ">=3.9"
# dependencies = [
#   "pywinrm>=0.4.3",
#   "requests-ntlm>=1.2.0",
#   "rich>=13.0",
# ]
# ///
# Unit/integration tests for winrm_remote.py helpers and command loop,
# using a fake session (no real server needed).
# Run: uv run --script scripts/test_units.py
import io
import pathlib
import sys
from collections import namedtuple

from rich.console import Console

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import winrm_remote as wr

Response = namedtuple("Response", "status_code std_out std_err")

CLIXML_PROGRESS = (
    "#< CLIXML\r\n"
    '<Objs Version="1.1.0.1" xmlns="http://schemas.microsoft.com/powershell/2004/04">'
    '<Obj S="progress" RefId="0"><TN RefId="0"><T>System.Management.Automation.PSCustomObject</T>'
    '<T>System.Object</T></TN><MS><I64 N="SourceId">1</I64><PR N="Record">'
    '<AV>Preparing modules for first use.</AV><AI>0</AI><Nil /><PI>-1</PI><PC>-1</PC>'
    '<T>Completed</T><SR>-1</SR><SD> </SD></PR></MS></Obj></Objs>'
)

CLIXML_ERROR = (
    "#< CLIXML\r\n"
    '<Objs Version="1.1.0.1" xmlns="http://schemas.microsoft.com/powershell/2004/04">'
    '<S S="Error">Get-Item : Cannot find path _x0027_C:\\nope_x0027_ because it does '
    'not exist._x000D__x000A_</S></Objs>'
)


class FakeSession:
    def run_ps(self, script):
        if wr.CWD_MARKER in script:
            return Response(0, b"__WINRM_REMOTE_CWD__=C:\\Users\\Admin\\Documents\r\n",
                            CLIXML_PROGRESS.encode())
        if "(Get-Location).Path" in script:
            return Response(0, b"C:\\Users\\Admin\r\n", b"")
        if "Get-Item" in script:
            return Response(1, b"", CLIXML_ERROR.encode())
        return Response(0, b"", b"")


# --- unit checks ---
assert wr.clean_stderr(CLIXML_PROGRESS) == "", "progress noise must be dropped"
err = wr.clean_stderr(CLIXML_ERROR)
assert "Cannot find path" in err and "<S S=" not in err and "_x0027_" not in err, err
assert "'C:\\nope'" in err, err

wrapped = wr.wrap_command("hostname", "C:\\Users\\Admin")
assert "Set-Location 'C:\\Users\\Admin'; hostname" in wrapped, wrapped
assert "O''Brien" in wr.ps_quote("O'Brien")

new_cwd, visible = wr.parse_cd_output("hello\r\n__WINRM_REMOTE_CWD__=D:\\\r\n")
assert new_cwd == "D:\\" and visible == "hello\n", (new_cwd, visible)

# --- loop integration: cd persistence, prompt cwd, CLIXML cleanup ---
buf = io.StringIO()
wr.console = Console(file=buf, force_terminal=False, width=120)
sys.stdin = io.StringIO("cd .\\Documents\nGet-Item nope\nexit\n")

wr.command_loop(FakeSession(), "fakehost")

out = buf.getvalue()
assert "PS C:\\Users\\Admin> " in out, out
assert "PS C:\\Users\\Admin\\Documents> " in out, out
assert "Cannot find path" in out, out
assert "CLIXML" not in out and "<Objs" not in out, out
assert "(exit code 1)" in out, out

print("ALL TESTS PASSED")
