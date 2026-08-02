%global appid io.github.dalekcoffee.DolphinEasyConvert

Name:           dolphin-easy-convert
Version:        0.1.0
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
* Sat Aug 02 2026 Dalek <dalekcoffee@users.noreply.github.com> - 0.1.0-1
- Initial package
