from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List

try:
    import imageio_ffmpeg
except ImportError:  # pragma: no cover - handled at runtime with a clear message
    imageio_ffmpeg = None


def _ffmpeg_executable() -> str:
    if imageio_ffmpeg is not None:
        try:
            return imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            pass

    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg

    raise RuntimeError(
        "Không tìm thấy FFmpeg. Hãy chạy lại: pip install -r requirements.txt"
    )


def _run_ffmpeg(args: list[str], timeout: int = 180) -> None:
    command = [_ffmpeg_executable(), "-hide_banner", "-loglevel", "error", "-y", *args]
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        error = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"FFmpeg xử lý media thất bại: {error or 'unknown error'}")


def audio_to_wav(data: bytes, suffix: str = ".bin") -> bytes:
    """Convert an audio/video payload to 16 kHz mono PCM WAV for the omni model."""
    safe_suffix = suffix if suffix.startswith(".") and len(suffix) <= 10 else ".bin"

    with tempfile.TemporaryDirectory(prefix="tg_ai_audio_") as temp_dir:
        src = Path(temp_dir) / f"input{safe_suffix}"
        dst = Path(temp_dir) / "audio.wav"
        src.write_bytes(data)

        _run_ffmpeg(
            [
                "-i",
                str(src),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "pcm_s16le",
                str(dst),
            ]
        )

        if not dst.exists() or dst.stat().st_size == 0:
            raise RuntimeError("Không tách/chuyển đổi được âm thanh.")

        return dst.read_bytes()


def extract_video_frames(
    data: bytes,
    suffix: str = ".mp4",
    duration_seconds: float = 0.0,
    max_frames: int = 8,
    max_width: int = 960,
) -> List[bytes]:
    """Sample evenly-spaced JPEG frames from a video without requiring system FFmpeg."""
    if max_frames < 1:
        max_frames = 1

    safe_suffix = suffix if suffix.startswith(".") and len(suffix) <= 10 else ".mp4"
    duration = max(float(duration_seconds or 0.0), 1.0)
    fps = min(max(max_frames / duration, 0.02), 2.0)

    with tempfile.TemporaryDirectory(prefix="tg_ai_video_") as temp_dir:
        src = Path(temp_dir) / f"input{safe_suffix}"
        frame_pattern = Path(temp_dir) / "frame_%03d.jpg"
        src.write_bytes(data)

        # fps spreads samples across the clip. Scale down large phone-camera frames to
        # reduce base64 payload size while preserving enough detail for visual analysis.
        vf = f"fps={fps:.6f},scale='min({max_width},iw)':-2"
        _run_ffmpeg(
            [
                "-i",
                str(src),
                "-vf",
                vf,
                "-q:v",
                "4",
                "-frames:v",
                str(max_frames),
                str(frame_pattern),
            ]
        )

        frame_files = sorted(Path(temp_dir).glob("frame_*.jpg"))[:max_frames]
        if not frame_files:
            raise RuntimeError("Không trích xuất được frame nào từ video.")

        return [path.read_bytes() for path in frame_files]
