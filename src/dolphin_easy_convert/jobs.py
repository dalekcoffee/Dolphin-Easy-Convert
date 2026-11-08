"""Where converted files go, and what happens to the originals.

This is the file to read if you want to know whether this program can lose your
data. The rules it enforces:

  * Nothing is ever deleted. The only destructive-looking mode *moves* an
    original into a subdirectory, and only after its conversion succeeded.
  * Nothing is ever overwritten. A name collision gets a numeric suffix,
    including collisions between files in the same batch, and the final
    move re-checks atomically in case a file appeared meanwhile.
  * Conversion writes to a temporary file first. The result is only put in
    place once the encoder exits successfully, so an interrupted or failed run
    cannot leave a half-written file where a real one belongs.
"""

from __future__ import annotations

import dataclasses
import os
import secrets
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

    # Scratch file the tool writes to; created by prepare(), never guessed.
    temp_dest: Path | None = None

    def prepare(self) -> Path:
        """Create the scratch file the encoder writes to, in the dest dir.

        Same filesystem as `dest`, so the final placement is a cheap rename
        rather than a copy, and a crash leaves this file rather than a
        corrupt `dest`.

        The name is random and the file is created exclusively (O_EXCL,
        O_NOFOLLOW), so in a folder other users can write to, nobody can
        pre-plant a symlink at a predictable name and have the encoder write
        through it.

        The real extension stays on the end. ffmpeg picks its output muxer from
        the filename, so a scratch name like ".clip.flac.part" leaves it unable
        to choose a format and the encode fails before it starts.
        """
        self.dest.parent.mkdir(parents=True, exist_ok=True)
        self.temp_dest = _create_hidden(self.dest.parent, self.dest.stem,
                                        self.dest.suffix)
        return self.temp_dest

    def scratch(self, tag: str, ext: str) -> Path:
        """Create an intermediate file for a two-step conversion.

        Hidden, created the same safe way as temp_dest, and removed by the
        runner whether the conversion succeeds or not.
        """
        self.dest.parent.mkdir(parents=True, exist_ok=True)
        return _create_hidden(self.dest.parent, f"{self.dest.stem}-{tag}",
                              f".{ext}")


def _create_hidden(folder: Path, stem: str, suffix: str) -> Path:
    """Exclusively create an empty hidden file with a random name."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
    for _ in range(100):
        path = folder / f".{stem}.dec-part-{secrets.token_hex(4)}{suffix}"
        try:
            fd = os.open(path, flags, 0o666)  # umask applies, as for any file
        except FileExistsError:
            continue
        os.close(fd)
        return path
    raise RuntimeError(f"could not create a scratch file in {folder}")


def _taken(path: Path, reserved: set[Path]) -> bool:
    # lexists, not exists: a dangling symlink still occupies the name.
    return path in reserved or os.path.lexists(path)


def _unique(path: Path, reserved: set[Path] | None = None) -> Path:
    """First free name at or after `path`, by appending -1, -2, ...

    `reserved` holds names already promised to earlier files in the same
    batch, so two inputs such as clip.mkv and clip.mov cannot both be planned
    to land on clip.mp4.
    """
    reserved = reserved if reserved is not None else set()
    if not _taken(path, reserved):
        return path
    stem, suffix, parent = path.stem, path.suffix, path.parent
    for n in range(1, 10_000):
        candidate = parent / f"{stem}-{n}{suffix}"
        if not _taken(candidate, reserved):
            return candidate
    raise RuntimeError(f"could not find a free filename near {path}")


def _place(src: Path, wanted: Path) -> Path:
    """Move `src` to `wanted`, or the next free name, never replacing a file.

    A hard link fails atomically if the name is taken, which closes the gap
    between checking a name and using it. Filesystems without hard links
    (FAT/exFAT USB sticks, some network mounts) fall back to check-then-rename.
    Returns where the file ended up.
    """
    dest = wanted
    for _ in range(10_000):
        try:
            os.link(src, dest, follow_symlinks=False)
        except FileExistsError:
            dest = _unique(wanted)
            continue
        except OSError:
            if os.path.lexists(dest):
                dest = _unique(wanted)
                continue
            os.rename(src, dest)
            return dest
        os.unlink(src)
        return dest
    raise RuntimeError(f"could not find a free filename near {wanted}")


# Leading bytes of formats ImageMagick would hand to a script interpreter or
# an external delegate (SVG/MSL/MVG renderers, Ghostscript for PostScript and
# PDF). ImageMagick picks the decoder from a file's content, not its name, so
# a download named photo.png that is really one of these would otherwise be
# run through that interpreter: the "ImageTragick" class of attack.
_SCRIPT_MAGIC = (b"<", b"%!", b"%pdf", b"push ", b"viewbox ", b"\x04%!",
                 b"\xc5\xd0\xd3\xc6")  # last: DOS EPS binary header


def _content_mismatch(src: Path) -> str | None:
    """Why an image's content is refused, or None if it looks fine."""
    if src.suffix.lower() == ".svg":
        return None  # SVG is expected to be XML; that is its real format
    try:
        with open(src, "rb") as fh:
            head = fh.read(64)
    except OSError as exc:
        return f"cannot read file: {exc.strerror}"
    head = head.lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    if head.startswith(_SCRIPT_MAGIC):
        return (f"content is XML, PostScript, PDF or drawing commands, not a "
                f"{src.suffix} image; refusing to pass it to ImageMagick")
    return None


def plan(paths: list[Path], preset: Preset, mode: OutputMode) -> list[Job]:
    """Work out every source -> destination pair before anything runs.

    Planning up front means the UI can show the full list, and a collision or
    an unsupported file is reported before a single byte is written.
    """
    jobs: list[Job] = []
    reserved: set[Path] = set()
    for src in paths:
        # Absolute paths always start with "/", so no filename can be read
        # as a command-line option ("-x.mkv") or as an ImageMagick coder or
        # ffmpeg protocol prefix ("msl:x.png", "concat:a|b").
        src = src.absolute()
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
        if media is Media.IMAGE:
            refused = _content_mismatch(src)
            if refused:
                jobs.append(Job(src, src, preset, status="skipped",
                                message=refused))
                continue

        if mode is OutputMode.SUBDIR_CONVERTED:
            out_dir = src.parent / CONVERTED_DIR
            dest = _unique(out_dir / f"{src.stem}.{preset.ext}", reserved)
            jobs.append(Job(src, dest, preset))

        elif mode is OutputMode.SUBDIR_ORIGINALS:
            dest = _unique(src.parent / f"{src.stem}.{preset.ext}", reserved)
            # Converting to the same extension would otherwise target the
            # source itself; _unique already stepped aside, and the original
            # only moves after success.
            keep = _unique(src.parent / ORIGINALS_DIR / src.name, reserved)
            reserved.add(keep)
            jobs.append(Job(src, dest, preset, move_original_to=keep))

        else:  # INPLACE_SUFFIX
            dest = _unique(src.parent / f"{src.stem}{SUFFIX}.{preset.ext}",
                           reserved)
            jobs.append(Job(src, dest, preset))
        reserved.add(jobs[-1].dest)

    return jobs


def finalise(job: Job) -> None:
    """Put a finished conversion in place. Called only after the tool exits 0.

    Ordering matters: the new file is moved into position first, and only then
    is the original relocated. If anything raises, the original has not moved.
    """
    if job.temp_dest is None:
        raise RuntimeError("internal error: job finished without a scratch file")
    job.dest.parent.mkdir(parents=True, exist_ok=True)
    # Planned names were free at planning time; _place re-checks atomically,
    # so a file that appeared since is stepped around, never replaced.
    job.dest = _place(job.temp_dest, job.dest)
    job.temp_dest = None

    if job.move_original_to is not None:
        job.move_original_to.parent.mkdir(parents=True, exist_ok=True)
        job.move_original_to = _place(job.src, job.move_original_to)


def cleanup(job: Job) -> None:
    """Remove the scratch file after a failure or a cancellation.

    Only ever touches `temp_dest`, which this process created and named.
    """
    if job.temp_dest is None:
        return
    try:
        job.temp_dest.unlink(missing_ok=True)
    except OSError:
        pass
    job.temp_dest = None
