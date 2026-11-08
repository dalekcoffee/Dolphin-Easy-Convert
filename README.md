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
- **Shows what will happen first.** Before converting, the window says how
  many files will be saved where, and which will be skipped and why.
- **Progress window** listing every file with its result and size change,
  time remaining, *Skip this file* and *Stop all*, and *Show in folder* when
  done. If the window is in the background you get a notification.
- **Plain-language errors.** "The disk is full", "MP4 cannot hold these
  codecs without re-encoding", and so on; the raw tool output is under
  *Technical details*.
- **Keeps what matters.** Every audio track in a video, album art in MP3,
  M4A and FLAC, tags, and the original file dates. Phone photos are turned
  upright.
- **Video → animated WebP or GIF, with a preview.** Pick the part of the
  video, the size, smoothness and quality, press *Preview*, and a few seconds
  play back next to your previous attempt, each with the size the whole clip
  would come out at. *Use previous settings* goes back if you preferred it.
- **Animated GIF → WebP keeps the animation**, every frame and its timing.
- **One window.** Clicking *Convert To* again while something is converting
  adds the new files to that window's queue ("Then: MP3 …") instead of
  starting a second conversion that fights the first for the processor.
- **Progress in Plasma's notification area**, like Dolphin's file copies,
  with Cancel and Open folder — handy when the window is minimised.
- **Graphics-card encoding (optional).** For MP4 (H.264) and MKV (H.265), a
  checkbox appears when your GPU and driver can really do it (tested with a
  tiny encode first). Much faster, files a little larger. If the card fails
  on a file, that file is redone on the processor automatically. On Fedora,
  H.264/H.265 on the GPU needs RPM Fusion's `mesa-va-drivers-freeworld`
  (AMD) or `intel-media-driver` (Intel).
- **You choose where files go** — every time, or tick *start straight away*
  to skip the question for menu formats (*Custom…* always asks, and is where
  you turn that off again).

## Where converted files go

Asked at the start of each conversion:

| Mode | Result |
|---|---|
| `converted` subfolder | New files land in `./converted/`, originals untouched |
| `originals` subfolder | New files land beside the old ones; originals move to `./originals/` |
| replace originals | New files take the originals' place; the old files go to the **Trash**, where Dolphin can restore them |
| in place | Everything stays put; new files get `-conv` appended |

Tick **Remember where I put files** to have it pre-selected next time.

### What it will not do to your files

- **Never deletes.** Originals are only ever *moved* — into `originals/`, or
  to the Trash — and only after that file's conversion has succeeded. If the
  Trash isn't available (some removable or network drives) the original is
  left where it was and you are told.
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

- **Video** → MP4 (H.264), **MP4 (small, for sharing)** — 720p, plays on any
  phone or chat app — **MP4 (fast, no re-encode)** — repackages in seconds
  with no quality loss when the codecs fit — MKV (H.265), WebM (VP9),
  MP4 (AV1), animated GIF, and audio extraction to MP3, OGG or FLAC.
  Video presets keep every audio track and drop subtitle tracks: bitmap
  subtitles (PGS/VobSub) can't be converted to MP4 or WebM subtitle formats
  and would otherwise make the whole conversion fail.
- **Audio** → MP3, FLAC, OGG, Opus, M4A (AAC), WAV
- **Image** → PNG, JPEG, **JPEG (remove location & camera info)** — strips
  GPS position, camera details and other metadata before you share a photo —
  WebP, AVIF, TIFF, PDF

Image presets are filtered the same way, against `magick -list format`.

**AVIF** goes through ImageMagick's HEIC coder (`ImageMagick-heic`), which
can only read or write AVIF when libheif has an AV1 decoder or encoder plugin.
When it can't, Easy Convert falls back to ffmpeg: it decodes AVIF input with
ffmpeg, and encodes AVIF output with `libsvtav1` or `libaom-av1`. The ffmpeg
encode path drops transparency. `dolphin-easy-convert --check` shows which path
is in use.

**Custom…** opens the same window with a format dropdown, a quality choice —
*Recommended*, *Smaller file*, *Higher quality*, *Best quality*, or an exact
number — and an optional size cap (4K/1440p/1080p/720p/480p) that only ever
shrinks.

## Local only

Easy Convert never connects to the internet, and makes sure the tools it runs
don't either:

- **ffmpeg / ffprobe** get `-protocol_whitelist file`: only local files can
  be opened, including by anything a file references (playlists, concat
  lists), so a disguised playlist can't fetch a URL.
- **ImageMagick** runs with an extra security policy
  ([`magick/policy.xml`](src/dolphin_easy_convert/magick/policy.xml)) that
  blocks its URL/HTTP/FTP readers and its `curl` delegate, script
  interpreters (MSL, MVG), Ghostscript and `@file` lists.
- **No downloaded dependencies.** Everything comes from Fedora's own
  repositories through `dnf`: Python, PyQt6, ffmpeg and ImageMagick. There
  are no pip packages, nothing is bundled, and the build makes no network
  requests. The tools are run from `/usr/bin`, not whatever is first on your
  `$PATH`, and the launcher starts Python in isolated mode so no
  user-installed module can be loaded in place of the real ones.
- The only other contact is your own desktop session, all local: D-Bus for
  the "finished" notification, Plasma's progress entry and asking Dolphin to
  show the new files; and a Unix socket in `$XDG_RUNTIME_DIR` (`/run/user/…`,
  readable only by you) through which a second click hands its files to the
  running window. Animated-image previews are written to a private temporary
  folder that is deleted when the window closes.

`dolphin-easy-convert --check` confirms the protections are in place.

## Install

```bash
git clone https://github.com/dalekcoffee/Dolphin-Easy-Convert.git
cd Dolphin-Easy-Convert

sudo dnf install rpm-build python3-devel libappstream-glib desktop-file-utils   # build-time only
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
./update.sh
```

It fetches the latest version of the branch you're on, shows what changed,
runs the tests, builds the RPM and installs it (asking for your sudo password
only for that step), then refreshes Dolphin's menus. It stops without
changing anything if you have local edits, if your branch can't simply be
fast-forwarded, or if the tests or build fail. `./update.sh --help` lists the
options (`--force` to reinstall anyway, `--yes` to skip dnf's confirmation,
or a branch name to switch to).

The only network access is `git fetch` from this repository. After updating,
close and reopen Dolphin to see new menu entries.

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
  animate.py   video -> animated WebP/GIF options and previews
  desktop.py   notification, Show in folder, Plasma progress (local D-Bus)
  instance.py  one window: later clicks join the running queue
  magick/      extra ImageMagick security policy (local only)
servicemenus/  three .desktop files (video, audio, image)
packaging/     RPM spec, AppStream metainfo, /usr/bin launcher, hidden .desktop
tests/         standard-library unit tests (run during the RPM build)
tools/         one-off probes used to verify KDE behaviour; not packaged
update.sh      fetch, test, build and reinstall in one step
```

Run the tests from the repository root:

```bash
python3 -m unittest discover -s tests
```
