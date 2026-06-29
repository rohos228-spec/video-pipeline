"""Локальная озвучка без Chrome/11Labs (fleet hub montage)."""

from __future__ import annotations

import asyncio
import sys
import tempfile
import uuid
from pathlib import Path

from loguru import logger

from app.models import Project
from app.services.frame_audio import _run_ffmpeg, resolve_full_voiceover_text


async def synthesize_local_voice_for_montage(project: Project, audio_dir: Path) -> Path:
    """voiceover.txt → voice_full_*.mp3 (Windows SAPI + ffmpeg)."""
    text = resolve_full_voiceover_text(project)
    if len(text) < 50:
        raise RuntimeError(
            "voiceover.txt / script_text слишком короткий — нужен закадровый текст"
        )
    audio_dir.mkdir(parents=True, exist_ok=True)
    out_mp3 = audio_dir / f"voice_full_{uuid.uuid4().hex[:8]}.mp3"
    if sys.platform == "win32":
        await _synthesize_windows_sapi(text, out_mp3)
    else:
        raise RuntimeError(
            "local TTS: на этой ОС положите voice_full*.mp3 в audio/ или используйте Linux/WSL"
        )
    logger.info(
        "[#{}] local_tts: voice_full ← voiceover.txt ({} симв.) → {}",
        project.id,
        len(text),
        out_mp3.name,
    )
    return out_mp3


async def _synthesize_windows_sapi(text: str, out_mp3: Path) -> None:
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        suffix=".txt",
        delete=False,
    ) as tf:
        tf.write(text)
        text_path = Path(tf.name)

    wav_path = out_mp3.with_suffix(".wav")
    text_ps = str(text_path).replace("'", "''")
    wav_ps = str(wav_path).replace("'", "''")
    ps_script = f"""
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {{
  $s.SelectVoiceByHints(
    [System.Speech.Synthesis.VoiceGender]::Female,
    [System.Speech.Synthesis.VoiceAge]::Adult,
    0,
    [System.Globalization.CultureInfo]::GetCultureInfo('ru-RU')
  )
}} catch {{}}
$s.SetOutputToWaveFile('{wav_ps}')
$raw = Get-Content -LiteralPath '{text_ps}' -Raw -Encoding UTF8
$s.Speak($raw)
$s.Dispose()
"""
    try:
        proc = await asyncio.create_subprocess_exec(
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            ps_script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            err = (stderr or stdout or b"").decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"Windows SAPI TTS failed: {err or proc.returncode}")
        if not wav_path.is_file() or wav_path.stat().st_size < 1000:
            raise RuntimeError("Windows SAPI не создал wav")
        await _run_ffmpeg(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(wav_path),
                "-c:a",
                "libmp3lame",
                "-q:a",
                "2",
                str(out_mp3),
            ]
        )
        if not out_mp3.is_file() or out_mp3.stat().st_size < 1000:
            raise RuntimeError("ffmpeg не создал voice_full.mp3")
    finally:
        text_path.unlink(missing_ok=True)
        wav_path.unlink(missing_ok=True)
