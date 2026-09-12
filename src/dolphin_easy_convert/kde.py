"""What KDE this machine is running, and whether it can show the menu at all.

Everything this package does is invisible if KIO declines to draw the menu
entry, and KIO has shipped at least one release where it declined to. Rather
than leave that looking like a broken install, the version is probed here and
`--check` reports it.
"""

from __future__ import annotations

import functools
import os
import subprocess
from pathlib import Path

Version = tuple[int, int, int]

#: KIO 6.29 built the submenu with the wrong parent object, which was deleted
#: before the menu was shown, taking every entry grouped under an
#: `X-KDE-Submenu` with it. Menu files were unaffected and reinstalling them
#: changed nothing; the entries were simply never drawn.
#:
#: KDE bug 524239 (Dolphin bug 525484 is the same fault seen from the other
#: side), regressed by 148b9253d in Frameworks 6.29.0 (2026-08-14), fixed by
#: 774defb94 in Frameworks 6.30.0 (2026-09-09).
SUBMENU_BROKEN_FROM: Version = (6, 29, 0)
SUBMENU_FIXED_IN: Version = (6, 30, 0)

_KIO_WIDGETS_SONAME = "libKF6KIOWidgets.so"
_LIB_DIRS = ("/usr/lib64", "/usr/lib", "/usr/local/lib64", "/usr/local/lib")
_KIO_PACKAGES = ("kf6-kio-widgets-libs", "kf6-kio-widgets",
                 "kf6-kio-core-libs", "kf6-kio")

SERVICEMENU_SUBDIR = "kio/servicemenus"
MENU_GLOB = "dolphin-easy-convert-*.desktop"


def format_version(version: Version) -> str:
    return ".".join(str(part) for part in version)


def _parse(text: str) -> Version | None:
    """Turn '6.29.0' into (6, 29, 0). Anything else is None."""
    parts = text.strip().split(".")
    if len(parts) < 2:
        return None
    try:
        numbers = [int(part) for part in parts[:3]]
    except ValueError:
        return None
    while len(numbers) < 3:
        numbers.append(0)
    return (numbers[0], numbers[1], numbers[2])


def _from_soname() -> Version | None:
    """Read the version out of the KIOWidgets library file name.

    KF6 libraries carry the Frameworks release in their soname
    (libKF6KIOWidgets.so.6.29.0), so this reports the library that would
    actually be loaded rather than what a package database claims.
    """
    found: list[Version] = []
    for directory in _LIB_DIRS:
        base = Path(directory)
        candidates = list(base.glob(f"{_KIO_WIDGETS_SONAME}.6.*"))
        link = base / f"{_KIO_WIDGETS_SONAME}.6"
        if link.is_symlink():
            candidates.append(Path(os.readlink(link)))
        for candidate in candidates:
            _, _, tail = candidate.name.partition(".so.")
            version = _parse(tail)
            if version and version[0] == 6:
                found.append(version)
    return max(found) if found else None


def _from_rpm() -> Version | None:
    """Ask rpm, for the case where the library sits somewhere unexpected."""
    for package in _KIO_PACKAGES:
        try:
            result = subprocess.run(
                ["rpm", "-q", "--qf", "%{VERSION}\n", package],
                capture_output=True, text=True, timeout=10, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if result.returncode != 0:
            continue
        for line in result.stdout.splitlines():
            version = _parse(line)
            if version:
                return version
    return None


@functools.lru_cache(maxsize=1)
def kio_version() -> Version | None:
    """The installed KIO (KDE Frameworks) version, or None if it can't be told."""
    return _from_soname() or _from_rpm()


def submenu_is_broken(version: Version | None) -> bool:
    """True when this KIO drops entries grouped under X-KDE-Submenu."""
    if version is None:
        return False
    return SUBMENU_BROKEN_FROM <= version < SUBMENU_FIXED_IN


def _data_dirs() -> list[Path]:
    """XDG data directories, most specific first."""
    home = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local/share")
    system = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    dirs = [Path(home)] + [Path(d) for d in system.split(":") if d]
    seen: set[Path] = set()
    unique = []
    for directory in dirs:
        if directory not in seen:
            seen.add(directory)
            unique.append(directory)
    return unique


def installed_servicemenus() -> list[Path]:
    """Every Easy Convert menu file KIO would read, wherever it was installed."""
    found: list[Path] = []
    for directory in _data_dirs():
        found.extend(sorted((directory / SERVICEMENU_SUBDIR).glob(MENU_GLOB)))
    return found
