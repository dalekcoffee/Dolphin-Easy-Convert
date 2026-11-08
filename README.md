# Easy Convert

Right-click media conversion for Dolphin on Fedora KDE. Adds a **Convert To**
submenu to audio, video and image files, driven by ffmpeg and ImageMagick.

Built and verified against **Fedora 44 KDE, Plasma 6.7.3, KDE Frameworks 6.28**.
No attempt is made to support other distributions.

## What it does

- **Right-click one or many files** → *Convert To* → a preset, or *Custom…*
- **Multi-select converts as one batch.** Dolphin passes the whole selection to
  a single process (`%F`), so you get one progress window, not one per file.
- **Only appears on files it can handle.** Each menu file lists explicit MIME
  types, so the entry is absent on a `.txt` and present on a `.mkv`.
- **Progress window** with per-file and overall bars, a details pane for
  errors, and a cancel button that stops the running encoder.
- **You choose where files go, every time.**

## Where converted files go

Asked at the start of each conversion:

| Mode | Result |
|---|---|
| `converted` subfolder | New files land in `./converted/`, originals untouched |
| `originals` subfolder | New files land beside the old ones; originals move to `./originals/` |
| in place | Everything stays put; new files get `-conv` appended |

Tick **Remember this choice as my default** to have it pre-selected next time.

### What it will not do to your files

- **Never deletes.** The `originals` mode *moves* a file, and only after that
  file's conversion has succeeded.
- **Never overwrites.** A name collision gets a number: `clip-conv.mp4`,
  `clip-conv-1.mp4`, and so on. That includes two files in one batch that
  would produce the same name (`clip.mkv` and `clip.mov` → MP4), and the final
  move is an atomic no-replace, so a file that appears mid-conversion is
  stepped around too.
- **Never leaves a half-written file in place.** Encoding writes to a hidden
  scratch file in the destination folder, which is renamed into position only
  after the encoder exits successfully. A crash or a cancel leaves the scratch
  file, not a corrupt result.
- **Never uses a shell.** Commands are built as argument lists, so filenames
  containing spaces, quotes or shell metacharacters are passed through
  literally. Paths are always absolute, so a name like `-x.mkv` or
  `msl:x.png` can't be read as an option or a format prefix.
- **Won't run disguised scripts through ImageMagick.** ImageMagick picks a
  decoder from a file's content, not its name, so an image whose content is
  really XML/SVG, PostScript, PDF or drawing commands is skipped with a
  message. Genuine `.svg` files are still converted.

The file to read if you want to verify all of that is
[`src/dolphin_easy_convert/jobs.py`](src/dolphin_easy_convert/jobs.py) — it is
short and it is the only place that decides what happens to your files.

## Presets

Presets are filtered at runtime against `ffmpeg -encoders`, so anything your
ffmpeg build cannot produce never appears in the menu.

- **Video** → MP4 (H.264), MKV (H.265), WebM (VP9), MP4 (AV1), animated GIF,
  and audio extraction to MP3, OGG or FLAC. Video presets drop subtitle
  tracks: bitmap subtitles (PGS/VobSub) can't be converted to MP4 or WebM
  subtitle formats and would otherwise make the whole conversion fail.
- **Audio** → MP3, FLAC, Opus, M4A (AAC), WAV
- **Image** → PNG, JPEG, WebP, AVIF, TIFF, PDF

Image presets are filtered the same way, against `magick -list format`.

**AVIF** goes through ImageMagick's HEIC coder (`ImageMagick-heic`), which
can only read or write AVIF when libheif has an AV1 decoder or encoder plugin.
When it can't, Easy Convert falls back to ffmpeg: it decodes AVIF input with
ffmpeg, and encodes AVIF output with `libsvtav1` or `libaom-av1`. The ffmpeg
encode path drops transparency. `dolphin-easy-convert --check` shows which path
is in use.

**Custom…** opens the same window with a format dropdown, a quality control
(CRF for video, quality for images, kbps for audio) and an optional
scale-down cap.

## Install

```bash
git clone https://github.com/dalekcoffee/Dolphin-Easy-Convert.git
cd Dolphin-Easy-Convert

sudo dnf install rpm-build python3-devel libappstream-glib   # build-time only
./build-rpm.sh
sudo dnf install ./dist/dolphin-easy-convert-*.noarch.rpm
```

`dnf` pulls in `python3-pyqt6` and checks for `ffmpeg`, `ffprobe` and `magick`.
Those last three are declared as **file** dependencies (`/usr/bin/ffmpeg`), so
the package installs against either Fedora's `ffmpeg-free` or RPM Fusion's
`ffmpeg`, whichever you have.

Then restart Dolphin, or run `kbuildsycoca6 --noincremental`, for the menu to
appear.

### Update to a newer version

```bash
git pull
./build-rpm.sh
sudo dnf install ./dist/dolphin-easy-convert-*.noarch.rpm
```

`dnf install` on a package that is already present upgrades it in place.

### Check your setup

```bash
dolphin-easy-convert --check
```

Reports the desktop environment, the three tools, PyQt6, and which encoders are
available. It only reports — it never installs anything.

## Uninstall

```bash
sudo dnf remove dolphin-easy-convert
```

The package ships AppStream metainfo, but in testing on Fedora 44 it did not
surface reliably in Discover's search or installed list, so `dnf remove` (or
`dnf` in a terminal) is the supported way to remove it.

## How it hooks into KDE

Service menus are a **KIO** feature — KDE's I/O framework — rather than a
Dolphin feature. Dolphin is a front-end on top of KIO, which is why the menu
files install to `/usr/share/kio/servicemenus/` (owned by `kf6-filesystem`,
not by `dolphin`) and why they still declare the Konqueror-era service type
`KonqPopupMenu/Plugin` that KIO has kept for compatibility.

## Layout

```
src/dolphin_easy_convert/
  presets.py   format definitions + runtime encoder probing
  jobs.py      output paths, placement modes, collision handling
  runner.py    subprocess execution + progress parsing
  ui.py        the PyQt6 window
  main.py      CLI entry point used by the menu files
servicemenus/  three .desktop files (video, audio, image)
packaging/     RPM spec, AppStream metainfo, /usr/bin launcher
tools/         one-off probes used to verify KDE behaviour; not packaged
```
