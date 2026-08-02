#!/usr/bin/env bash
# probe-system.sh — read-only fact-finder for dolphin-easy-convert.
#
# Answers the questions that could not be verified from official docs, by
# asking the only authoritative source available: your actual Fedora install.
#
# This script is READ-ONLY. It does not use sudo, does not install, remove or
# modify any package, and writes nothing outside a temp dir it cleans up.
# Read it before you run it.
#
# Usage:  bash tools/probe-system.sh          (paste the whole output back)

set -uo pipefail

hr() { printf '\n=== %s ===\n' "$1"; }
have() { command -v "$1" >/dev/null 2>&1; }

echo "dolphin-easy-convert system probe"
echo "generated: $(date -Is)"

hr "1. DISTRO + DESKTOP"
grep -E '^(NAME|VERSION_ID|VARIANT|PRETTY_NAME)=' /etc/os-release 2>/dev/null
echo "XDG_CURRENT_DESKTOP = ${XDG_CURRENT_DESKTOP:-<unset>}"
echo "KDE_SESSION_VERSION = ${KDE_SESSION_VERSION:-<unset>}"
echo "XDG_SESSION_TYPE    = ${XDG_SESSION_TYPE:-<unset>}"
have plasmashell && echo "plasmashell: $(plasmashell --version 2>&1 | head -1)" || echo "plasmashell: NOT FOUND"
have dolphin && echo "dolphin:     $(dolphin --version 2>&1 | head -1)" || echo "dolphin:     NOT FOUND"

hr "2. SERVICEMENU DIRECTORIES (which exist, who owns them)"
for d in /usr/share/kio/servicemenus \
         /usr/share/kservices5/ServiceMenus \
         "$HOME/.local/share/kio/servicemenus" \
         "$HOME/.local/share/kservices5/ServiceMenus"; do
  if [ -d "$d" ]; then
    n=$(find "$d" -maxdepth 1 -name '*.desktop' 2>/dev/null | wc -l)
    echo "EXISTS  $d  (${n} .desktop files)"
    case "$d" in
      /usr/*) echo "        rpm owner: $(rpm -qf "$d" 2>&1 | head -1)" ;;
    esac
  else
    echo "absent  $d"
  fi
done
echo
echo "-- exec bits on any existing system servicemenus (does KDE require +x here?) --"
find /usr/share/kio/servicemenus -maxdepth 1 -name '*.desktop' -printf '%M  %p\n' 2>/dev/null | head -10 \
  || echo "   (none to sample)"
echo
echo "-- a real-world example, for the KF6 key layout Dolphin actually accepts --"
_ex=$(find /usr/share/kio/servicemenus -maxdepth 1 -name '*.desktop' 2>/dev/null | head -1)
if [ -n "${_ex:-}" ]; then
  echo "   sample: $_ex  (owner: $(rpm -qf "$_ex" 2>&1 | head -1))"
  sed -n '1,40p' "$_ex"
else
  echo "   no system servicemenu installed to sample"
fi

hr "3. IMAGEMAGICK"
for b in magick convert identify; do
  if have "$b"; then
    echo "$b -> $(command -v "$b")   [rpm: $(rpm -qf "$(command -v "$b")" 2>&1 | head -1)]"
  else
    echo "$b -> NOT FOUND"
  fi
done
have magick && { echo "--- magick --version ---"; magick --version 2>&1 | head -3; }
if ! have magick && have convert; then
  echo "--- convert --version (v6 fallback) ---"; convert --version 2>&1 | head -3
fi
echo "--- installed ImageMagick rpms ---"
rpm -qa 'ImageMagick*' 2>/dev/null | sort || echo "(rpm query failed)"

hr "4. FFMPEG FLAVOR + ENCODERS"
if have ffmpeg; then
  echo "ffmpeg -> $(command -v ffmpeg)   [rpm: $(rpm -qf "$(command -v ffmpeg)" 2>&1 | head -1)]"
  ffmpeg -hide_banner -version 2>&1 | head -2
  echo "--- installed ffmpeg rpms (tells us free vs RPM Fusion) ---"
  rpm -qa '*ffmpeg*' 2>/dev/null | sort
  echo "--- vendor of the ffmpeg package ---"
  rpm -qi "$(rpm -qf --qf '%{NAME}' "$(command -v ffmpeg)" 2>/dev/null)" 2>/dev/null \
    | grep -E '^(Vendor|Packager|From repo|Name)' || true
  echo "--- ENCODER AVAILABILITY (the decisive list) ---"
  for enc in libx264 libx265 aac libfdk_aac libmp3lame libopus libvorbis \
             libvpx-vp9 libsvtav1 libaom-av1 libwebp png mjpeg flac \
             libopenh264 hevc_vaapi h264_vaapi; do
    if ffmpeg -hide_banner -encoders 2>/dev/null | awk '{print $2}' | grep -qx "$enc"; then
      echo "  YES  $enc"
    else
      echo "  no   $enc"
    fi
  done
else
  echo "ffmpeg: NOT FOUND on PATH"
  rpm -qa '*ffmpeg*' 2>/dev/null | sort
fi
echo "--- ffprobe ---"
have ffprobe && ffprobe -hide_banner -version 2>&1 | head -1 || echo "ffprobe: NOT FOUND"

hr "5. FFMPEG -progress OUTPUT FORMAT (literal bytes, not docs)"
if have ffmpeg; then
  _t=$(mktemp -d)
  # 2s of generated silence -> tiny wav. Nothing touches your files.
  if ffmpeg -hide_banner -loglevel error -f lavfi -i anullsrc=r=44100:cl=mono \
       -t 2 "$_t/probe.wav" -y >/dev/null 2>&1; then
    echo "--- one -progress block, verbatim ---"
    ffmpeg -hide_banner -loglevel error -nostats -progress pipe:1 \
      -i "$_t/probe.wav" -c:a flac "$_t/out.flac" -y 2>/dev/null | head -30
    echo "--- ffprobe duration query ---"
    ffprobe -v 0 -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 \
      "$_t/probe.wav" 2>&1
  else
    echo "could not synthesize test file (lavfi/anullsrc unavailable?)"
  fi
  rm -rf "$_t"
fi

hr "6. IMAGEMAGICK -monitor OUTPUT FORMAT (literal bytes)"
if have magick; then
  _t=$(mktemp -d)
  if magick -size 1200x1200 gradient:blue-red "$_t/probe.png" >/dev/null 2>&1; then
    echo "--- magick -monitor, verbatim (first 12 lines, stderr+stdout) ---"
    magick "$_t/probe.png" -monitor -resize 300x300 "$_t/out.jpg" 2>&1 | head -12
    echo "--- -monitor-decimal, if supported ---"
    magick "$_t/probe.png" -monitor-decimal -resize 300x300 "$_t/out2.jpg" 2>&1 | head -6
  else
    echo "could not synthesize test image"
  fi
  rm -rf "$_t"
else
  echo "skipped (no magick binary)"
fi

hr "7. BUILD + RUNTIME DEPS"
for p in python3 rpmbuild rpmdev-setuptree kdialog appstream-util appstreamcli; do
  have "$p" && echo "  present  $p  [$(rpm -qf "$(command -v "$p")" 2>&1 | head -1)]" \
             || echo "  MISSING  $p"
done
echo "--- PyQt6 importable? ---"
python3 -c 'import PyQt6.QtWidgets as w; print("PyQt6 OK", w.QApplication.__module__)' 2>&1 | head -3
echo "--- python3 version ---"
python3 --version 2>&1

hr "8. REPO AVAILABILITY (names only, no changes made)"
if have dnf; then
  dnf repolist --enabled 2>/dev/null | head -20
else
  echo "dnf not found"
fi

hr "PROBE COMPLETE"
echo "Paste this entire output back. Nothing was installed, changed or deleted."
