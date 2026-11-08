"""Talking to your own desktop session over the local D-Bus.

Three optional conveniences, each silently skipped if the service is not
there (another desktop, no session bus, an older Plasma):

  * a "finished" notification            org.freedesktop.Notifications
  * "Show in folder" selecting the files  org.freedesktop.FileManager1
  * progress in Plasma's notification     org.kde.JobViewServer
    area, like Dolphin's own file copies  (+ org.kde.JobViewV2 per job)

D-Bus here is the per-user session bus, a local Unix socket. Nothing in this
module, or anywhere in Easy Convert, touches the network.

The JobView interfaces are taken from KDE's own introspection files
(kf5_org.kde.JobViewServer.xml and kf5_org.kde.JobViewV2.xml as shipped in
the kjobwidgets development package).
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QMetaType, QObject, QUrl, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QDesktopServices

try:
    from PyQt6.QtDBus import (
        QDBus, QDBusArgument, QDBusConnection, QDBusMessage, QDBusVariant,
    )
except ImportError:  # QtDBus missing: every helper below becomes a no-op
    QDBusConnection = None  # type: ignore[assignment,misc]


def _bus():
    if QDBusConnection is None:
        return None
    bus = QDBusConnection.sessionBus()
    return bus if bus.isConnected() else None


def _typed(value, type_: QMetaType.Type) -> "QDBusArgument":
    arg = QDBusArgument()
    arg.add(value, type_.value)
    return arg


def notify(summary: str, body: str, icon: str) -> None:
    """Show a desktop notification."""
    bus = _bus()
    if bus is None:
        return
    msg = QDBusMessage.createMethodCall(
        "org.freedesktop.Notifications", "/org/freedesktop/Notifications",
        "org.freedesktop.Notifications", "Notify")
    msg.setArguments([
        "Easy Convert",
        _typed(0, QMetaType.Type.UInt),             # replaces_id
        icon, summary, body,
        _typed([], QMetaType.Type.QStringList),     # actions
        {"desktop-entry": QDBusVariant("dolphin-easy-convert")},  # hints
        -1,                                         # default timeout
    ])
    bus.call(msg, QDBus.CallMode.NoBlock)


def show_in_file_manager(paths: list[Path]) -> None:
    """Open the file manager with the new files selected; fall back to
    simply opening their folder."""
    if not paths:
        return
    bus = _bus()
    if bus is not None:
        msg = QDBusMessage.createMethodCall(
            "org.freedesktop.FileManager1", "/org/freedesktop/FileManager1",
            "org.freedesktop.FileManager1", "ShowItems")
        msg.setArguments([
            _typed([QUrl.fromLocalFile(str(p)).toString() for p in paths],
                   QMetaType.Type.QStringList),
            "",
        ])
        reply = bus.call(msg)
        if reply.type() != QDBusMessage.MessageType.ErrorMessage:
            return
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(paths[0].parent)))


class PlasmaJob(QObject):
    """One entry in Plasma's notification-area progress list.

    Plasma shows it like a Dolphin copy: a progress bar, the current file,
    a Cancel button, and "Open folder" once finished. Cancel there is
    delivered as `cancel_requested`.
    """

    SERVICE = "org.kde.JobViewServer"
    SERVER_PATH = "/JobViewServer"
    JOB_IFACE = "org.kde.JobViewV2"
    CAN_CANCEL = 0x0001

    cancel_requested = pyqtSignal()

    def __init__(self, icon: str) -> None:
        super().__init__()
        self._path: str | None = None
        self._finished = False
        bus = _bus()
        if bus is None:
            return
        msg = QDBusMessage.createMethodCall(
            self.SERVICE, self.SERVER_PATH, "org.kde.JobViewServer",
            "requestView")
        msg.setArguments(["Easy Convert", icon, self.CAN_CANCEL])
        reply = bus.call(msg, QDBus.CallMode.Block, 2000)
        if reply.type() != QDBusMessage.MessageType.ReplyMessage or \
                not reply.arguments():
            return
        path = reply.arguments()[0]
        self._path = getattr(path, "path", lambda: str(path))()
        bus.connect("", self._path, self.JOB_IFACE, "cancelRequested",
                    self._on_cancel)

    @property
    def active(self) -> bool:
        return self._path is not None and not self._finished

    def _call(self, method: str, *args) -> None:
        bus = _bus()
        if bus is None or not self.active:
            return
        msg = QDBusMessage.createMethodCall(
            self.SERVICE, self._path, self.JOB_IFACE, method)
        msg.setArguments(list(args))
        bus.call(msg, QDBus.CallMode.NoBlock)

    @pyqtSlot()
    def _on_cancel(self) -> None:
        self.cancel_requested.emit()

    def set_title(self, text: str) -> None:
        self._call("setInfoMessage", text)

    def set_current(self, name: str) -> None:
        self._call("setDescriptionField", _typed(0, QMetaType.Type.UInt),
                   "File", name)

    def set_progress(self, done: int, total: int, fraction: float) -> None:
        self._call("setTotalAmount", _typed(total, QMetaType.Type.ULongLong),
                   "files")
        self._call("setProcessedAmount",
                   _typed(done, QMetaType.Type.ULongLong), "files")
        percent = int(min(max(fraction, 0.0), 1.0) * 100)
        self._call("setPercent", _typed(percent, QMetaType.Type.UInt))

    def set_destination(self, folder: Path) -> None:
        self._call("setDestUrl", QDBusVariant(
            QUrl.fromLocalFile(str(folder)).toString()))

    def finish(self, error: str = "") -> None:
        """End the entry; a non-empty error shows it as failed."""
        if self.active:
            self._call("terminate", error)
            self._finished = True
