%global appid io.github.dalekcoffee.DolphinEasyConvert

Name:           dolphin-easy-convert
Version:        0.3.0
Release:        1%{?dist}
Summary:        Right-click media conversion for Dolphin using ffmpeg and ImageMagick

License:        MIT
URL:            https://github.com/dalekcoffee/Dolphin-Easy-Convert
Source0:        %{name}-%{version}.tar.gz

BuildArch:      noarch
BuildRequires:  python3-devel
BuildRequires:  libappstream-glib
BuildRequires:  desktop-file-utils

Requires:       python3
Requires:       python3-pyqt6
Requires:       kf6-filesystem

# Required as file dependencies rather than package names, so the package is
# satisfied by either Fedora's ffmpeg-free or RPM Fusion's ffmpeg, and by
# whichever ImageMagick package provides the v7 binary.
Requires:       /usr/bin/ffmpeg
Requires:       /usr/bin/ffprobe
Requires:       /usr/bin/magick

# Service menus are read by Dolphin, but the directory belongs to KF6 and the
# feature works with any KIO-based file manager that reads servicemenus.
Recommends:     dolphin
# ImageMagick's HEIC coder module, which also handles AVIF. Without it AVIF
# input is decoded with ffmpeg and AVIF output is encoded with ffmpeg instead.
Recommends:     ImageMagick-heic
# Lets the animated WebP/GIF window play its WebP previews.
Recommends:     qt6-qtimageformats

%description
Adds a "Convert To" submenu to Dolphin's right-click menu for audio, video and
image files. Conversions run through ffmpeg and ImageMagick, both installed
from your existing dnf repositories; nothing is bundled or downloaded.

Selecting several files converts them as one batch with a single progress
window showing per-file and overall progress, and a cancel button.

Each conversion asks where the results should go: into a "converted"
subfolder, alongside the originals with those moved into an "originals"
subfolder, or all in the same folder with "-conv" appended to the new files.
Originals are never deleted and existing files are never overwritten.

%prep
%autosetup

%build
# Pure Python; nothing to compile.

%install
install -d %{buildroot}%{python3_sitelib}/dolphin_easy_convert
install -p -m 0644 src/dolphin_easy_convert/*.py \
    %{buildroot}%{python3_sitelib}/dolphin_easy_convert/

# Extra ImageMagick security policy (no network, no script interpreters),
# loaded by the package's own magick processes only.
install -D -p -m 0644 src/dolphin_easy_convert/magick/policy.xml \
    %{buildroot}%{python3_sitelib}/dolphin_easy_convert/magick/policy.xml

install -D -p -m 0755 packaging/dolphin-easy-convert \
    %{buildroot}%{_bindir}/dolphin-easy-convert

# Confirmed on Plasma 6.7.3 / KF 6.28: service menus live here and ship 0644,
# without an executable bit (every file KDE itself installs here is 0644).
install -d %{buildroot}%{_datadir}/kio/servicemenus
install -p -m 0644 servicemenus/*.desktop \
    %{buildroot}%{_datadir}/kio/servicemenus/

# Hidden (NoDisplay) entry: names the window and its notifications.
install -D -p -m 0644 packaging/%{name}.desktop \
    %{buildroot}%{_datadir}/applications/%{name}.desktop

install -D -p -m 0644 packaging/%{appid}.metainfo.xml \
    %{buildroot}%{_metainfodir}/%{appid}.metainfo.xml

install -D -p -m 0644 LICENSE \
    %{buildroot}%{_licensedir}/%{name}/LICENSE

%check
# The project's own tests: standard library only, no network.
%{python3} -m unittest discover -s tests -v
desktop-file-validate %{buildroot}%{_datadir}/applications/%{name}.desktop
appstream-util validate-relax --nonet \
    %{buildroot}%{_metainfodir}/%{appid}.metainfo.xml

%files
%license LICENSE
%doc README.md
%{_bindir}/dolphin-easy-convert
%{python3_sitelib}/dolphin_easy_convert/
%{_datadir}/kio/servicemenus/dolphin-easy-convert-video.desktop
%{_datadir}/kio/servicemenus/dolphin-easy-convert-audio.desktop
%{_datadir}/kio/servicemenus/dolphin-easy-convert-image.desktop
%{_datadir}/applications/%{name}.desktop
%{_metainfodir}/%{appid}.metainfo.xml

%changelog
* Sun Nov 08 2026 Dalek <dalekcoffee@users.noreply.github.com> - 0.3.0-1
- Video to animated WebP or GIF with a preview window: choose the part of
  the video, size, smoothness and quality, preview a few seconds next to
  the previous attempt with an estimated final size, then convert
- Animated GIF to WebP keeps every frame and its timing
- Progress shown in Plasma's notification area, with Cancel and Open folder
- One window: a "Convert To" click while a conversion runs joins its queue
  (local socket in $XDG_RUNTIME_DIR) instead of opening a second window
- Optional graphics-card (VAAPI) encoding for H.264/H.265, checked with a
  real test encode, falling back to the processor if it fails

* Sun Nov 08 2026 Dalek <dalekcoffee@users.noreply.github.com> - 0.2.0-1
- New presets: MP4 (small, for sharing), MP4 (fast, no re-encode) and
  JPEG (remove location & camera info)
- Keep every audio track in video conversions, and album art in MP3, M4A
  and FLAC; keep the original file dates on converted files
- Photos are rotated upright from their EXIF orientation; H.264 output is
  8-bit 4:2:0 so it plays everywhere
- New placement mode: replace the originals, sending the old files to the
  Trash where they can be restored
- Options page previews where files will go and which will be skipped;
  optional "start straight away" for menu formats
- Quality as plain choices (Smaller file / Higher quality / Best quality),
  exact numbers still available
- Progress window lists every file with its result and size change, shows
  time remaining, can skip one file, offers "Show in folder", reports space
  saved and sends a notification if the window is in the background
- Errors explained in plain language, raw output under Technical details
- Local-only hardening: ffmpeg and ffprobe may open local files only
  (-protocol_whitelist file); an extra ImageMagick policy blocks URL
  coders, the curl delegate, script interpreters and Ghostscript
- Tools are taken from /usr/bin before $PATH; the launcher runs Python in
  isolated mode (-I) so no user-installed module can be imported
- Fix files in one batch with the same name (clip.mkv, clip.mov) being
  planned onto the same output, the second overwriting the first
- Place finished files with an atomic no-replace move, so nothing that
  appears mid-conversion is overwritten; dangling symlinks count as taken
- Create scratch files with random names, exclusively and without
  following symlinks
- Pass absolute paths to every tool, so a file named "-x.mkv" or
  "msl:x.png" cannot be read as an option or a coder/protocol prefix
- Refuse images whose content is XML, PostScript, PDF or MVG instead of
  the format their name claims, so ImageMagick never runs them through
  those interpreters
- Stop the whole process group on cancel, including ImageMagick helpers
- Escape or closing the window while converting now cancels and waits,
  instead of exiting under a running thread
- Drop subtitles in video presets; bitmap subtitles made MP4/WebM fail
- Retry HEIC/AVIF decoding with ffmpeg when ImageMagick fails to read them
- Quality box: real per-format ranges, CRF 0 selectable, works for MP3
  and OGG, disabled for formats without a quality setting
- GIF scale cap applies before palette generation
- Show filenames as plain text; say "no audio track" for silent videos

* Sun Nov 08 2026 Dalek <dalekcoffee@users.noreply.github.com> - 0.1.2-1
- Fix AVIF conversions: probe ImageMagick for AVIF read/write support, which
  depends on libheif's AV1 plugins, instead of assuming it
- Encode AVIF with ffmpeg (libsvtav1 or libaom-av1) when ImageMagick cannot,
  and decode AVIF/HEIC with ffmpeg when ImageMagick cannot read them
- Treat an image conversion that exits 0 without writing output as a failure
- Hide image presets whose format ImageMagick cannot write
- Report AVIF read/write support in --check
- Recommend ImageMagick-heic

* Sun Aug 02 2026 Dalek <dalekcoffee@users.noreply.github.com> - 0.1.1-1
- Keep the output extension on the scratch filename so ffmpeg can select a
  muxer; every ffmpeg preset failed to start without it
- Add an OGG (Vorbis) output preset for audio and video sources

* Sun Aug 02 2026 Dalek <dalekcoffee@users.noreply.github.com> - 0.1.0-1
- Initial package
