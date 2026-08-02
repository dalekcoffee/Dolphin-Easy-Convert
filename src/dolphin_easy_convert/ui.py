"""The one window: choose how files are organised, then watch them convert.

Deliberately a single dialog rather than two. You asked to be asked every time
where the files should go, and a separate options popup followed by a separate
progress popup would mean two windows per conversion. Instead the dialog starts
on the options page and becomes the progress view when you press Convert.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from PyQt6.QtCore import QSettings, Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog,
    QDialogButtonBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QMessageBox,
    QPlainTextEdit, QProgressBar, QPushButton, QRadioButton, QSizePolicy,
    QSpinBox, QStackedWidget, QVBoxLayout, QWidget,
)

from . import jobs as jobs_mod
from . import runner
from .jobs import Job, OutputMode, MODE_LABELS
from .presets import Media, Preset, presets_for

ORG = "dolphin-easy-convert"


# --------------------------------------------------------------------------
# worker
# --------------------------------------------------------------------------

class ConvertWorker(QThread):
    """Runs the planned jobs one after another on a background thread."""

    file_started = pyqtSignal(int, str)
    file_progress = pyqtSignal(float)          # -1.0 means indeterminate
    file_finished = pyqtSignal(int, bool, str)  # index, ok, message
    finished_all = pyqtSignal(int, int, int)    # done, failed, skipped

    def __init__(self, job_list: list[Job]) -> None:
        super().__init__()
        self._jobs = job_list
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:  # noqa: D102 - Qt entry point
        done = failed = 0
        skipped = sum(1 for j in self._jobs if j.status == "skipped")

        for index, job in enumerate(self._jobs):
            if job.status == "skipped":
                continue
            if self._cancel:
                job.status = "skipped"
                job.message = "cancelled"
                skipped += 1
                continue

            job.status = "running"
            self.file_started.emit(index, job.src.name)
            self.file_progress.emit(-1.0)

            try:
                runner.run(
                    job,
                    on_progress=lambda f: self.file_progress.emit(f),
                    should_cancel=lambda: self._cancel,
                )
                jobs_mod.finalise(job)
                job.status = "done"
                done += 1
                self.file_finished.emit(index, True, str(job.dest))
            except runner.Cancelled:
                jobs_mod.cleanup(job)
                job.status = "skipped"
                job.message = "cancelled"
                skipped += 1
                self.file_finished.emit(index, False, "cancelled")
            except Exception as exc:  # noqa: BLE001 - surfaced in the log pane
                jobs_mod.cleanup(job)
                job.status = "failed"
                job.message = str(exc)
                failed += 1
                self.file_finished.emit(index, False, str(exc))

        self.finished_all.emit(done, failed, skipped)


# --------------------------------------------------------------------------
# quality overrides for the Custom… entry
# --------------------------------------------------------------------------

def apply_overrides(preset: Preset, quality: int | None,
                    max_width: int | None) -> Preset:
    """Return a copy of `preset` with quality and scaling adjusted."""
    args = list(preset.args)

    if quality is not None:
        if "-crf" in args:
            args[args.index("-crf") + 1] = str(quality)
        elif "-quality" in args:
            args[args.index("-quality") + 1] = str(quality)
        elif "-b:a" in args:
            args[args.index("-b:a") + 1] = f"{quality}k"

    if max_width and preset.tool == "ffmpeg" and "-vn" not in args:
        # Only shrink; never upscale a small source to the cap.
        scale = f"scale='min({max_width},iw)':-2"
        if "-vf" in args:
            i = args.index("-vf") + 1
            args[i] = f"{args[i]},{scale}"
        else:
            args += ["-vf", scale]
    elif max_width and preset.tool == "magick":
        args += ["-resize", f"{max_width}x>"]

    return dataclasses.replace(preset, args=tuple(args))


# --------------------------------------------------------------------------
# dialog
# --------------------------------------------------------------------------

class ConvertDialog(QDialog):
    def __init__(self, paths: list[Path], preset: Preset | None,
                 media: Media, custom: bool) -> None:
        super().__init__()
        self._paths = paths
        self._media = media
        self._custom = custom or preset is None
        self._preset = preset
        self._worker: ConvertWorker | None = None
        self._jobs: list[Job] = []
        self._settings = QSettings(ORG, ORG)

        self.setWindowTitle("Convert")
        self.setMinimumWidth(520)

        self._stack = QStackedWidget(self)
        self._stack.addWidget(self._build_options_page())
        self._stack.addWidget(self._build_progress_page())

        layout = QVBoxLayout(self)
        layout.addWidget(self._stack)

    # ---- page 1: options ------------------------------------------------

    def _build_options_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        count = len(self._paths)
        noun = "file" if count == 1 else "files"
        heading = QLabel(f"<b>Convert {count} {noun}</b>")
        layout.addWidget(heading)

        if self._custom:
            layout.addWidget(self._build_format_box())
        else:
            assert self._preset is not None
            layout.addWidget(QLabel(f"Target format: <b>{self._preset.label}"
                                    f"</b>"))

        layout.addWidget(self._build_placement_box())

        self._remember = QCheckBox("Remember this choice as my default")
        layout.addWidget(self._remember)

        buttons = QDialogButtonBox()
        self._start_btn = buttons.addButton(
            "Convert", QDialogButtonBox.ButtonRole.AcceptRole)
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

        self._quality = QSpinBox()
        self._quality.setRange(0, 320)
        self._quality.setSpecialValueText("preset default")
        self._quality.setValue(0)
        form.addRow("Quality:", self._quality)

        self._max_width = QComboBox()
        self._max_width.addItem("Original size", 0)
        for width in (3840, 2560, 1920, 1280, 854):
            self._max_width.addItem(f"max {width}px wide", width)
        form.addRow("Scale:", self._max_width)

        hint = QLabel("<small>Quality: lower is better for video (CRF ~18-28), "
                      "higher is better for images (0-100) and audio (kbps)."
                      "</small>")
        hint.setWordWrap(True)
        form.addRow(hint)

        if self._format_combo.count() == 0:
            self._format_combo.addItem("no usable encoder found", None)
            self._format_combo.setEnabled(False)

        return box

    def _build_placement_box(self) -> QWidget:
        box = QGroupBox("Where should the files go?")
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

        note = QLabel("<small>Originals are never deleted. Nothing is "
                      "overwritten — a clashing name gets a number.</small>")
        note.setWordWrap(True)
        layout.addWidget(note)
        return box

    def _selected_mode(self) -> OutputMode:
        button = self._mode_group.checkedButton()
        return OutputMode(button.property("mode"))

    # ---- page 2: progress ----------------------------------------------

    def _build_progress_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        self._overall_label = QLabel("Preparing…")
        layout.addWidget(self._overall_label)
        self._overall_bar = QProgressBar()
        layout.addWidget(self._overall_bar)

        self._file_label = QLabel("")
        self._file_label.setSizePolicy(QSizePolicy.Policy.Ignored,
                                       QSizePolicy.Policy.Preferred)
        layout.addWidget(self._file_label)
        self._file_bar = QProgressBar()
        layout.addWidget(self._file_bar)

        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setVisible(False)
        self._log.setMaximumHeight(160)
        layout.addWidget(self._log)

        row = QHBoxLayout()
        self._log_btn = QPushButton("Show details")
        self._log_btn.setCheckable(True)
        self._log_btn.toggled.connect(self._toggle_log)
        row.addWidget(self._log_btn)
        row.addStretch(1)
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.clicked.connect(self._cancel)
        row.addWidget(self._cancel_btn)
        self._close_btn = QPushButton("Close")
        self._close_btn.clicked.connect(self.accept)
        self._close_btn.setVisible(False)
        row.addWidget(self._close_btn)
        layout.addLayout(row)

        return page

    def _toggle_log(self, shown: bool) -> None:
        self._log.setVisible(shown)
        self._log_btn.setText("Hide details" if shown else "Show details")
        self.adjustSize()

    # ---- running --------------------------------------------------------

    def _start(self) -> None:
        preset = self._preset
        if self._custom:
            preset = self._format_combo.currentData()
            if preset is None:
                QMessageBox.warning(self, "Nothing to do",
                                    "No encoder on this system can produce "
                                    "that format.")
                return
            quality = self._quality.value() or None
            width = self._max_width.currentData() or None
            preset = apply_overrides(preset, quality, width)

        mode = self._selected_mode()
        if self._remember.isChecked():
            self._settings.setValue("output_mode", mode.value)

        self._jobs = jobs_mod.plan(self._paths, preset, mode)
        runnable = [j for j in self._jobs if j.status != "skipped"]
        if not runnable:
            QMessageBox.information(
                self, "Nothing to convert",
                "None of the selected files can be converted with this "
                "preset.")
            self.reject()
            return

        for job in self._jobs:
            if job.status == "skipped":
                self._append_log(f"skipped  {job.src.name} — {job.message}")

        self._total = len(runnable)
        self._completed = 0
        self._overall_bar.setRange(0, self._total)
        self._overall_bar.setValue(0)
        self.setWindowTitle(f"Converting to {preset.label}")
        self._stack.setCurrentIndex(1)

        self._worker = ConvertWorker(self._jobs)
        self._worker.file_started.connect(self._on_file_started)
        self._worker.file_progress.connect(self._on_file_progress)
        self._worker.file_finished.connect(self._on_file_finished)
        self._worker.finished_all.connect(self._on_all_finished)
        self._worker.start()

    def _on_file_started(self, _index: int, name: str) -> None:
        self._overall_label.setText(
            f"File {self._completed + 1} of {self._total}")
        self._file_label.setText(name)

    def _on_file_progress(self, fraction: float) -> None:
        if fraction < 0:
            self._file_bar.setRange(0, 0)  # indeterminate
        else:
            self._file_bar.setRange(0, 100)
            self._file_bar.setValue(int(fraction * 100))

    def _on_file_finished(self, index: int, ok: bool, message: str) -> None:
        self._completed += 1
        self._overall_bar.setValue(self._completed)
        name = self._jobs[index].src.name
        if ok:
            self._append_log(f"ok       {name} → {Path(message).name}")
        else:
            self._append_log(f"FAILED   {name} — {message}")
            if not self._log_btn.isChecked():
                self._log_btn.setChecked(True)

    def _on_all_finished(self, done: int, failed: int, skipped: int) -> None:
        self._file_bar.setRange(0, 100)
        self._file_bar.setValue(100 if failed == 0 else self._file_bar.value())
        self._file_label.setText("")
        parts = [f"{done} converted"]
        if failed:
            parts.append(f"{failed} failed")
        if skipped:
            parts.append(f"{skipped} skipped")
        self._overall_label.setText("<b>Finished — " +
                                    ", ".join(parts) + "</b>")
        self._cancel_btn.setVisible(False)
        self._close_btn.setVisible(True)
        self._close_btn.setDefault(True)

    def _append_log(self, text: str) -> None:
        self._log.appendPlainText(text)

    def _cancel(self) -> None:
        if self._worker is not None:
            self._cancel_btn.setEnabled(False)
            self._cancel_btn.setText("Stopping…")
            self._overall_label.setText("Cancelling after the current file…")
            self._worker.cancel()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(10_000)
        super().closeEvent(event)


def launch(paths: list[Path], preset: Preset | None, media: Media,
           custom: bool) -> int:
    app = QApplication([])
    app.setApplicationName("Easy Convert")
    dialog = ConvertDialog(paths, preset, media, custom)
    dialog.show()
    return app.exec()
