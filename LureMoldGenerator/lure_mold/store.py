"""Remembering settings between runs.

Settings are stored as a JSON attribute on the lure mesh body itself, so
reopening the dialog brings back exactly what you used last time for that
lure -- and a different lure in the same document keeps its own settings.
"""

import dataclasses
import json

from . import layout

ATTR_GROUP = "LureMoldGenerator"
ATTR_SETTINGS = "settings"


def _field_names():
    return {f.name for f in dataclasses.fields(layout.MoldSettings)}


def save(mesh_body, settings):
    """Write settings onto the lure body. Never raises."""
    try:
        payload = json.dumps(dataclasses.asdict(settings))
        mesh_body.attributes.add(ATTR_GROUP, ATTR_SETTINGS, payload)
        return True
    except Exception:
        return False


def load(mesh_body):
    """Read back settings for this lure, or None if there are none."""
    try:
        attribute = mesh_body.attributes.itemByName(ATTR_GROUP, ATTR_SETTINGS)
        if attribute is None or not attribute.value:
            return None
        raw = json.loads(attribute.value)
    except Exception:
        return None

    # Ignore anything we no longer recognise, so an older saved blob still
    # loads after the settings gain or lose a field.
    known = _field_names()
    values = {k: v for k, v in raw.items() if k in known}

    # JSON has no tuples, so the vent list comes back as lists of lists.
    points = values.get("manual_vents")
    if points is not None:
        try:
            values["manual_vents"] = tuple(
                (float(p[0]), float(p[1])) for p in points
            )
        except Exception:
            values.pop("manual_vents", None)

    return layout.MoldSettings(**values)
