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
  `clip-conv-1.mp4`, and so on.
- **Never leaves a half-written file in place.** Encoding writes to a hidden
  scratch file in the destination folder, which is renamed into position only
  after the encoder exits successfully. A crash or a cancel leaves the scratch
  file, not a corrupt result.
- **Never uses a shell.** Commands are built as argument lists, so filenames
  containing spaces, quotes or shell metacharacters are passed through
  literally.

The file to read if you want to verify all of that is
[`src/dolphin_easy_convert/jobs.py`](src/dolphin_easy_convert/jobs.py) — it is
short and it is the only place that decides what happens to your files.

## Presets

Presets are filtered at runtime against `ffmpeg -encoders`, so anything your
ffmpeg build cannot produce never appears in the menu.

- **Video** → MP4 (H.264), MKV (H.265), WebM (VP9), MP4 (AV1), animated GIF,
  and audio extraction to MP3 or FLAC
- **Audio** → MP3, FLAC, Opus, M4A (AAC), WAV
- **Image** → PNG, JPEG, WebP, AVIF, TIFF, PDF

**Custom…** opens the same window with a format dropdown, a quality control
(CRF for video, quality for images, kbps for audio) and an optional
scale-down cap.

## Install

```bash
sudo dnf install rpm-build libappstream-glib   # build-time only
./build-rpm.sh
sudo dnf install ./dist/dolphin-easy-convert-*.noarch.rpm
```

`dnf` pulls in `python3-pyqt6` and checks for `ffmpeg`, `ffprobe` and `magick`.
Those last three are declared as **file** dependencies (`/usr/bin/ffmpeg`), so
the package installs against either Fedora's `ffmpeg-free` or RPM Fusion's
`ffmpeg`, whichever you have.

Then restart Dolphin, or run `kbuildsycoca6 --noincremental`, for the menu to
appear.

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

Or from Discover's installed list. The package ships AppStream metainfo so it
appears there properly, though `dnf remove` is the path that is known to work.

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
