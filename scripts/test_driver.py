# /// script
# requires-python = ">=3.9"
# dependencies = [
#   "pywinrm>=0.4.3",
#   "requests-ntlm>=1.2.0",
#   "rich>=13.0",
# ]
# ///
# Test driver: runs winrm_remote.main() with getpass patched, so the
# guided flow can be exercised non-interactively (piped stdin).
# Run: uv run --script scripts/test_driver.py
import getpass
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

getpass.getpass = lambda prompt="": "wrongpass"

sys.argv = ["winrm_remote.py"]

import winrm_remote

try:
    winrm_remote.main()
except SystemExit as e:
    print(f"[driver] exited with code {e.code}")
