import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional


class NTSCRSError(RuntimeError):
    pass


def find_ntsc_cli() -> Optional[Path]:
    env_path = os.getenv("NTSC_RS_CLI_PATH")
    if env_path:
        candidate = Path(env_path)
        if candidate.exists():
            return candidate

    shell_path = shutil.which("ntsc-rs-cli")
    if shell_path:
        return Path(shell_path)

    mac_app = Path("/Applications/ntsc-rs.app/Contents/MacOS/ntsc-rs-cli")
    if mac_app.exists():
        return mac_app

    return None


def run_ntsc_cli(input_path: Path, output_path: Path, preset_path: Path) -> None:
    cli_path = find_ntsc_cli()
    if cli_path is None:
        raise NTSCRSError(
            "ntsc-rs-cli not found. Install ntsc-rs and set NTSC_RS_CLI_PATH or add it to PATH."
        )

    args = [
        str(cli_path),
        "-i",
        str(input_path),
        "-o",
        str(output_path),
        "-p",
        str(preset_path),
        "-y",
    ]
    process = subprocess.run(args, capture_output=True, text=True)
    if process.returncode != 0:
        raise NTSCRSError(process.stderr.strip() or process.stdout.strip())
