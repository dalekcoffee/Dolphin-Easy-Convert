"""One conversion window at a time.

When a conversion window is already open and busy, a second "Convert To"
click does not open a second window competing for the processor. It hands
its request (preset + files) to the running window, which asks any
questions it needs to and adds the files to the end of its queue.

Transport: a Unix domain socket in $XDG_RUNTIME_DIR (/run/user/<uid>,
mode 0700, so only you can reach it). Local only by construction. Without
$XDG_RUNTIME_DIR the feature is simply off and every click gets its own
window, as before.

The message is one line of JSON. Everything in it is validated: the preset
must be a known id, and every file must be an existing absolute path.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path

from PyQt6.QtNetwork import QLocalServer, QLocalSocket

from .presets import PRESETS_BY_ID

MAX_MESSAGE = 1 << 20  # 1 MiB: tens of thousands of paths
PROTOCOL = 1


def socket_path() -> str | None:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime or not Path(runtime).is_dir():
        return None
    return str(Path(runtime) / "dolphin-easy-convert.sock")


def make_request(preset: str | None, custom: bool, animate: str | None,
                 files: list[Path]) -> dict:
    return {"v": PROTOCOL, "preset": preset, "custom": custom,
            "animate": animate, "files": [str(f) for f in files]}


def validate(raw: bytes) -> dict | None:
    """Parse and check a request; None if anything is off."""
    try:
        req = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(req, dict) or req.get("v") != PROTOCOL:
        return None
    preset = req.get("preset")
    if preset is not None and preset not in PRESETS_BY_ID:
        return None
    if req.get("animate") not in (None, "webp", "gif"):
        return None
    files = req.get("files")
    if not isinstance(files, list) or not files:
        return None
    paths = []
    for f in files:
        if not isinstance(f, str) or not f.startswith("/"):
            return None
        p = Path(f)
        if not p.exists():
            return None
        paths.append(p)
    return {"preset": preset, "custom": bool(req.get("custom")),
            "animate": req.get("animate"), "files": paths}


def hand_over(request: dict, timeout_ms: int = 1500) -> bool:
    """Give the request to a running window. True if it accepted it."""
    path = socket_path()
    if path is None:
        return False
    sock = QLocalSocket()
    sock.connectToServer(path)
    if not sock.waitForConnected(timeout_ms):
        return False
    sock.write(json.dumps(request).encode("utf-8") + b"\n")
    if not sock.waitForBytesWritten(timeout_ms):
        return False
    ok = sock.waitForReadyRead(timeout_ms) and \
        bytes(sock.readAll()).startswith(b"ok")
    sock.disconnectFromServer()
    return ok


class Receiver:
    """Listens for requests from later clicks while a window is open."""

    def __init__(self, on_request: Callable[[dict], None]) -> None:
        self._on_request = on_request
        self._server: QLocalServer | None = None
        self._buffers: dict[QLocalSocket, bytes] = {}

    def listen(self) -> bool:
        path = socket_path()
        if path is None:
            return False
        server = QLocalServer()
        server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        if not server.listen(path):
            # A leftover socket from a crashed run: nobody answers on it,
            # so it is safe to remove and try once more.
            probe = QLocalSocket()
            probe.connectToServer(path)
            if probe.waitForConnected(300):
                return False  # a live window owns it
            QLocalServer.removeServer(path)
            if not server.listen(path):
                return False
        server.newConnection.connect(self._accept)
        self._server = server
        return True

    def close(self) -> None:
        if self._server is not None:
            self._server.close()
            self._server = None

    def _accept(self) -> None:
        while self._server and self._server.hasPendingConnections():
            sock = self._server.nextPendingConnection()
            self._buffers[sock] = b""
            sock.readyRead.connect(lambda s=sock: self._read(s))
            sock.disconnected.connect(lambda s=sock: self._drop(s))

    def _drop(self, sock: QLocalSocket) -> None:
        self._buffers.pop(sock, None)
        sock.deleteLater()

    def _read(self, sock: QLocalSocket) -> None:
        data = self._buffers.get(sock, b"") + bytes(sock.readAll())
        if len(data) > MAX_MESSAGE:
            sock.write(b"no\n")
            sock.disconnectFromServer()
            return
        if b"\n" not in data:
            self._buffers[sock] = data
            return
        line = data.split(b"\n", 1)[0]
        request = validate(line)
        sock.write(b"ok\n" if request else b"no\n")
        sock.flush()
        sock.disconnectFromServer()
        if request:
            self._on_request(request)
