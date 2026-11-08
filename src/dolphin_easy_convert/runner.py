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

import json
import os
import re
import signal
import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path

from .jobs import Job
from .presets import (  # noqa: F401 - MAGICK_POLICY_DIR re-exported
    MAGICK_POLICY_DIR, magick_env,
    FFMPEG_DECODE_FALLBACK, avif_backend, avif_ffmpeg_encoder, magick_can_read,
    magick_format, tool_path,
)

# Reports fractional progress in [0.0, 1.0], or None when it cannot be known.
ProgressFn = Callable[[float | None], None]

# Placed before every ffmpeg/ffprobe "-i". Only plain local files may be
# opened, and the restriction is inherited by anything the input opens in
# turn, so a playlist or reference file disguised as a video (HLS, DASH,
# concat, ...) cannot make ffmpeg fetch a URL. Keeps the tool local-only.
FFMPEG_LOCAL_ONLY = ("-protocol_whitelist", "file")


# Common tool errors, in words a person can act on. The raw text is still
# kept for the details pane.
_FRIENDLY = (
    ("No space left on device", "The disk is full."),
    ("Permission denied", "No permission to write in this folder."),
    ("Read-only file system", "This location is read-only."),
    ("moov atom not found", "The file is damaged or was not fully copied."),
    ("Invalid data found when processing input",
     "The file is damaged, or is not really the format its name says."),
    ("no decode delegate", "ImageMagick cannot read this kind of image."),
    ("no encode delegate", "ImageMagick cannot write this format."),
    ("not allowed by the security policy",
     "Blocked by the security policy: this file asks ImageMagick to do "
     "something unsafe (fetch a URL, run a script)."),
    ("Protocol not on whitelist",
     "Blocked: this file tries to load other files or a network address."),
    ("Could not find tag for codec",
     "MP4 cannot hold the codecs in this file without re-encoding. "
     "Use \"MP4 (H.264)\" instead."),
    ("codec not currently supported in container",
     "MP4 cannot hold the codecs in this file without re-encoding. "
     "Use \"MP4 (H.264)\" instead."),
    ("does not contain any stream", "This file has no audio track to extract."),
    ("Decoder (codec", "ffmpeg cannot decode the codec used in this file."),
)


class ToolError(RuntimeError):
    """A conversion failure with a plain-language summary and raw detail."""

    def __init__(self, summary: str, detail: str = "") -> None:
        super().__init__(summary)
        self.summary = summary
        self.detail = detail or summary


def explain(raw: str) -> ToolError:
    """Wrap raw tool output in a ToolError with a readable summary."""
    for needle, summary in _FRIENDLY:
        if needle.lower() in raw.lower():
            return ToolError(summary, raw)
    return ToolError(raw, raw)


class Cancelled(Exception):
    """Raised when the user aborts a running job."""


def probe_duration(path: Path) -> float | None:
    """Length of a media file in seconds, or None if ffprobe cannot say."""
    exe = tool_path("ffprobe")
    if not exe:
        return None
    try:
        res = subprocess.run(
            [exe, "-v", "error", *FFMPEG_LOCAL_ONLY,
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

    # temp_dest keeps the real extension on the end, so ffmpeg selects the
    # muxer from it exactly as it would for the final filename.
    args = list(job.preset.args)
    cover = _cover_stream(job.src) if job.preset.keep_cover else None
    if cover is not None:
        # Keep the album art: main audio plus the embedded picture, copied
        # as-is and flagged as cover art, instead of "-vn" dropping it.
        args.remove("-vn")
        args = ["-map", "0:a:0", "-map", f"0:{cover}", "-c:v", "copy",
                "-disposition:v:0", "attached_pic", *args]

    cmd = [
        exe, "-hide_banner", "-nostdin", "-loglevel", "error",
        "-nostats", "-progress", "pipe:1",
        *FFMPEG_LOCAL_ONLY, "-i", str(job.src),
        *args,
        "-y", str(job.temp_dest),
    ]

    _stream(cmd, job, on_progress, should_cancel,
            parse=lambda line: _parse_ffmpeg_line(line, total))


def _cover_stream(path: Path) -> int | None:
    """Index of an embedded JPEG/PNG cover picture, or None."""
    exe = tool_path("ffprobe")
    if not exe:
        return None
    try:
        res = subprocess.run(
            [exe, "-v", "error", *FFMPEG_LOCAL_ONLY,
             "-show_entries",
             "stream=index,codec_name:stream_disposition=attached_pic",
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=30, check=False,
        )
        streams = json.loads(res.stdout or "{}").get("streams", [])
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    for st in streams:
        if (st.get("disposition", {}).get("attached_pic") == 1
                and st.get("codec_name") in {"mjpeg", "png"}):
            return int(st["index"])
    return None


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
    """Convert one image with ImageMagick 7.

    Up to three stages, each only when needed:

      1. ffmpeg decodes an AVIF/HEIC source that magick's libheif cannot read.
      2. magick converts (always).
      3. ffmpeg encodes AVIF when magick's libheif has no AV1 encoder.

    Intermediate files are hidden scratch files in the destination folder and
    are always removed.
    """
    exe = tool_path("magick")
    if not exe:
        raise RuntimeError("ImageMagick (magick) is not installed")

    decode_first = (job.src.suffix.lower() in FFMPEG_DECODE_FALLBACK
                    and not magick_can_read(magick_format(job.src.suffix)))
    encode_after = job.preset.ext == "avif" and avif_backend() == "ffmpeg"
    if job.preset.ext == "avif" and avif_backend() is None:
        raise RuntimeError("no tool on this system can write AVIF; "
                           "run  dolphin-easy-convert --check")

    stages = 1 + decode_first + encode_after
    done = 0

    def stage_progress(fraction: float | None) -> None:
        if fraction is None:
            on_progress(None)
        else:
            on_progress((done + fraction) / stages)

    scratch: list[Path] = []
    try:
        source = job.src
        if decode_first:
            decoded = job.scratch("decode", "png")
            scratch.append(decoded)
            _ffmpeg_decode_still(job, source, decoded, stage_progress,
                                 should_cancel)
            source = decoded
            done += 1

        args = list(job.preset.args)
        quality = None
        if encode_after:
            # magick only decodes and resizes here; ffmpeg sets the quality.
            if "-quality" in args:
                i = args.index("-quality")
                quality = int(args[i + 1])
                del args[i:i + 2]
            target, out_fmt = job.scratch("encode", "png"), "png"
            scratch.append(target)
        else:
            target, out_fmt = job.temp_dest, job.preset.ext

        def convert(src: Path) -> None:
            # "[0]" takes the first frame, so a multi-page input yields one
            # output file rather than magick writing foo-0.png, foo-1.png...
            spec = f"{src}[0]" if job.preset.ext != "pdf" else str(src)
            cmd = [exe, spec, "-monitor", *args, f"{out_fmt}:{target}"]
            tail = _stream(cmd, job, stage_progress, should_cancel,
                           parse=_parse_magick_line, env=magick_env())
            _require_output(target, tail, "ImageMagick")

        try:
            convert(source)
        except RuntimeError as exc:
            # ImageMagick registers a HEIC reader even when libheif has no
            # HEVC decoder (Fedora's libheif lacks one unless RPM Fusion's
            # libheif-freeworld is installed), so "r" in -list format is not
            # a promise. Retry once via ffmpeg.
            if (decode_first
                    or job.src.suffix.lower() not in FFMPEG_DECODE_FALLBACK
                    or not tool_path("ffmpeg")):
                raise
            decoded = job.scratch("decode", "png")
            scratch.append(decoded)
            try:
                _ffmpeg_decode_still(job, job.src, decoded, stage_progress,
                                     should_cancel)
            except RuntimeError as ff_exc:
                raise ToolError(getattr(exc, "summary", str(exc)),
                                f"ImageMagick: {exc}; {ff_exc}") from exc
            convert(decoded)
        done += 1

        if encode_after:
            _ffmpeg_encode_avif(job, target, quality, stage_progress,
                                should_cancel)
    finally:
        for path in scratch:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


def _require_output(path: Path, tail: list[str], tool: str) -> None:
    """Fail unless `path` exists and is non-empty.

    ImageMagick can exit 0 after a "no encode delegate" warning without
    writing anything, so the exit status alone does not prove success.
    """
    try:
        if path.stat().st_size > 0:
            return
    except OSError:
        pass
    detail = " / ".join(t for t in tail[-4:] if "=" not in t)
    raise explain(detail or f"{tool} exited without writing any output")


def _ffmpeg_still_cmd(src: Path) -> list[str]:
    exe = tool_path("ffmpeg")
    if not exe:
        raise RuntimeError("ffmpeg is not installed")
    return [exe, "-hide_banner", "-nostdin", "-loglevel", "error",
            "-nostats", "-progress", "pipe:1",
            *FFMPEG_LOCAL_ONLY, "-i", str(src), "-frames:v", "1"]


def _ffmpeg_decode_still(job: Job, src: Path, dest: Path,
                         on_progress: ProgressFn,
                         should_cancel: Callable[[], bool]) -> None:
    """Decode the first frame of `src` to a PNG with ffmpeg."""
    cmd = [*_ffmpeg_still_cmd(src), "-update", "1", "-y", str(dest)]
    try:
        tail = _stream(cmd, job, on_progress, should_cancel,
                       parse=lambda line: _parse_ffmpeg_line(line, None))
    except RuntimeError as exc:
        raise RuntimeError(f"could not decode {src.suffix} with ffmpeg "
                           f"either: {exc}") from exc
    _require_output(dest, tail, "ffmpeg")


def _ffmpeg_encode_avif(job: Job, src: Path, quality: int | None,
                        on_progress: ProgressFn,
                        should_cancel: Callable[[], bool]) -> None:
    """Encode a still AVIF with ffmpeg's AV1 encoder.

    `quality` uses ImageMagick's 0-100 scale (higher is better) so the Custom
    dialog behaves the same either way; it is mapped onto CRF 63-0.
    """
    encoder = avif_ffmpeg_encoder()
    if encoder is None:
        raise RuntimeError("ffmpeg has no AV1 encoder for AVIF")
    q = 60 if quality is None else max(0, min(quality, 100))
    crf = round((100 - q) * 63 / 100)

    cmd = [*_ffmpeg_still_cmd(src),
           # 4:2:0 needs even dimensions; pad by at most one pixel.
           "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2",
           "-pix_fmt", "yuv420p", "-c:v", encoder, "-crf", str(crf)]
    if encoder == "libaom-av1":
        cmd += ["-still-picture", "1", "-cpu-used", "4"]
    else:
        cmd += ["-preset", "6"]
    cmd += ["-f", "avif", "-y", str(job.temp_dest)]

    tail = _stream(cmd, job, on_progress, should_cancel,
                   parse=lambda line: _parse_ffmpeg_line(line, None))
    _require_output(job.temp_dest, tail, "ffmpeg")


def _stream(cmd: list[str], job: Job, on_progress: ProgressFn,
            should_cancel: Callable[[], bool],
            parse: Callable[[str], float | None],
            env: dict[str, str] | None = None) -> list[str]:
    """Run a command, feeding each output line to `parse` for a percentage.

    Returns the last few output lines, so callers can explain a run that
    exited 0 but did not do its job.

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
            env=env,
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
        raise explain(detail)
    return tail


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


def _signal_group(proc: subprocess.Popen, sig: int) -> None:
    # The child leads its own process group (start_new_session), so this
    # also reaches helpers it spawned: Ghostscript, rsvg, inkscape...
    try:
        os.killpg(proc.pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def _terminate(proc: subprocess.Popen) -> None:
    """Stop a child and anything it started, politely, then firmly."""
    _signal_group(proc, signal.SIGTERM)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        _signal_group(proc, signal.SIGKILL)


def run(job: Job, on_progress: ProgressFn,
        should_cancel: Callable[[], bool]) -> None:
    """Dispatch a job to the right tool."""
    job.prepare()
    if job.preset.tool == "ffmpeg":
        run_ffmpeg(job, on_progress, should_cancel)
    else:
        run_magick(job, on_progress, should_cancel)
