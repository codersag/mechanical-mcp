"""Shared test doubles for the connection layer."""

import json
import re
import sys
import types
from pathlib import Path


class _Channel:
    """Like a grpc channel: independent of the session object that uses it."""

    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True
        Finalizing.events.append("closed")


class Finalizing:
    """A session whose finalizer records whether its channel was still open when it ran.

    PyMechanical's real finalizer reads the channel target; closing the channel first crashed the
    whole process (exit 139). The session sits in a reference cycle, so only gc.collect() (or a
    late collection) finalizes it, like the real object; "finalized-open" proves the order
    finalize-then-close.
    """

    events: list = []

    def __init__(self, reply=None, fail_with=None, hang=None):
        self.reply = reply
        self.fail_with = fail_with
        self.hang = hang
        self.sent = []
        self._cleanup_on_exit = True
        self._channel = _Channel()
        self._cycle = self

    def __del__(self):
        Finalizing.events.append(
            "finalized-open" if not self._channel.closed else "finalized-after-close"
        )

    def run_python_script(self, script):
        self.sent.append(script)
        if self.hang is not None:
            self.hang.wait(30)
        if self.fail_with is not None:
            raise self.fail_with
        if self.reply is not None:
            paths = re.findall(r'u"([^"]+)"', script)
            Path(paths[1]).write_text(self.reply, encoding="utf-8")
        return ""


def install_fake_core(monkeypatch, make):
    """Make `import ansys.mechanical.core` give a module whose connect_to_mechanical is `make`."""
    core = types.ModuleType("ansys.mechanical.core")
    core.connect_to_mechanical = lambda port, **kwargs: make(port)
    monkeypatch.setitem(sys.modules, "ansys", types.ModuleType("ansys"))
    monkeypatch.setitem(sys.modules, "ansys.mechanical", types.ModuleType("ansys.mechanical"))
    monkeypatch.setitem(sys.modules, "ansys.mechanical.core", core)


def info_reply(project="C:/proj/"):
    return json.dumps(
        {"ok": True, "analyses": [{"index": 0, "name": "A", "type": "Static"}], "project": project}
    )
