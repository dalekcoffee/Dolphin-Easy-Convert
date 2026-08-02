"""Where converted files go, and what happens to the originals.

This is the file to read if you want to know whether this program can lose your
data. The rules it enforces:

  * Nothing is ever deleted. The only destructive-looking mode *moves* an
    original into a subdirectory, and only after its conversion succeeded.
  * Nothing is ever overwritten. A name collision gets a numeric suffix.
  * Conversion writes to a temporary file first. The result is only put in
    place once the encoder exits successfully, so an interrupted or failed run
    cannot leave a half-written file where a real one belongs.
"""

from __future__ import annotations

import dataclasses
import os
from enum import Enum
from pathlib import Path

from .presets import Media, Preset

# Input extensions we are willing to hand to each tool. Also drives the
# MimeType lists in the .desktop files.
VIDEO_EXT = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".wmv", ".flv", ".m4v",
             ".mpg", ".mpeg", ".ts", ".m2ts", ".ogv", ".3gp"}
AUDIO_EXT = {".mp3", ".flac", ".wav", ".ogg", ".oga", ".opus", ".m4a", ".aac",
             ".wma", ".aiff", ".aif", ".ape", ".mka"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".avif", ".tif", ".tiff",
             ".bmp", ".gif", ".heic", ".heif", ".ppm", ".pgm", ".tga", ".ico",
             ".svg", ".psd", ".xcf", ".jxl"}


class OutputMode(str, Enum):
    """What to do with the files once a conversion succeeds."""

    SUBDIR_CONVERTED = "converted-subdir"   # new files -> ./converted/
    SUBDIR_ORIGINALS = "originals-subdir"   # old files -> ./originals/
    INPLACE_SUFFIX = "inplace-suffix"       # everything stays, "-conv" added


MODE_LABELS = {
    OutputMode.SUBDIR_CONVERTED:
        'Put converted files in a "converted" subfolder',
    OutputMode.SUBDIR_ORIGINALS:
        'Keep converted files here, move originals to "originals"',
    OutputMode.INPLACE_SUFFIX:
        'Keep everything here, add "-conv" to converted files',
}

CONVERTED_DIR = "converted"
ORIGINALS_DIR = "originals"
SUFFIX = "-conv"


def media_of(path: Path) -> Media | None:
    """Classify a file by extension, or None if we do not handle it."""
    ext = path.suffix.lower()
    if ext in VIDEO_EXT:
        return Media.VIDEO
    if ext in AUDIO_EXT:
        return Media.AUDIO
    if ext in IMAGE_EXT:
        return Media.IMAGE
    return None


@dataclasses.dataclass
class Job:
    """One input file heading for one output file."""

    src: Path
    dest: Path
    preset: Preset
    move_original_to: Path | None = None  # set only in SUBDIR_ORIGINALS mode

    # filled in as the job runs
    status: str = "pending"  # pending | running | done | failed | skipped
    message: str = ""

    @property
    def temp_dest(self) -> Path:
        """Scratch path written during encoding, in the destination dir.

        Same filesystem as `dest`, so the final placement is a cheap rename
        rather than a copy, and a crash leaves this file rather than a
        corrupt `dest`.

        The real extension stays on the end. ffmpeg picks its output muxer from
        the filename, so a scratch name like ".clip.flac.part" leaves it unable
        to choose a format and the encode fails before it starts.
        """
        return self.dest.with_name(
            f".{self.dest.stem}.dec-part-{os.getpid()}{self.dest.suffix}"
        )


def _unique(path: Path) -> Path:
    """First non-existing name at or after `path`, by appending -1, -2, ..."""
    if not path.exists():
        return path
    stem, suffix, parent = path.stem, path.suffix, path.parent
    for n in range(1, 10_000):
        candidate = parent / f"{stem}-{n}{suffix}"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"could not find a free filename near {path}")


def plan(paths: list[Path], preset: Preset, mode: OutputMode) -> list[Job]:
    """Work out every source -> destination pair before anything runs.

    Planning up front means the UI can show the full list, and a collision or
    an unsupported file is reported before a single byte is written.
    """
    jobs: list[Job] = []
    for src in paths:
        media = media_of(src)
        if media is None:
            jobs.append(Job(src, src, preset, status="skipped",
                            message="unrecognised file type"))
            continue
        if media not in preset.accepts:
            jobs.append(Job(src, src, preset, status="skipped",
                            message=f"{preset.label} does not accept "
                                    f"{media.value} files"))
            continue
        if not src.is_file():
            jobs.append(Job(src, src, preset, status="skipped",
                            message="not a regular file"))
            continue

        if mode is OutputMode.SUBDIR_CONVERTED:
            out_dir = src.parent / CONVERTED_DIR
            dest = _unique(out_dir / f"{src.stem}.{preset.ext}")
            jobs.append(Job(src, dest, preset))

        elif mode is OutputMode.SUBDIR_ORIGINALS:
            dest = _unique(src.parent / f"{src.stem}.{preset.ext}")
            # Converting to the same extension would otherwise target the
            # source itself; _unique already stepped aside, and the original
            # only moves after success.
            keep = _unique(src.parent / ORIGINALS_DIR / src.name)
            jobs.append(Job(src, dest, preset, move_original_to=keep))

        else:  # INPLACE_SUFFIX
            dest = _unique(src.parent / f"{src.stem}{SUFFIX}.{preset.ext}")
            jobs.append(Job(src, dest, preset))

    return jobs


def finalise(job: Job) -> None:
    """Put a finished conversion in place. Called only after the tool exits 0.

    Ordering matters: the new file is moved into position first, and only then
    is the original relocated. If anything raises, the original has not moved.
    """
    job.dest.parent.mkdir(parents=True, exist_ok=True)
    os.replace(job.temp_dest, job.dest)

    if job.move_original_to is not None:
        job.move_original_to.parent.mkdir(parents=True, exist_ok=True)
        os.replace(job.src, job.move_original_to)


def cleanup(job: Job) -> None:
    """Remove the scratch file after a failure or a cancellation.

    Only ever touches `temp_dest`, which this process created and named.
    """
    try:
        job.temp_dest.unlink(missing_ok=True)
    except OSError:
        pass
