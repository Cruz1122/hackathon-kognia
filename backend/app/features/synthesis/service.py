from __future__ import annotations

import os
import shutil
import subprocess


def synthesize_text(text: str) -> bytes:
    """Generate WAV audio with the local espeak-ng executable."""
    executable = os.getenv("TTS_EXECUTABLE", "espeak-ng")
    if shutil.which(executable) is None:
        raise FileNotFoundError(executable)
    result = subprocess.run(
        [
            executable,
            "--stdout",
            "-v",
            os.getenv("TTS_VOICE", "es-419"),
            "-s",
            os.getenv("TTS_SPEED", "145"),
            "-p",
            os.getenv("TTS_PITCH", "55"),
        ],
        input=text.encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=20,
    )
    if result.returncode != 0 or not result.stdout.startswith(b"RIFF"):
        raise RuntimeError("Local TTS failed")
    return result.stdout
