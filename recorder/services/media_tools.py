"""Locate ffmpeg without requiring a system install: PATH first, then the pip-bundled binary."""
import re
import shutil
import subprocess


def ffmpeg_exe() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        raise RuntimeError(
            "ffmpeg not found. Install it, or run: python -m pip install imageio-ffmpeg"
        )


def duration_seconds(path) -> float:
    """Audio/video length via `ffmpeg -i` (no ffprobe needed)."""
    r = subprocess.run([ffmpeg_exe(), "-i", str(path)], capture_output=True, text=True)
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", r.stderr)
    if not m:
        return 0.0
    h, mi, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
    return h * 3600 + mi * 60 + s
