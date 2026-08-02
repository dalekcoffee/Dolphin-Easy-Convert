"""Running ffmpeg / ImageMagick and turning their chatter into a percentage.

Every command is built as an argument list and run without a shell, so a
filename containing spaces, quotes, newlines or shell metacharacters is passed
through untouched and never interpreted.

The progress parsers are deliberately forgiving. ffmpeg's `-progress` key set
has varied across releases (notably `out_time_ms`, which has historically
carried microseconds despite its name), so we prefer the unambiguous keys and
fall back to an indeterminate bar rather than reporting a wrong number.
"""

from __future__ import annotations

import re
import signal
import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path

from .jobs import Job
from .presets import tool_path

# Reports fractional progress in [0.0, 1.0], or None when it cannot be known.
ProgressFn = Callable[[float | None], None]


class Cancelled(Exception):
    """Raised when the user aborts a running job."""


def probe_duration(path: Path) -> float | None:
    """Length of a media file in seconds, or None if ffprobe cannot say."""
    exe = tool_path("ffprobe")
    if not exe:
        return None
    try:
        res = subprocess.run(
            [exe, "-v", "error",
             "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=30, check=False,
        )
        value = float(res.stdout.strip())
        return value if value > 0 else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _hms_to_seconds(text: str) -> float | None:
    """Parse ffmpeg's `HH:MM:SS.microseconds` timestamp form."""
    parts = text.strip().split(":")
    try:
        seconds = 0.0
        for part in parts:
            seconds = seconds * 60.0 + float(part)
    except ValueError:
        return None
    return seconds


def _ffmpeg_position(key: str, value: str) -> float | None:
    """Current output position in seconds from one `-progress` key=value pair.

    `out_time_us` is unambiguous. `out_time` is a readable timestamp. Both are
    trusted ahead of `out_time_ms`, whose units have not been consistent.
    """
    if key == "out_time_us":
        try:
            return int(value) / 1_000_000.0
        except ValueError:
            return None
    if key == "out_time":
        return _hms_to_seconds(value)
    return None


def run_ffmpeg(job: Job, on_progress: ProgressFn,
               should_cancel: Callable[[], bool]) -> None:
    """Encode one file with ffmpeg, reporting progress as it goes."""
    exe = tool_path("ffmpeg")
    if not exe:
        raise RuntimeError("ffmpeg is not installed")

    total = probe_duration(job.src)
    job.temp_dest.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        exe, "-hide_banner", "-nostdin", "-loglevel", "error",
        "-nostats", "-progress", "pipe:1",
        "-i", str(job.src),
        *job.preset.args,
        "-y", str(job.temp_dest),
    ]

    # ffmpeg infers the container from the extension; temp_dest is a dotfile
    # with the real extension buried, so state it explicitly.
    cmd[-1:-1] = ["-f", _container_for(job.preset.ext)] if _container_for(
        job.preset.ext) else []

    _stream(cmd, job, on_progress, should_cancel,
            parse=lambda line: _parse_ffmpeg_line(line, total))


def _container_for(ext: str) -> str | None:
    """ffmpeg muxer name for an output extension, when it is not the ext."""
    return {"m4a": "ipod", "jpg": "image2"}.get(ext)


def _parse_ffmpeg_line(line: str, total: float | None) -> float | None:
    if "=" not in line:
        return None
    key, _, value = line.partition("=")
    key, value = key.strip(), value.strip()
    if key == "progress" and value == "end":
        return 1.0
    if total is None:
        return None
    position = _ffmpeg_position(key, value)
    if position is None:
        return None
    return max(0.0, min(position / total, 1.0))


# ImageMagick -monitor writes lines such as
#   "  load image[0]: 512 of 1024, 50% complete"
# but the exact wording varies by version and operation, so match either the
# "N of M" pair or a bare percentage, whichever appears.
_IM_OF = re.compile(r"(\d+)\s+of\s+(\d+)")
_IM_PCT = re.compile(r"(\d+(?:\.\d+)?)%")
_IM_DECIMAL = re.compile(r"^\s*(0(?:\.\d+)?|1(?:\.0+)?)\s*$")


def _parse_magick_line(line: str, _total: float | None = None) -> float | None:
    match = _IM_OF.search(line)
    if match:
        done, whole = int(match.group(1)), int(match.group(2))
        if whole > 0:
            return max(0.0, min(done / whole, 1.0))
    match = _IM_PCT.search(line)
    if match:
        return max(0.0, min(float(match.group(1)) / 100.0, 1.0))
    match = _IM_DECIMAL.match(line)
    if match:
        return max(0.0, min(float(match.group(1)), 1.0))
    return None


def run_magick(job: Job, on_progress: ProgressFn,
               should_cancel: Callable[[], bool]) -> None:
    """Convert one image with ImageMagick 7."""
    exe = tool_path("magick")
    if not exe:
        raise RuntimeError("ImageMagick (magick) is not installed")

    job.temp_dest.parent.mkdir(parents=True, exist_ok=True)

    # "[0]" takes the first frame, so a multi-page input yields one output
    # file rather than magick silently writing foo-0.png, foo-1.png, ...
    source = f"{job.src}[0]" if job.preset.ext != "pdf" else str(job.src)

    cmd = [exe, source, "-monitor", *job.preset.args,
           f"{job.preset.ext}:{job.temp_dest}"]

    _stream(cmd, job, on_progress, should_cancel, parse=_parse_magick_line)


def _stream(cmd: list[str], job: Job, on_progress: ProgressFn,
            should_cancel: Callable[[], bool],
            parse: Callable[[str], float | None]) -> None:
    """Run a command, feeding each output line to `parse` for a percentage.

    stdout and stderr are merged because ffmpeg writes progress to stdout while
    ImageMagick writes it to stderr, and both write errors to stderr.
    """
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            errors="replace",
            bufsize=1,
            start_new_session=True,  # so cancelling cannot signal our own group
        )
    except OSError as exc:
        raise RuntimeError(f"could not start {cmd[0]}: {exc}") from exc

    tail: list[str] = []  # keep the last few lines to explain a failure
    try:
        assert proc.stdout is not None
        for raw in _lines(proc.stdout):
            if should_cancel():
                _terminate(proc)
                raise Cancelled
            line = raw.rstrip("\r\n")
            if line:
                tail.append(line)
                del tail[:-12]
            fraction = parse(line)
            if fraction is not None:
                on_progress(fraction)
    finally:
        if proc.poll() is None:
            _terminate(proc)
        proc.wait()

    if proc.returncode != 0:
        detail = " / ".join(t for t in tail[-4:] if "=" not in t) or \
                 f"exit status {proc.returncode}"
        raise RuntimeError(detail)


def _lines(stream) -> Iterator[str]:
    """Yield output lines, splitting on \\r as well as \\n.

    ImageMagick redraws its monitor line with carriage returns, so plain
    iteration would block until the operation finished.
    """
    buffer = ""
    while True:
        chunk = stream.read(1)
        if not chunk:
            break
        if chunk in "\r\n":
            if buffer:
                yield buffer
                buffer = ""
        else:
            buffer += chunk
    if buffer:
        yield buffer


def _terminate(proc: subprocess.Popen) -> None:
    """Stop a child politely, then firmly."""
    try:
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    except OSError:
        pass


def run(job: Job, on_progress: ProgressFn,
        should_cancel: Callable[[], bool]) -> None:
    """Dispatch a job to the right tool."""
    if job.preset.tool == "ffmpeg":
        run_ffmpeg(job, on_progress, should_cancel)
    else:
        run_magick(job, on_progress, should_cancel)
