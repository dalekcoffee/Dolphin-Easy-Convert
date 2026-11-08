"""The one window: choose how files are organised, then watch them convert.

Deliberately a single dialog rather than two. The dialog starts on the
options page and becomes the progress view when you press Convert, so a
conversion never opens more than one window. People who always choose the
same thing can tick "start straight away" and skip the options page for
menu formats; Custom… always shows it, which is also where that setting is
turned back off.

While it converts, the window also takes requests from later "Convert To"
clicks (see instance.py) and adds them to its queue, so conversions run one
after another in one place instead of fighting over the processor.

Nothing here talks to the network. The only outside contact is the local
session D-Bus (see desktop.py), all optional.
"""

from __future__ import annotations

import dataclasses
import time
from pathlib import Path

from PyQt6.QtCore import QSettings, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QFont, QIcon
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QComboBox,
    QDialog, QDialogButtonBox, QFormLayout, QGroupBox, QHBoxLayout,
    QHeaderView, QLabel, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QRadioButton, QSpinBox, QStackedWidget, QStyle,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from . import desktop, instance
from . import jobs as jobs_mod
from . import runner
from .jobs import Job, MODE_LABELS, OutputMode, media_of
from .presets import (
    PRESETS_BY_ID, Media, Preset, gpu_encoder_name, gpu_variant, presets_for,
)

ORG = "dolphin-easy-convert"

WINDOW_ICONS = {
    Media.VIDEO: "video-x-generic",
    Media.AUDIO: "audio-x-generic",
    Media.IMAGE: "image-x-generic",
}

WHERE = {
    OutputMode.SUBDIR_CONVERTED:
        'into a "converted" folder next to the originals',
    OutputMode.SUBDIR_ORIGINALS:
        'next to the originals; the originals move into an "originals" folder',
    OutputMode.TRASH_ORIGINALS:
        "in place of the originals; the old files go to the Trash",
    OutputMode.INPLACE_SUFFIX:
        'next to the originals, with "-conv" added to the new names',
}


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def size_change(before: int, after: int) -> str:
    if before <= 0:
        return human_size(after)
    pct = round((after - before) * 100 / before)
    sign = "+" if pct > 0 else "−" if pct < 0 else "±"
    return f"{human_size(before)} → {human_size(after)} ({sign}{abs(pct)}%)"


def human_eta(seconds: float) -> str:
    if seconds < 60:
        return "less than a minute left"
    minutes = round(seconds / 60)
    if minutes < 60:
        return f"about {minutes} min left"
    return f"about {minutes // 60} h {minutes % 60:02d} min left"


# --------------------------------------------------------------------------
# worker
# --------------------------------------------------------------------------

class ConvertWorker(QThread):
    """Runs queued jobs one after another on a background thread.

    Reads the shared job list by index, so jobs appended while it runs (a
    later click joining the queue) are picked up without a restart.
    """

    file_started = pyqtSignal(int)
    file_progress = pyqtSignal(float)                # -1.0 = indeterminate
    file_finished = pyqtSignal(int, str, str, str)   # index, status, text, detail
    queue_empty = pyqtSignal()

    def __init__(self, job_list: list[Job], start: int = 0) -> None:
        super().__init__()
        self._jobs = job_list
        self._next = start
        self._cancel = False
        self._skip = False

    def cancel(self) -> None:
        self._cancel = True

    def skip_current(self) -> None:
        self._skip = True

    def run(self) -> None:  # noqa: D102 - Qt entry point
        while self._next < len(self._jobs):
            index = self._next
            self._next += 1
            job = self._jobs[index]
            if job.status != "pending":
                continue
            if self._cancel:
                job.status, job.message = "skipped", "cancelled"
                self.file_finished.emit(index, "skipped", "cancelled", "")
                continue

            self._skip = False
            job.status = "running"
            self.file_started.emit(index)
            self.file_progress.emit(-1.0)

            try:
                runner.run(
                    job,
                    on_progress=lambda f: self.file_progress.emit(
                        -1.0 if f is None else float(f)),
                    should_cancel=lambda: self._cancel or self._skip,
                )
                warning = jobs_mod.finalise(job)
                job.status = "done"
                text = size_change(job.src_size, job.dest.stat().st_size)
                for extra in (job.note, warning):
                    if extra:
                        text += f" — {extra}"
                self.file_finished.emit(index, "done", text, str(job.dest))
            except runner.Cancelled:
                jobs_mod.cleanup(job)
                job.status = "skipped"
                job.message = "skipped" if not self._cancel else "cancelled"
                self.file_finished.emit(index, "skipped", job.message, "")
            except Exception as exc:  # noqa: BLE001 - surfaced in the UI
                jobs_mod.cleanup(job)
                job.status = "failed"
                job.message = getattr(exc, "summary", str(exc))
                self.file_finished.emit(index, "failed", job.message,
                                        getattr(exc, "detail", str(exc)))

        self.queue_empty.emit()


# --------------------------------------------------------------------------
# quality: plain-language levels mapped onto each format's own scale
# --------------------------------------------------------------------------

LEVELS = ("Recommended", "Smaller file", "Higher quality", "Best quality",
          "Exact value…")


def _knob(preset: Preset) -> tuple[str, int] | None:
    """(option, preset default) for the preset's quality setting, if any."""
    for opt in ("-crf", "-quality", "-b:a", "-q:a"):
        if opt in preset.args:
            raw = preset.args[preset.args.index(opt) + 1]
            return opt, int(raw.rstrip("k"))
    return None


def quality_range(preset: Preset) -> tuple[int, int] | None:
    """Valid values for the preset's quality knob, or None if it has none."""
    knob = _knob(preset)
    if knob is None:
        return None
    opt = knob[0]
    if opt == "-crf":
        # x264/x265 stop at 51; VP9 and SVT-AV1 go to 63.
        return (0, 51) if {"libx264", "libx265"} & set(preset.needs) \
            else (0, 63)
    if opt == "-quality":
        return (1, 100)
    if opt == "-b:a":
        return (32, 320)
    # -q:a VBR: LAME 0 (best) to 9; Vorbis 0 to 10 (best).
    return (0, 9) if "libmp3lame" in preset.needs else (0, 10)


def quality_for_level(preset: Preset, level: str) -> int | None:
    """The value a named level means for this preset; None = preset default."""
    knob = _knob(preset)
    rng = quality_range(preset)
    if knob is None or rng is None or level == "Recommended":
        return None
    opt, d = knob
    lo, hi = rng
    if opt == "-crf":
        table = {"Smaller file": d + 6, "Higher quality": d - 4,
                 "Best quality": d - 8}
    elif opt == "-quality":
        table = {"Smaller file": d - 20, "Higher quality": d + (100 - d) // 2,
                 "Best quality": 100}
    elif opt == "-b:a":
        table = {"Smaller file": d - 64, "Higher quality": d + 64,
                 "Best quality": 320}
    elif "libmp3lame" in preset.needs:  # MP3 -q:a, lower is better
        table = {"Smaller file": d + 3, "Higher quality": d - 1,
                 "Best quality": lo}
    else:  # Vorbis -q:a, higher is better
        table = {"Smaller file": d - 2, "Higher quality": d + 2,
                 "Best quality": hi}
    value = table.get(level)
    return None if value is None else max(lo, min(hi, value))


def apply_overrides(preset: Preset, quality: int | None,
                    max_width: int | None) -> Preset:
    """Return a copy of `preset` with quality and scaling adjusted."""
    args = list(preset.args)

    if quality is not None:
        for opt in ("-crf", "-quality", "-b:a", "-q:a"):
            if opt in args:
                value = f"{quality}k" if opt == "-b:a" else str(quality)
                args[args.index(opt) + 1] = value
                break

    if max_width and preset.tool == "ffmpeg" and "-vn" not in args:
        # Only shrink; never upscale a small source to the cap.
        scale = f"scale='min({max_width},iw)':-2"
        if "-vf" in args:
            i = args.index("-vf") + 1
            if "palettegen" in args[i]:
                # GIF: the chain ends in paletteuse, and scaling after it
                # would throw the palette away. Tighten its own scale step.
                args[i] = args[i].replace(
                    "scale=480:-1", f"scale='min({max_width},480)':-1", 1)
            else:
                args[i] = f"{args[i]},{scale}"
        else:
            args += ["-vf", scale]
    elif max_width and preset.tool == "magick":
        args += ["-resize", f"{max_width}x>"]

    return dataclasses.replace(preset, args=tuple(args))


# --------------------------------------------------------------------------
# dialog
# --------------------------------------------------------------------------

STATUS_ICONS = {
    "pending": QStyle.StandardPixmap.SP_FileIcon,
    "running": QStyle.StandardPixmap.SP_MediaPlay,
    "done": QStyle.StandardPixmap.SP_DialogApplyButton,
    "failed": QStyle.StandardPixmap.SP_MessageBoxCritical,
    "skipped": QStyle.StandardPixmap.SP_MediaSkipForward,
}


class ConvertDialog(QDialog):
    def __init__(self, paths: list[Path], preset: Preset | None,
                 media: Media, custom: bool, animate: str | None = None,
                 queue_into: "ConvertDialog | None" = None) -> None:
        super().__init__(queue_into)
        self._paths = paths
        self._media = media
        self._animate = animate if media is Media.VIDEO else None
        self._custom = (custom or preset is None) and not self._animate
        self._preset = preset
        self._queue_into = queue_into
        self._worker: ConvertWorker | None = None
        self._jobs: list[Job] = []
        self._rows: list[QTreeWidgetItem | None] = []
        self._close_when_done = False
        self._auto_started = False
        self._outputs: list[Path] = []
        self._plasma: desktop.PlasmaJob | None = None
        self._receiver: instance.Receiver | None = None
        self._settings = QSettings(ORG, ORG)
        self._anim_box = None
        self._run_first = 0
        self._started_at = time.monotonic()

        self.setWindowTitle("Convert" if not queue_into else "Add to queue")
        self.setWindowIcon(QIcon.fromTheme(
            WINDOW_ICONS[media], QIcon.fromTheme("applications-multimedia")))
        self.setMinimumWidth(560)

        self._stack = QStackedWidget(self)
        self._stack.addWidget(self._build_options_page())
        self._stack.addWidget(self._build_progress_page())

        layout = QVBoxLayout(self)
        layout.addWidget(self._stack)
        self._update_preview()

    def start_immediately(self) -> bool:
        """True when the options page should be skipped for this run."""
        return (not self._custom and not self._animate
                and self._settings.value("start_immediately", False, type=bool))

    # ---- page 1: options ------------------------------------------------

    def _build_options_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        count = len(self._paths)
        noun = "file" if count == 1 else "files"
        title = QLabel()
        title.setTextFormat(Qt.TextFormat.PlainText)
        font = title.font()
        font.setBold(True)
        title.setFont(font)
        layout.addWidget(title)
        if self._animate:
            from .animate import AnimateBox
            title.setText(f"Turn {count} video{'s' if count > 1 else ''} into "
                          "an animated image")
            self._anim_box = AnimateBox(self._paths, self._animate)
            self._anim_box.changed.connect(self._update_preview)
            layout.addWidget(self._anim_box)
        elif self._custom:
            title.setText(f"Convert {count} {noun}")
            layout.addWidget(self._build_format_box())
        else:
            assert self._preset is not None
            title.setText(f"Convert {count} {noun} to {self._preset.label}")

        self._gpu = QCheckBox("Use the graphics card — much faster, files "
                              "a little larger")
        self._gpu.setChecked(self._settings.value("use_gpu", False, type=bool))
        layout.addWidget(self._gpu)

        layout.addWidget(self._build_placement_box())

        self._preview = QLabel()
        self._preview.setTextFormat(Qt.TextFormat.PlainText)
        self._preview.setWordWrap(True)
        layout.addWidget(self._preview)

        self._remember = QCheckBox("Remember where I put files")
        layout.addWidget(self._remember)
        self._skip_next = QCheckBox(
            "Next time, start straight away for menu formats "
            "(Custom… always asks)")
        self._skip_next.setChecked(
            self._settings.value("start_immediately", False, type=bool))
        self._skip_next.toggled.connect(
            lambda on: on and self._remember.setChecked(True))
        layout.addWidget(self._skip_next)

        buttons = QDialogButtonBox()
        self._start_btn = buttons.addButton(
            "Add to queue" if self._queue_into else "Convert",
            QDialogButtonBox.ButtonRole.AcceptRole)
        self._start_btn.setDefault(True)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._start)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        return page

    def _build_format_box(self) -> QWidget:
        box = QGroupBox("Format")
        form = QFormLayout(box)

        self._format_combo = QComboBox()
        for preset in presets_for(self._media):
            self._format_combo.addItem(preset.label, preset)
        form.addRow("Convert to:", self._format_combo)

        self._level = QComboBox()
        self._level.addItems(LEVELS)
        self._exact = QSpinBox()
        self._exact.setVisible(False)
        row = QHBoxLayout()
        row.addWidget(self._level, 1)
        row.addWidget(self._exact)
        form.addRow("Quality:", row)
        self._level.currentTextChanged.connect(self._update_quality)

        self._max_width = QComboBox()
        self._max_width.addItem("Original size", 0)
        for width, name in ((3840, "4K"), (2560, "1440p"), (1920, "1080p"),
                            (1280, "720p"), (854, "480p")):
            self._max_width.addItem(f"No wider than {width}px ({name})", width)
        form.addRow("Size:", self._max_width)

        self._quality_hint = QLabel()
        self._quality_hint.setWordWrap(True)
        form.addRow(self._quality_hint)

        if self._format_combo.count() == 0:
            self._format_combo.addItem("no usable encoder found", None)
            self._format_combo.setEnabled(False)
        self._format_combo.currentIndexChanged.connect(self._on_format_changed)
        self._on_format_changed()

        return box

    def _on_format_changed(self) -> None:
        preset = self._format_combo.currentData()
        rng = quality_range(preset) if preset is not None else None
        self._level.setEnabled(rng is not None)
        self._level.setCurrentIndex(0)
        if rng is not None:
            self._exact.setRange(*rng)
            knob = _knob(preset)
            if knob:
                self._exact.setValue(knob[1])
        self._max_width.setEnabled(preset is not None and "-vn" not in
                                   preset.args and preset.ext != "pdf")
        self._update_quality()
        self._update_preview()

    def _update_quality(self) -> None:
        preset = self._format_combo.currentData()
        exact = self._level.currentText() == LEVELS[-1] and \
            self._level.isEnabled()
        self._exact.setVisible(exact)
        hint = ""
        if preset is None or not self._level.isEnabled():
            hint = "This format has no quality setting."
        elif exact:
            knob = _knob(preset)
            hint = {"-crf": "CRF: lower is better quality and a bigger file.",
                    "-quality": "1-100: higher is better quality and a "
                                "bigger file.",
                    "-b:a": "Bitrate in kbps: higher is better.",
                    }.get(knob[0] if knob else "",
                          "MP3: 0 is best, 9 smallest." if "libmp3lame" in
                          preset.needs else "OGG: 10 is best, 0 smallest.")
        self._quality_hint.setText(f"<small>{hint}</small>" if hint else "")

    def _chosen_quality(self, preset: Preset) -> int | None:
        if not self._level.isEnabled():
            return None
        if self._level.currentText() == LEVELS[-1]:
            return self._exact.value()
        return quality_for_level(preset, self._level.currentText())

    def _build_placement_box(self) -> QWidget:
        box = QGroupBox("Where should the new files go?")
        layout = QVBoxLayout(box)

        self._mode_group = QButtonGroup(self)
        saved = self._settings.value("output_mode",
                                     OutputMode.SUBDIR_CONVERTED.value)
        for index, mode in enumerate(OutputMode):
            button = QRadioButton(MODE_LABELS[mode])
            button.setProperty("mode", mode.value)
            if mode.value == saved:
                button.setChecked(True)
            self._mode_group.addButton(button, index)
            layout.addWidget(button)
        if self._mode_group.checkedButton() is None:
            self._mode_group.buttons()[0].setChecked(True)
        self._mode_group.buttonToggled.connect(
            lambda _b, on: on and self._update_preview())

        note = QLabel("<small>Nothing is ever deleted or overwritten — a "
                      "clashing name gets a number.</small>")
        note.setWordWrap(True)
        layout.addWidget(note)
        return box

    def _selected_mode(self) -> OutputMode:
        button = self._mode_group.checkedButton()
        return OutputMode(button.property("mode"))

    def _current_preset(self) -> Preset | None:
        if self._anim_box is not None:
            return self._anim_box.preset()
        if self._custom:
            return self._format_combo.currentData()
        return self._preset

    def _update_preview(self) -> None:
        """Say what will happen before anything does."""
        if not hasattr(self, "_preview"):
            return
        preset = self._current_preset()
        self._gpu.setVisible(preset is not None
                             and gpu_encoder_name(preset) is not None)
        if preset is None:
            self._preview.setText("")
            return
        planned = jobs_mod.plan(self._paths, preset, self._selected_mode())
        runnable = [j for j in planned if j.status != "skipped"]
        skipped = [j for j in planned if j.status == "skipped"]
        n = len(runnable)
        lines = []
        if n:
            noun = "file" if n == 1 else "files"
            lines.append(f"{n} {noun} will be saved "
                         f"{WHERE[self._selected_mode()]}.")
        if skipped:
            lines.append(f"{len(skipped)} will be skipped:")
            for job in skipped[:3]:
                lines.append(f"  • {job.src.name} — {job.message}")
            if len(skipped) > 3:
                lines.append(f"  • …and {len(skipped) - 3} more")
        if self._queue_into is not None:
            lines.append("They will be converted after the files already "
                         "in the queue.")
        self._preview.setText("\n".join(lines))
        self._start_btn.setEnabled(n > 0)

    # ---- page 2: progress ----------------------------------------------

    def _build_progress_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        self._overall_label = QLabel("Preparing…")
        layout.addWidget(self._overall_label)
        self._overall_bar = QProgressBar()
        self._overall_bar.setRange(0, 1000)
        self._overall_bar.setTextVisible(False)
        layout.addWidget(self._overall_bar)

        self._file_bar = QProgressBar()
        layout.addWidget(self._file_bar)

        self._list = QTreeWidget()
        self._list.setHeaderLabels(["File", "Result"])
        self._list.setRootIsDecorated(False)
        self._list.setSelectionMode(
            QAbstractItemView.SelectionMode.NoSelection)
        self._list.header().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents)
        self._list.header().setStretchLastSection(True)
        self._list.setMinimumHeight(140)
        layout.addWidget(self._list)

        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setVisible(False)
        self._log.setMaximumHeight(140)
        layout.addWidget(self._log)

        row = QHBoxLayout()
        self._log_btn = QPushButton("Technical details")
        self._log_btn.setCheckable(True)
        self._log_btn.toggled.connect(self._toggle_log)
        row.addWidget(self._log_btn)
        row.addStretch(1)
        self._skip_btn = QPushButton("Skip this file")
        self._skip_btn.clicked.connect(self._skip_file)
        row.addWidget(self._skip_btn)
        self._cancel_btn = QPushButton("Stop all")
        self._cancel_btn.clicked.connect(self._cancel)
        row.addWidget(self._cancel_btn)
        self._show_btn = QPushButton("Show in folder")
        self._show_btn.setIcon(QIcon.fromTheme("folder-open"))
        self._show_btn.clicked.connect(
            lambda: desktop.show_in_file_manager(self._outputs))
        self._show_btn.setVisible(False)
        row.addWidget(self._show_btn)
        self._close_btn = QPushButton("Close")
        self._close_btn.clicked.connect(self.accept)
        self._close_btn.setVisible(False)
        row.addWidget(self._close_btn)
        layout.addLayout(row)

        return page

    def _toggle_log(self, shown: bool) -> None:
        self._log.setVisible(shown)
        self.adjustSize()

    def _icon(self, status: str) -> QIcon:
        return self.style().standardIcon(STATUS_ICONS[status])

    # ---- planning --------------------------------------------------------

    def _plan(self) -> tuple[list[Job], Preset] | None:
        """Turn the options into jobs (shared by Convert and Add to queue)."""
        preset = self._current_preset()
        if preset is None:
            QMessageBox.warning(self, "Nothing to do",
                                "No encoder on this system can produce "
                                "that format.")
            return None
        if self._custom:
            width = self._max_width.currentData() or None
            if not self._max_width.isEnabled():
                width = None
            preset = apply_overrides(preset, self._chosen_quality(preset),
                                     width)

        mode = self._selected_mode()
        use_gpu = self._gpu.isChecked()
        if not self._auto_started:  # the person saw and used the options
            if self._remember.isChecked():
                self._settings.setValue("output_mode", mode.value)
            self._settings.setValue("start_immediately",
                                    self._skip_next.isChecked())
            if self._gpu.isVisible():
                self._settings.setValue("use_gpu", use_gpu)

        software = preset
        note = ""
        if use_gpu and gpu_encoder_name(preset):
            gpu = gpu_variant(preset)
            if gpu is not None:
                preset = gpu
            else:
                note = ("The graphics card can't encode this format on this "
                        "system (driver), so the processor is used.")

        jobs = jobs_mod.plan(self._paths, preset, mode)
        for job in jobs:
            if preset is not software:
                job.fallback_preset = software
        if not any(j.status != "skipped" for j in jobs):
            QMessageBox.information(
                self, "Nothing to convert",
                "None of the selected files can be converted to "
                f"{preset.label}.")
            return None
        if note:
            self._log.appendPlainText(note)
        return jobs, preset

    # ---- running --------------------------------------------------------

    def _start(self) -> None:
        planned = self._plan()
        if planned is None:
            if self._queue_into is None:
                self.reject()
            return
        jobs, preset = planned
        if self._queue_into is not None:
            self._queue_into.add_jobs(jobs, preset.label)
            self.accept()
            return

        self.setWindowTitle(f"Converting to {preset.label}")
        self._stack.setCurrentIndex(1)
        self._started_at = time.monotonic()
        self._plasma = desktop.PlasmaJob(WINDOW_ICONS[self._media])
        self._plasma.cancel_requested.connect(self._cancel)
        self._plasma.set_title(f"Converting to {preset.label}")
        self._eta_timer = QTimer(self)
        self._eta_timer.timeout.connect(self._update_overall)
        self._eta_timer.start(1000)
        self.add_jobs(jobs, preset.label, header=False)

        # From now on, later "Convert To" clicks join this window's queue.
        self._receiver = instance.Receiver(self.enqueue)
        self._receiver.listen()

    def add_jobs(self, jobs: list[Job], label: str, header: bool = True) -> None:
        """Append jobs to the queue (and restart the worker if it is idle)."""
        if header:
            item = QTreeWidgetItem([f"Then: {label}", ""])
            font = QFont(item.font(0))
            font.setBold(True)
            item.setFont(0, font)
            self._list.addTopLevelItem(item)
        first = len(self._jobs)
        for job in jobs:
            item = QTreeWidgetItem([job.src.name,
                                    job.message if job.status == "skipped"
                                    else "waiting"])
            item.setIcon(0, self._icon("skipped" if job.status == "skipped"
                                       else "pending"))
            item.setToolTip(0, str(job.src))
            self._list.addTopLevelItem(item)
            self._jobs.append(job)
            self._rows.append(item)
        self._current_fraction = 0.0

        if self._worker is None or not self._worker.isRunning():
            self._set_running_ui(True)
            self._run_first = first
            self._started_at = time.monotonic()
            self._start_worker(first)
        self._update_overall()

    def _start_worker(self, first: int) -> None:
        self._worker = ConvertWorker(self._jobs, start=first)
        self._worker.file_started.connect(self._on_file_started)
        self._worker.file_progress.connect(self._on_file_progress)
        self._worker.file_finished.connect(self._on_file_finished)
        self._worker.queue_empty.connect(self._on_queue_empty)
        self._worker.start()

    def enqueue(self, request: dict) -> None:
        """A later click handed over its request (see instance.py)."""
        files = request["files"]
        kinds = [m for m in (media_of(f) for f in files) if m is not None]
        if not kinds:
            return
        media = max(set(kinds), key=kinds.count)
        preset = PRESETS_BY_ID.get(request["preset"]) if request["preset"] \
            else None
        dialog = ConvertDialog(files, preset, media, request["custom"],
                               request["animate"], queue_into=self)
        if dialog.start_immediately():
            dialog._auto_started = True
            dialog._start()
            dialog.deleteLater()
        else:
            dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            dialog.show()
            dialog.raise_()
            dialog.activateWindow()

    def _set_running_ui(self, running: bool) -> None:
        self._skip_btn.setVisible(running)
        self._cancel_btn.setVisible(running)
        self._cancel_btn.setEnabled(running)
        self._cancel_btn.setText("Stop all")
        self._file_bar.setVisible(running)
        self._close_btn.setVisible(not running)
        self._show_btn.setVisible(not running and bool(self._outputs))
        if running and self._plasma is not None and not self._plasma.active:
            self._plasma = desktop.PlasmaJob(WINDOW_ICONS[self._media])
            self._plasma.cancel_requested.connect(self._cancel)
            self._plasma.set_title("Converting")

    def _counts(self) -> dict[str, int]:
        counts = {"pending": 0, "running": 0, "done": 0, "failed": 0,
                  "skipped": 0}
        for job in self._jobs:
            counts[job.status] = counts.get(job.status, 0) + 1
        return counts

    def _on_file_started(self, index: int) -> None:
        self._current_fraction = 0.0
        item = self._rows[index]
        item.setIcon(0, self._icon("running"))
        item.setText(1, "converting…")
        self._list.scrollToItem(item)
        self._skip_btn.setEnabled(True)
        if self._plasma:
            self._plasma.set_current(self._jobs[index].src.name)
        self._update_overall()

    def _on_file_progress(self, fraction: float) -> None:
        if fraction < 0:
            self._file_bar.setRange(0, 0)  # indeterminate
        else:
            self._current_fraction = fraction
            self._file_bar.setRange(0, 100)
            self._file_bar.setValue(int(fraction * 100))
            self._update_overall()

    def _update_overall(self) -> None:
        if self._worker is None or not self._worker.isRunning():
            return
        run = self._jobs[self._run_first:]  # this run only, for a fair ETA
        finished = sum(1 for j in run if j.status in ("done", "failed")
                       or (j.status == "skipped"
                           and j.message in ("skipped", "cancelled")))
        total = finished + sum(1 for j in run
                               if j.status in ("pending", "running"))
        if total == 0:
            return
        overall = min((finished + self._current_fraction) / total, 1.0)
        self._overall_bar.setValue(int(overall * 1000))
        text = f"Converting {min(finished + 1, total)} of {total}"
        elapsed = time.monotonic() - self._started_at
        if overall > 0.03 and elapsed > 5:
            text += " · " + human_eta(elapsed / overall * (1 - overall))
        self._overall_label.setText(text)
        if self._plasma:
            self._plasma.set_progress(finished, total, overall)

    def _on_file_finished(self, index: int, status: str, text: str,
                          detail: str) -> None:
        job = self._jobs[index]
        item = self._rows[index]
        item.setIcon(0, self._icon(status))
        item.setText(1, text)
        if status == "done":
            dest = Path(detail)
            self._outputs.append(dest)
            item.setToolTip(1, str(dest))
            item.setText(0, f"{job.src.name}  →  {dest.name}")
            self._log.appendPlainText(f"ok       {job.src} → {dest}")
            if job.gpu_error:
                self._log.appendPlainText(
                    f"         graphics card error: {job.gpu_error}")
            if self._plasma:
                self._plasma.set_destination(dest.parent)
        elif status == "failed":
            item.setToolTip(1, detail)
            self._log.appendPlainText(f"FAILED   {job.src}\n         {detail}")
        self._current_fraction = 0.0
        self._update_overall()

    def _on_queue_empty(self) -> None:
        # A request may have been queued just as the worker ran out of work.
        if any(j.status == "pending" for j in self._jobs):
            self._worker.wait()
            pending = next(i for i, j in enumerate(self._jobs)
                           if j.status == "pending")
            self._start_worker(pending)
            return
        self._worker.wait()
        self._finish()

    def _finish(self) -> None:
        c = self._counts()
        done, failed = c["done"], c["failed"]
        skipped = c["skipped"]
        self._overall_bar.setValue(1000)
        self._file_bar.setRange(0, 100)
        self._file_bar.setValue(100)
        parts = [f"{done} converted"]
        if failed:
            parts.append(f"{failed} failed")
        if skipped:
            parts.append(f"{skipped} skipped")
        summary = ", ".join(parts)
        before = sum(j.src_size for j in self._jobs if j.status == "done")
        after = 0
        for j in self._jobs:
            if j.status == "done":
                try:
                    after += j.dest.stat().st_size
                except OSError:
                    pass
        if before and after < before:
            saved = before - after
            summary += (f" · saved {human_size(saved)} "
                        f"({round(saved * 100 / before)}%)")
        self._overall_label.setText(f"Finished — {summary}")
        self._set_running_ui(False)
        self._close_btn.setDefault(True)
        if self._plasma:
            self._plasma.finish(f"{failed} failed" if failed else "")

        if not self.isActiveWindow():
            QApplication.alert(self)
            desktop.notify("Conversion finished" if not failed
                           else "Conversion finished with problems",
                           summary, WINDOW_ICONS[self._media])
        if self._close_when_done:
            self.accept()

    def _skip_file(self) -> None:
        if self._worker is not None:
            self._skip_btn.setEnabled(False)
            self._worker.skip_current()

    def _cancel(self) -> None:
        if self._running():
            self._cancel_btn.setEnabled(False)
            self._skip_btn.setEnabled(False)
            self._cancel_btn.setText("Stopping…")
            self._worker.cancel()

    def _running(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def reject(self) -> None:  # noqa: D102 - Escape key / Cancel role
        # While converting, Escape must stop the encoder and wait for the
        # worker, not hide the window and let the app exit underneath a
        # running thread (Qt aborts) and an orphaned encoder.
        if self._running():
            self._close_when_done = True
            self._cancel()
            return
        super().reject()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if self._running():
            self._close_when_done = True
            self._cancel()
            event.ignore()  # closed for real once the worker has finished
            return
        super().closeEvent(event)

    def done(self, result: int) -> None:  # noqa: D102 - every way of closing
        if self._receiver is not None:
            self._receiver.close()
            self._receiver = None
        if self._anim_box is not None:
            self._anim_box.cleanup()
        if self._plasma is not None:
            self._plasma.finish()
        super().done(result)


def launch(paths: list[Path], preset: Preset | None, media: Media,
           custom: bool, animate: str | None = None) -> int:
    app = QApplication([])
    app.setApplicationName("Easy Convert")
    app.setDesktopFileName("dolphin-easy-convert")
    app.setQuitOnLastWindowClosed(False)

    # Another window busy converting? Join its queue instead of competing.
    request = instance.make_request(preset.id if preset else None, custom,
                                    animate, paths)
    if instance.hand_over(request):
        return 0

    dialog = ConvertDialog(paths, preset, media, custom, animate)
    dialog.finished.connect(app.quit)
    dialog.show()
    if dialog.start_immediately():
        dialog._auto_started = True
        QTimer.singleShot(0, dialog._start)
    return app.exec()
