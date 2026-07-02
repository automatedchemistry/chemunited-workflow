from __future__ import annotations

import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
log_dir = root / ".tmp"
stdout_path = log_dir / "chemunited-server.out.log"
stderr_path = log_dir / "chemunited-server.err.log"
returncode_path = log_dir / "chemunited-server.returncode.txt"

cmd = [
    str(root / ".venv" / "Scripts" / "python.exe"),
    "-m",
    "chemunited_workflow.cli",
    "serve",
    "examples\\custom_project",
    "--port",
    "3116",
]

with stdout_path.open("ab") as stdout, stderr_path.open("ab") as stderr:
    proc = subprocess.Popen(cmd, cwd=root, stdout=stdout, stderr=stderr)
    returncode = proc.wait()

returncode_path.write_text(str(returncode), encoding="utf-8")
sys.exit(returncode)
