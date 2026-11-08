"""Text-to-speech. gTTS first (needs internet), espeak-ng as an offline fallback."""
import logging
import shutil
import subprocess
from pathlib import Path

from . import media_tools

log = logging.getLogger(__name__)


def audio_duration(path: Path) -> float:
    return media_tools.duration_seconds(path)


def synthesize(text: str, lang: str, out_path: Path):
    """Write speech to out_path (.mp3). Returns duration in seconds, or None if all engines failed."""
    if not text.strip():
        return None
    try:
        from gtts import gTTS

        gTTS(text=text, lang=lang).save(str(out_path))
        return audio_duration(out_path)
    except Exception as e:
        log.warning("gTTS failed (%s); trying espeak-ng", e)

    exe = shutil.which("espeak-ng") or shutil.which("espeak")
    if exe:
        wav = out_path.with_suffix(".wav")
        subprocess.run([exe, "-v", lang, "-w", str(wav), text], check=False)
        if wav.exists():
            subprocess.run(
                [media_tools.ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(wav), str(out_path)], check=False
            )
            wav.unlink(missing_ok=True)
            if out_path.exists():
                return audio_duration(out_path)
    return None
