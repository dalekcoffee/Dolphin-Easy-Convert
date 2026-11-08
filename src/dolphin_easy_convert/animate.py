"""Video -> animated WebP or GIF, with a live quality preview.

The options box lets you pick the part of the video, size, smoothness and
quality, then press Preview: a few seconds are encoded with exactly those
settings and played back, with the size the whole clip would come out at.
The previous preview stays next to it, so you can see what a change bought
you — or cost you — before converting.

Previews are written to a private temporary folder (mode 0700) that is
removed when the window closes.
"""

from __future__ import annotations

import dataclasses
import shutil
import tempfile
from pathlib import Path

from PyQt6.QtCore import QProcess, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QMovie
from PyQt6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QPushButton, QRadioButton, QSlider,
    QVBoxLayout, QWidget,
)

from .presets import PRESETS_BY_ID, Preset, ffmpeg_encoders, tool_path
from .runner import FFMPEG_LOCAL_ONLY, probe_duration

PREVIEW_SECONDS = 3.0
PANE = QSize(360, 260)

WIDTHS = ((320, "320 px — small"), (480, "480 px — chat sticker size"),
          (640, "640 px"), (800, "800 px"), (1080, "1080 px — large"),
          (0, "Original size"))
FPS = (10, 12, 15, 20, 24, 30)
DITHER = (("sierra2_4a", "Smooth (dithered)"),
          ("bayer:bayer_scale=3", "Patterned (smaller)"),
          ("none", "None (flat colours, smallest)"))


@dataclasses.dataclass(frozen=True)
class AnimSettings:
    fmt: str             # "webp" or "gif"
    start: float
    length: float        # 0 = to the end
    width: int           # 0 = original
    fps: int
    quality: int         # WebP 1-100
    lossless: bool       # WebP
    colors: int          # GIF 32-256
    dither: str          # GIF ffmpeg paletteuse dither

    def summary(self) -> str:
        size = f"{self.width}px" if self.width else "original size"
        if self.fmt == "webp":
            q = "lossless" if self.lossless else f"quality {self.quality}"
        else:
            q = f"{self.colors} colours"
        return f"{self.fmt.upper()} · {size} · {self.fps} fps · {q}"

    def output_args(self) -> tuple[str, ...]:
        scale = (f",scale='min({self.width},iw)':-2:flags=lanczos"
                 if self.width else "")
        if self.fmt == "webp":
            quality = ("-lossless", "1") if self.lossless else \
                ("-quality", str(self.quality))
            return ("-map", "0:V:0", "-vf", f"fps={self.fps}{scale}",
                    "-c:v", "libwebp_anim", "-loop", "0", *quality,
                    "-compression_level", "4", "-an")
        graph = (f"fps={self.fps}{scale},split[a][b];"
                 f"[a]palettegen=max_colors={self.colors}:stats_mode=diff[p];"
                 f"[b][p]paletteuse=dither={self.dither}:diff_mode=rectangle")
        return ("-map", "0:V:0", "-vf", graph, "-loop", "0", "-an")

    def input_args(self, start: float | None = None,
                   length: float | None = None) -> tuple[str, ...]:
        start = self.start if start is None else start
        length = self.length if length is None else length
        args: list[str] = []
        if start > 0:
            args += ["-ss", f"{start:.3f}"]
        if length > 0:
            args += ["-t", f"{length:.3f}"]
        return tuple(args)

    def preset(self) -> Preset:
        base = PRESETS_BY_ID["webp-anim" if self.fmt == "webp" else "gif"]
        return dataclasses.replace(
            base, label=f"Animated {self.summary()}", args=self.output_args(),
            input_args=self.input_args())


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


class PreviewPane(QWidget):
    """Plays one preview and says what it was made with."""

    def __init__(self, title: str) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        heading = QLabel(f"<b>{title}</b>")
        layout.addWidget(heading)
        self._view = QLabel("No preview yet")
        self._view.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._view.setMinimumSize(PANE)
        self._view.setFrameShape(QLabel.Shape.StyledPanel)
        layout.addWidget(self._view, 1)
        self._caption = QLabel()
        self._caption.setTextFormat(Qt.TextFormat.PlainText)
        self._caption.setWordWrap(True)
        layout.addWidget(self._caption)
        self._movie: QMovie | None = None
        self.file: Path | None = None
        self.caption = ""

    def show_file(self, file: Path | None, caption: str) -> None:
        if self._movie is not None:
            self._movie.stop()
            self._movie = None
        self.file, self.caption = file, caption
        self._caption.setText(caption)
        if file is None:
            self._view.setText("No preview yet")
            return
        movie = QMovie(str(file))
        movie.setCacheMode(QMovie.CacheMode.CacheAll)
        if not movie.isValid():
            self._view.setText("This preview cannot be played here\n"
                               "(install qt6-qtimageformats for WebP)")
            return
        movie.jumpToFrame(0)
        natural = movie.currentImage().size()
        if natural.width() > PANE.width() or natural.height() > PANE.height():
            movie.setScaledSize(natural.scaled(
                PANE, Qt.AspectRatioMode.KeepAspectRatio))
        self._view.setMovie(movie)
        movie.start()
        self._movie = movie

    def set_busy(self, text: str) -> None:
        self._view.setText(text)


class AnimateBox(QGroupBox):
    """Options + previews for animated output. Emits `changed`."""

    changed = pyqtSignal()

    def __init__(self, paths: list[Path], fmt: str) -> None:
        super().__init__("Animated image")
        self._paths = paths
        self._tmp = Path(tempfile.mkdtemp(prefix="easy-convert-preview-"))
        self._proc: QProcess | None = None
        self._counter = 0
        self._durations: dict[Path, float | None] = {}

        outer = QVBoxLayout(self)
        form = QFormLayout()
        outer.addLayout(form)

        # format
        fmt_row = QHBoxLayout()
        self._fmt_group = QButtonGroup(self)
        self._webp = QRadioButton("WebP — smaller, full colour")
        self._gif = QRadioButton("GIF — works everywhere")
        for button in (self._webp, self._gif):
            self._fmt_group.addButton(button)
            fmt_row.addWidget(button)
        has_webp = "libwebp_anim" in ffmpeg_encoders()
        self._webp.setEnabled(has_webp)
        (self._webp if fmt == "webp" and has_webp else self._gif).setChecked(True)
        form.addRow("Format:", fmt_row)

        # which file to preview
        self._file = QComboBox()
        for p in paths:
            self._file.addItem(p.name, p)
        if len(paths) > 1:
            form.addRow("Preview using:", self._file)

        # clip
        clip_row = QHBoxLayout()
        self._start = QDoubleSpinBox()
        self._start.setSuffix(" s")
        self._start.setDecimals(1)
        self._start.setSingleStep(0.5)
        self._length = QDoubleSpinBox()
        self._length.setSuffix(" s")
        self._length.setDecimals(1)
        self._length.setSingleStep(0.5)
        self._length.setSpecialValueText("to the end")
        clip_row.addWidget(QLabel("from"))
        clip_row.addWidget(self._start)
        clip_row.addWidget(QLabel("for"))
        clip_row.addWidget(self._length)
        clip_row.addStretch(1)
        form.addRow("Part of the video:", clip_row)

        self._width = QComboBox()
        for w, label in WIDTHS:
            self._width.addItem(label, w)
        self._width.setCurrentIndex(1)
        form.addRow("Size:", self._width)

        self._fps = QComboBox()
        for f in FPS:
            self._fps.addItem(f"{f} frames per second"
                              + (" — smoother, bigger" if f >= 24 else ""), f)
        self._fps.setCurrentIndex(FPS.index(15))
        form.addRow("Smoothness:", self._fps)

        # WebP quality
        self._webp_q = QWidget()
        wq = QHBoxLayout(self._webp_q)
        wq.setContentsMargins(0, 0, 0, 0)
        self._quality = QSlider(Qt.Orientation.Horizontal)
        self._quality.setRange(30, 100)
        self._quality.setValue(75)
        self._quality_label = QLabel()
        self._lossless = QCheckBox("Lossless")
        wq.addWidget(QLabel("smaller"))
        wq.addWidget(self._quality, 1)
        wq.addWidget(QLabel("better"))
        wq.addWidget(self._quality_label)
        wq.addWidget(self._lossless)
        form.addRow("Quality:", self._webp_q)

        # GIF quality
        self._gif_q = QWidget()
        gq = QHBoxLayout(self._gif_q)
        gq.setContentsMargins(0, 0, 0, 0)
        self._colors = QComboBox()
        for c in (256, 128, 64, 32):
            self._colors.addItem(f"{c} colours", c)
        self._dither = QComboBox()
        for value, label in DITHER:
            self._dither.addItem(label, value)
        gq.addWidget(self._colors)
        gq.addWidget(self._dither, 1)
        form.addRow("Colours:", self._gif_q)

        # previews
        buttons = QHBoxLayout()
        self._preview_btn = QPushButton("Preview")
        self._preview_btn.setToolTip(
            f"Encode {PREVIEW_SECONDS:.0f} seconds with these settings and "
            "play them next to the previous preview")
        self._preview_btn.clicked.connect(self.render_preview)
        self._swap_btn = QPushButton("Use previous settings")
        self._swap_btn.setEnabled(False)
        self._swap_btn.clicked.connect(self._use_previous)
        buttons.addWidget(self._preview_btn)
        buttons.addWidget(self._swap_btn)
        buttons.addStretch(1)
        outer.addLayout(buttons)

        panes = QHBoxLayout()
        self._before = PreviewPane("Previous")
        self._now = PreviewPane("Now")
        panes.addWidget(self._before)
        panes.addWidget(self._now)
        outer.addLayout(panes)
        self._before_settings: AnimSettings | None = None
        self._now_settings: AnimSettings | None = None

        for signal in (self._fmt_group.buttonToggled, self._quality.valueChanged,
                       self._lossless.toggled, self._width.currentIndexChanged,
                       self._fps.currentIndexChanged,
                       self._colors.currentIndexChanged,
                       self._dither.currentIndexChanged,
                       self._start.valueChanged, self._length.valueChanged):
            signal.connect(self._on_change)
        self._file.currentIndexChanged.connect(self._on_file_changed)
        self._on_file_changed()
        self._on_change()

    # ---- settings -------------------------------------------------------

    def settings(self) -> AnimSettings:
        return AnimSettings(
            fmt="webp" if self._webp.isChecked() else "gif",
            start=self._start.value(), length=self._length.value(),
            width=self._width.currentData(), fps=self._fps.currentData(),
            quality=self._quality.value(), lossless=self._lossless.isChecked(),
            colors=self._colors.currentData(),
            dither=self._dither.currentData())

    def apply(self, s: AnimSettings) -> None:
        (self._webp if s.fmt == "webp" else self._gif).setChecked(True)
        self._width.setCurrentIndex(self._width.findData(s.width))
        self._fps.setCurrentIndex(self._fps.findData(s.fps))
        self._quality.setValue(s.quality)
        self._lossless.setChecked(s.lossless)
        self._colors.setCurrentIndex(self._colors.findData(s.colors))
        self._dither.setCurrentIndex(self._dither.findData(s.dither))

    def preset(self) -> Preset:
        return self.settings().preset()

    def _duration(self, path: Path) -> float | None:
        if path not in self._durations:
            self._durations[path] = probe_duration(path)
        return self._durations[path]

    def _on_file_changed(self) -> None:
        duration = self._duration(self._file.currentData()) or 3600.0
        self._start.setRange(0, max(duration - 0.1, 0))
        self._length.setRange(0, duration)
        if self._length.value() == 0 and duration > 15:
            self._length.setValue(10.0)  # long videos: a 10 s clip to start

    def _on_change(self) -> None:
        webp = self._webp.isChecked()
        self._webp_q.setVisible(webp)
        self._gif_q.setVisible(not webp)
        self._quality.setEnabled(not self._lossless.isChecked())
        self._quality_label.setText(
            "" if self._lossless.isChecked() else str(self._quality.value()))
        self.changed.emit()

    def _use_previous(self) -> None:
        if self._before_settings is not None:
            self.apply(self._before_settings)

    # ---- preview ----------------------------------------------------------

    def render_preview(self) -> None:
        exe = tool_path("ffmpeg")
        if not exe:
            return
        if self._proc is not None:
            self._proc.kill()
            self._proc.waitForFinished(2000)
        s = self.settings()
        src = self._file.currentData()
        duration = self._duration(src)
        clip = s.length or ((duration or PREVIEW_SECONDS) - s.start)
        sample = min(PREVIEW_SECONDS, max(clip, 0.5))
        self._counter += 1
        out = self._tmp / f"preview-{self._counter}.{s.fmt}"
        args = ["-hide_banner", "-nostdin", "-loglevel", "error",
                *s.input_args(length=sample), *FFMPEG_LOCAL_ONLY,
                "-i", str(src), *s.output_args(), "-y", str(out)]
        self._pending = (s, out, sample, clip)
        self._preview_btn.setEnabled(False)
        self._now.set_busy("Rendering preview…")
        proc = QProcess(self)
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        proc.finished.connect(self._preview_done)
        proc.start(exe, args)
        self._proc = proc

    def _preview_done(self, code: int, _status) -> None:
        proc, self._proc = self._proc, None
        self._preview_btn.setEnabled(True)
        s, out, sample, clip = self._pending
        if code != 0 or not out.exists() or out.stat().st_size == 0:
            error = bytes(proc.readAll()).decode(errors="replace").strip()
            self._now.set_busy("Preview failed")
            self._now._caption.setText(error.splitlines()[-1] if error
                                       else "ffmpeg could not make a preview")
            return
        size = out.stat().st_size
        estimate = size * (clip / sample) if sample > 0 else size
        caption = (f"{s.summary()}\n{human_size(size)} for {sample:.1f} s · "
                   f"about {human_size(estimate)} for the {clip:.1f} s clip")
        # The current preview becomes "Previous"; the new one is "Now".
        if self._now.file is not None:
            self._before.show_file(self._now.file, self._now.caption)
            self._before_settings = self._now_settings
            self._swap_btn.setEnabled(True)
        self._now.show_file(out, caption)
        self._now_settings = s

    def cleanup(self) -> None:
        if self._proc is not None:
            self._proc.kill()
            self._proc.waitForFinished(2000)
        self._before.show_file(None, "")
        self._now.show_file(None, "")
        shutil.rmtree(self._tmp, ignore_errors=True)
