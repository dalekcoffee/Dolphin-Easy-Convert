"""Command line entry point invoked by the Dolphin service menus.

The .desktop files call this as:

    dolphin-easy-convert --preset mp4-h264 %F
    dolphin-easy-convert --custom %F

`%F` hands over every selected path in a single invocation (verified on
Plasma 6.7.3), so one run of this program handles the whole batch and shows one
progress window.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

from . import __version__
from .jobs import media_of
from .presets import (
    Media, PRESETS_BY_ID, avif_backend, avif_ffmpeg_encoder, avif_reader,
    ffmpeg_encoders, magick_formats, missing_requirements, tool_path,
)

REQUIRED_PACKAGES = {
    "ffmpeg": "ffmpeg",
    "ffprobe": "ffmpeg",
    "magick": "ImageMagick",
}


def _fail(message: str) -> int:
    """Report a startup problem, graphically when a desktop is present."""
    print(f"dolphin-easy-convert: {message}", file=sys.stderr)
    kdialog = tool_path("kdialog")
    if kdialog and os.environ.get("DISPLAY", os.environ.get("WAYLAND_DISPLAY")):
        subprocess.run([kdialog, "--title", "Easy Convert",
                        "--error", message], check=False)
    return 1


def check(verbose: bool = True) -> int:
    """Verify the environment. Reports; never installs anything itself."""
    problems: list[str] = []
    notes: list[str] = []

    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "")
    if "KDE" in desktop.upper():
        notes.append(f"desktop         : {desktop}")
    else:
        notes.append(f"desktop         : {desktop or 'unknown'} "
                     f"(expected KDE — the menus will not appear elsewhere)")

    for binary, package in REQUIRED_PACKAGES.items():
        path = tool_path(binary)
        if path:
            notes.append(f"{binary:<16}: {path}")
        else:
            problems.append(f"{binary} is missing (dnf package: {package})")

    try:
        import PyQt6.QtWidgets  # noqa: F401
        notes.append("PyQt6           : present")
    except ImportError:
        problems.append("PyQt6 is missing (dnf package: python3-pyqt6)")

    encoders = ffmpeg_encoders()
    if encoders:
        interesting = sorted(
            e for e in ("libx264", "libx265", "libsvtav1", "libvpx-vp9",
                        "aac", "libopus", "libvorbis", "libmp3lame", "flac")
            if e in encoders
        )
        notes.append("encoders        : " + (", ".join(interesting) or "none"))

    if tool_path("magick"):
        reader, writer = avif_reader(), avif_backend()
        notes.append("AVIF read       : " + {
            "magick": "ImageMagick (libheif)",
            "ffmpeg": "via ffmpeg (ImageMagick's libheif cannot decode AV1)",
        }.get(reader, "unavailable"))
        notes.append("AVIF write      : " + {
            "magick": "ImageMagick (libheif)",
            "ffmpeg": f"via ffmpeg {avif_ffmpeg_encoder()} (ImageMagick's "
                      "libheif has no AV1 encoder; transparency is dropped)",
        }.get(writer, "unavailable — the AVIF preset is hidden"))
        if "AVIF" not in magick_formats() and magick_formats():
            notes.append("                  ImageMagick has no HEIC/AVIF coder:"
                         "  sudo dnf install ImageMagick-heic")
        elif reader != "magick" or writer != "magick":
            notes.append("                  libheif lacks an AV1 plugin; see"
                         "  dnf list 'libheif*'")

    from .runner import MAGICK_POLICY_DIR
    policy = MAGICK_POLICY_DIR / "policy.xml"
    if policy.is_file():
        notes.append("local only      : yes — ffmpeg limited to local files; "
                     "ImageMagick network/script coders blocked")
    else:
        problems.append(f"ImageMagick safety policy missing: {policy} "
                        "(reinstall the package)")

    if verbose:
        print(f"Easy Convert {__version__}")
        for note in notes:
            print("  " + note)

    if problems:
        print("\nProblems found:", file=sys.stderr)
        for problem in problems:
            print("  - " + problem, file=sys.stderr)
        packages = sorted({
            p.rsplit("dnf package: ", 1)[1].rstrip(")")
            for p in problems if "dnf package: " in p
        })
        if packages:
            print("\nInstall the missing pieces with:\n"
                  f"  sudo dnf install {' '.join(packages)}", file=sys.stderr)
        return 1

    if verbose:
        print("\nAll good.")
    return 0


def _dominant_media(paths: list[Path]) -> Media | None:
    """The media kind most of the selection belongs to."""
    kinds = Counter(m for m in (media_of(p) for p in paths) if m is not None)
    if not kinds:
        return None
    return kinds.most_common(1)[0][0]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="dolphin-easy-convert",
        description="Convert media files with ffmpeg and ImageMagick.",
    )
    parser.add_argument("--preset", help="preset id, e.g. mp4-h264")
    parser.add_argument("--custom", action="store_true",
                        help="open the dialog with format options")
    parser.add_argument("--check", action="store_true",
                        help="verify ffmpeg, ImageMagick, PyQt6 and KDE")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("files", nargs="*", type=Path)
    args = parser.parse_args(argv)

    if args.check:
        return check()

    # Absolute, so a relative name such as "-x.mkv" can never reach a tool as
    # an option; de-duplicated (order kept), so one file is not converted, or
    # in "originals" mode moved, twice.
    paths = list(dict.fromkeys(p.absolute() for p in args.files))
    if not paths:
        return _fail("no files given")

    missing = [p for p in paths if not p.exists()]
    if missing:
        return _fail("file not found: " + ", ".join(str(p) for p in missing))

    preset = None
    if args.preset:
        preset = PRESETS_BY_ID.get(args.preset)
        if preset is None:
            return _fail(f"unknown preset {args.preset!r}")
        blockers = missing_requirements(preset)
        if blockers:
            return _fail(f"cannot use {preset.label}: " + "; ".join(blockers))

    media = _dominant_media(paths)
    if media is None:
        return _fail("none of the selected files are a supported "
                     "audio, video or image type")

    try:
        from .ui import launch
    except ImportError:
        return _fail("PyQt6 is not installed — run:  "
                     "sudo dnf install python3-pyqt6")

    return launch(paths, preset, media, args.custom)


if __name__ == "__main__":
    sys.exit(main())
