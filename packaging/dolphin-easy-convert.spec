%global appid io.github.dalekcoffee.DolphinEasyConvert

Name:           dolphin-easy-convert
Version:        0.1.3
Release:        1%{?dist}
Summary:        Right-click media conversion for Dolphin using ffmpeg and ImageMagick

License:        MIT
URL:            https://github.com/dalekcoffee/Dolphin-Easy-Convert
Source0:        %{name}-%{version}.tar.gz

BuildArch:      noarch
BuildRequires:  python3-devel
BuildRequires:  libappstream-glib

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

install -D -p -m 0755 packaging/dolphin-easy-convert \
    %{buildroot}%{_bindir}/dolphin-easy-convert

# Confirmed on Plasma 6.7.3 / KF 6.28: service menus live here and ship 0644,
# without an executable bit (every file KDE itself installs here is 0644).
install -d %{buildroot}%{_datadir}/kio/servicemenus
install -p -m 0644 servicemenus/*.desktop \
    %{buildroot}%{_datadir}/kio/servicemenus/

install -D -p -m 0644 packaging/%{appid}.metainfo.xml \
    %{buildroot}%{_metainfodir}/%{appid}.metainfo.xml

install -D -p -m 0644 LICENSE \
    %{buildroot}%{_licensedir}/%{name}/LICENSE

%check
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
%{_metainfodir}/%{appid}.metainfo.xml

%changelog
* Sun Nov 08 2026 Dalek <dalekcoffee@users.noreply.github.com> - 0.1.3-1
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
