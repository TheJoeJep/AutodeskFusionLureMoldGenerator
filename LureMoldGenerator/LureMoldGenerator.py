"""Lure Mold Generator - Autodesk Fusion add-in entry point.

Generates a printable two-part soft-plastic injection mold around any lure
mesh: auto-orients it, sizes the block, chooses where to split, cuts the
cavities, and places alignment pegs, injection sprues and vents.

Note the module purge below. Stopping and re-running an add-in does NOT clear
Python's module cache, so Fusion keeps executing the sub-modules it loaded the
first time however many times you restart the add-in. The symptom is baffling:
tracebacks quote the new source line while raising errors that only exist in
the old code. Dropping our package from sys.modules on both start and stop
makes a restart genuinely reload the code.
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

PACKAGE = "lure_mold"


def _purge_package():
    """Drop our package from the module cache so the next import is fresh."""
    for name in [
        name
        for name in sys.modules
        if name == PACKAGE or name.startswith(PACKAGE + ".")
    ]:
        del sys.modules[name]


def run(_context):
    _purge_package()
    from lure_mold import ui_command

    ui_command.start()


def stop(_context):
    try:
        from lure_mold import ui_command

        ui_command.stop()
    finally:
        _purge_package()
