"""Conversion presets, and runtime probing of what this machine can encode.

No preset list is baked in as fact. Every ffmpeg preset declares the encoders it
needs, and the list is filtered against `ffmpeg -encoders` at startup, so a
preset whose encoder is missing simply never appears. That keeps the menu honest
on any ffmpeg build without this code having to know which one you installed.
"""

from __future__ import annotations

import dataclasses
import functools
import re
import shutil
import subprocess
from enum import Enum


class Media(str, Enum):
    VIDEO = "video"
    AUDIO = "audio"
    IMAGE = "image"


@dataclasses.dataclass(frozen=True)
class Preset:
    """One entry in the Convert To submenu."""

    id: str
    label: str
    accepts: tuple[Media, ...]  # input kinds this preset can consume
    ext: str  # output extension, without the dot
    tool: str  # "ffmpeg" or "magick"
    args: tuple[str, ...] = ()  # options placed between input and output
    needs: tuple[str, ...] = ()  # ffmpeg encoder names that must be present

    @property
    def is_video_target(self) -> bool:
        return self.ext in {"mp4", "mkv", "webm", "gif"}


_AV = (Media.VIDEO, Media.AUDIO)

PRESETS: tuple[Preset, ...] = (
    # ---- video -> video -------------------------------------------------
    Preset(
        "mp4-h264", "MP4 (H.264)", (Media.VIDEO,), "mp4", "ffmpeg",
        ("-c:v", "libx264", "-crf", "20", "-preset", "medium",
         "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"),
        needs=("libx264", "aac"),
    ),
    Preset(
        "mkv-h265", "MKV (H.265 / smaller)", (Media.VIDEO,), "mkv", "ffmpeg",
        ("-c:v", "libx265", "-crf", "24", "-preset", "medium",
         "-c:a", "aac", "-b:a", "192k"),
        needs=("libx265", "aac"),
    ),
    Preset(
        "webm-vp9", "WebM (VP9)", (Media.VIDEO,), "webm", "ffmpeg",
        ("-c:v", "libvpx-vp9", "-crf", "32", "-b:v", "0",
         "-c:a", "libopus", "-b:a", "128k"),
        needs=("libvpx-vp9", "libopus"),
    ),
    Preset(
        "mp4-av1", "MP4 (AV1 / smallest)", (Media.VIDEO,), "mp4", "ffmpeg",
        ("-c:v", "libsvtav1", "-crf", "32", "-preset", "6",
         "-c:a", "libopus", "-b:a", "128k"),
        needs=("libsvtav1", "libopus"),
    ),
    Preset(
        "gif", "Animated GIF", (Media.VIDEO,), "gif", "ffmpeg",
        ("-vf", "fps=12,scale=480:-1:flags=lanczos,"
                "split[a][b];[a]palettegen[p];[b][p]paletteuse",
         "-loop", "0"),
    ),
    # ---- video/audio -> audio ------------------------------------------
    Preset(
        "mp3", "MP3", _AV, "mp3", "ffmpeg",
        ("-vn", "-c:a", "libmp3lame", "-q:a", "2"),
        needs=("libmp3lame",),
    ),
    Preset(
        "flac", "FLAC (lossless)", _AV, "flac", "ffmpeg",
        ("-vn", "-c:a", "flac"),
        needs=("flac",),
    ),
    Preset(
        "ogg", "OGG (Vorbis)", _AV, "ogg", "ffmpeg",
        ("-vn", "-c:a", "libvorbis", "-q:a", "5"),
        needs=("libvorbis",),
    ),
    Preset(
        "opus", "Opus", _AV, "opus", "ffmpeg",
        ("-vn", "-c:a", "libopus", "-b:a", "128k"),
        needs=("libopus",),
    ),
    Preset(
        "m4a", "M4A (AAC)", _AV, "m4a", "ffmpeg",
        ("-vn", "-c:a", "aac", "-b:a", "192k"),
        needs=("aac",),
    ),
    Preset(
        "wav", "WAV (uncompressed)", _AV, "wav", "ffmpeg",
        ("-vn", "-c:a", "pcm_s16le"),
        needs=("pcm_s16le",),
    ),
    # ---- image -> image -------------------------------------------------
    Preset("png", "PNG (lossless)", (Media.IMAGE,), "png", "magick"),
    Preset("jpeg", "JPEG", (Media.IMAGE,), "jpg", "magick",
           ("-quality", "90")),
    Preset("webp-img", "WebP", (Media.IMAGE,), "webp", "magick",
           ("-quality", "85")),
    Preset("avif", "AVIF (smallest)", (Media.IMAGE,), "avif", "magick",
           ("-quality", "60")),
    Preset("tiff", "TIFF", (Media.IMAGE,), "tiff", "magick"),
    Preset("pdf", "PDF", (Media.IMAGE,), "pdf", "magick"),
)

PRESETS_BY_ID = {p.id: p for p in PRESETS}


# --------------------------------------------------------------------------
# capability probing
# --------------------------------------------------------------------------

def tool_path(tool: str) -> str | None:
    """Absolute path of an external tool, or None if it is not installed."""
    return shutil.which(tool)


@functools.lru_cache(maxsize=1)
def ffmpeg_encoders() -> frozenset[str]:
    """Encoder names this ffmpeg build actually offers.

    Returns an empty set if ffmpeg is missing or misbehaves; callers treat that
    as "no ffmpeg presets available" rather than crashing.
    """
    exe = tool_path("ffmpeg")
    if not exe:
        return frozenset()
    try:
        out = subprocess.run(
            [exe, "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=20, check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return frozenset()

    names: set[str] = set()
    for line in out.splitlines():
        # Lines look like: " V....D libx264   libx264 H.264 / AVC ..."
        # The flag column is exactly 6 chars; the encoder name is field 2.
        parts = line.split()
        if len(parts) >= 2 and len(parts[0]) == 6 and parts[0][0] in "VAS":
            names.add(parts[1])
    return frozenset(names)


@functools.lru_cache(maxsize=1)
def ffmpeg_muxers() -> frozenset[str]:
    """Output container names this ffmpeg build can write."""
    exe = tool_path("ffmpeg")
    if not exe:
        return frozenset()
    try:
        out = subprocess.run(
            [exe, "-hide_banner", "-muxers"],
            capture_output=True, text=True, timeout=20, check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return frozenset()

    names: set[str] = set()
    for line in out.splitlines():
        # Lines look like: "  E avif            AVIF" or " DE matroska ..."
        parts = line.split()
        if len(parts) >= 2 and parts[0] in {"E", "DE"} and parts[1] != "=":
            names.update(parts[1].split(","))
    return frozenset(names)


# ImageMagick's format names differ from file extensions in a few places.
_MAGICK_ALIASES = {"jpg": "JPEG", "jpe": "JPEG", "tif": "TIFF"}

_MODE = re.compile(r"^[r-][w-][+-]$")


def magick_format(ext: str) -> str:
    """ImageMagick format name for a file extension (with or without dot)."""
    ext = ext.lower().lstrip(".")
    return _MAGICK_ALIASES.get(ext, ext.upper())


@functools.lru_cache(maxsize=1)
def magick_formats() -> dict[str, str]:
    """Format name -> mode string ("rw+", "r--", ...) from `magick -list format`.

    AVIF and HEIC are the reason this exists. ImageMagick's HEIC coder only
    enables reading or writing AVIF when libheif has an AV1 decoder or encoder
    plugin at runtime, so a machine can read AVIF but not write it, or neither.
    When writing is unavailable magick prints "no encode delegate" and can
    still exit 0, so the only reliable check is to ask before running.

    Returns an empty dict if magick is missing or misbehaves; callers treat
    that as "unknown" and let magick try.
    """
    exe = tool_path("magick")
    if not exe:
        return {}
    try:
        out = subprocess.run(
            [exe, "-list", "format"],
            capture_output=True, text=True, timeout=20, check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return {}

    formats: dict[str, str] = {}
    for line in out.splitlines():
        # IM7 with modules: "     AVIF  HEIC      rw+   AV1 Image File Format"
        # IM7 without, IM6: "     AVIF            r--   AV1 Image File Format"
        # The mode is the first r/w/+ triple after the name, wherever the
        # optional module column puts it.
        parts = line.split()
        for token in parts[1:4]:
            if _MODE.match(token):
                formats[parts[0].rstrip("*").upper()] = token
                break
    return formats


def _magick_mode(fmt: str, index: int, char: str) -> bool:
    formats = magick_formats()
    if not formats:
        return True  # could not probe: let magick try and report
    mode = formats.get(fmt.upper())
    return mode is not None and mode[index] == char


def magick_can_read(fmt: str) -> bool:
    return _magick_mode(fmt, 0, "r")


def magick_can_write(fmt: str) -> bool:
    return _magick_mode(fmt, 1, "w")


# Formats that ImageMagick reads through libheif. When its coder cannot read
# one, the file is decoded with ffmpeg instead (AVIF is AV1 in an ISOBMFF
# container, which ffmpeg demuxes natively).
FFMPEG_DECODE_FALLBACK = {".avif", ".heic", ".heif"}

# AV1 encoders usable for a still AVIF when ImageMagick cannot write one,
# best first. Both take -crf on a 0-63 scale.
AVIF_FFMPEG_ENCODERS = ("libsvtav1", "libaom-av1")


def avif_ffmpeg_encoder() -> str | None:
    """The ffmpeg AV1 encoder to use for AVIF output, or None."""
    if "avif" not in ffmpeg_muxers():
        return None
    for name in AVIF_FFMPEG_ENCODERS:
        if name in ffmpeg_encoders():
            return name
    return None


def avif_backend() -> str | None:
    """Which tool writes AVIF here: "magick", "ffmpeg", or None."""
    if not tool_path("magick"):
        return None
    if magick_can_write("AVIF"):
        return "magick"
    if avif_ffmpeg_encoder():
        return "ffmpeg"
    return None


def avif_reader() -> str | None:
    """Which tool reads AVIF here: "magick", "ffmpeg", or None."""
    if tool_path("magick") and magick_can_read("AVIF"):
        return "magick"
    if tool_path("ffmpeg") and tool_path("magick"):
        return "ffmpeg"  # decode support itself is only known by trying
    return None


def is_available(preset: Preset) -> bool:
    """True if this machine can actually run the preset."""
    return not missing_requirements(preset)


def presets_for(media: Media, *, only_available: bool = True) -> list[Preset]:
    """Presets that accept `media`, in menu order."""
    out = [p for p in PRESETS if media in p.accepts]
    if only_available:
        out = [p for p in out if is_available(p)]
    return out


def missing_requirements(preset: Preset) -> list[str]:
    """Human-readable reasons a preset cannot run here."""
    reasons: list[str] = []
    if not tool_path(preset.tool):
        reasons.append(f"{preset.tool} is not installed")
    elif preset.tool == "ffmpeg":
        absent = sorted(set(preset.needs) - ffmpeg_encoders())
        if absent:
            reasons.append("ffmpeg lacks encoder(s): " + ", ".join(absent))
    elif preset.ext == "avif":
        if avif_backend() is None:
            reasons.append(
                "ImageMagick cannot write AVIF (libheif has no AV1 encoder) "
                "and ffmpeg has no AVIF muxer with libsvtav1 or libaom-av1")
    elif not magick_can_write(magick_format(preset.ext)):
        reasons.append(f"ImageMagick cannot write {preset.ext.upper()}")
    return reasons
