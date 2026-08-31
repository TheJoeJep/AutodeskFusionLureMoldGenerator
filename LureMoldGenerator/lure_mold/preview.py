"""The live ghost overlay.

Drawn with custom graphics rather than real geometry, so it redraws instantly
as the user types. This is the fast half of the design: everything worth
tuning -- wall thickness, grid, peg placement, sprue position -- shows up here
immediately, and only the final cavity cut costs real time on Generate.
"""

import math

import adsk.core
import adsk.fusion

MM = 0.1  # millimetres -> centimetres, which is what the API wants

GROUP_ID = "LureMoldGeneratorPreview"

BLOCK_COLOUR = (90, 140, 220, 255)
CAVITY_COLOUR = (235, 150, 60, 255)
PEG_COLOUR = (80, 190, 120, 255)
SPRUE_COLOUR = (225, 90, 90, 255)
LAND_COLOUR = (150, 150, 165, 255)
BOLT_COLOUR = (120, 120, 130, 255)

CIRCLE_SEGMENTS = 24


def clear(design):
    """Remove any overlay we previously drew."""
    try:
        for group in list(design.rootComponent.customGraphicsGroups):
            if group.id == GROUP_ID:
                group.deleteMe()
    except Exception:
        pass


def _place(placement, point):
    """Move one preview point into a half's laid-out position.

    Mirrors HalfPlacement exactly: the flip is a 180 degree rotation about X,
    applied first, then the translation.
    """
    x, y, z = point
    if placement is None:
        return (x, y, z)
    sign = -1.0 if placement.flip else 1.0
    return (x + placement.dx, y * sign + placement.dy, z * sign + placement.dz)


def _draw(group, points_mm, colour, closed=True, placement=None):
    """Draw one polyline from a list of (x, y, z) millimetre tuples."""
    points = [_place(placement, p) for p in points_mm]
    flat = []
    for x, y, z in points:
        flat += [x * MM, y * MM, z * MM]
    if closed:
        x, y, z = points[0]
        flat += [x * MM, y * MM, z * MM]

    coordinates = adsk.fusion.CustomGraphicsCoordinates.create(flat)
    lines = group.addLines(coordinates, [], True, [])
    red, green, blue, alpha = colour
    lines.color = adsk.fusion.CustomGraphicsSolidColorEffect.create(
        adsk.core.Color.create(red, green, blue, alpha)
    )
    lines.weight = 2
    return lines


def _rectangle(cx, cy, width, depth, z):
    hw, hd = width / 2, depth / 2
    return [
        (cx - hw, cy - hd, z),
        (cx + hw, cy - hd, z),
        (cx + hw, cy + hd, z),
        (cx - hw, cy + hd, z),
    ]


def _circle(cx, cy, radius, z):
    return [
        (
            cx + radius * math.cos(2 * math.pi * i / CIRCLE_SEGMENTS),
            cy + radius * math.sin(2 * math.pi * i / CIRCLE_SEGMENTS),
            z,
        )
        for i in range(CIRCLE_SEGMENTS)
    ]


def _draw_half(group, plan, settings, lure_length, lure_height,
               placement, z_lo, z_hi):
    """Everything that appears on one half, in that half's own position."""

    def line(points, colour, closed=True):
        _draw(group, points, colour, closed=closed, placement=placement)

    # The block: both faces, the parting face, and the four vertical edges.
    for z in (z_lo, z_hi, 0.0):
        line(_rectangle(0, 0, plan.block_x, plan.block_y, z), BLOCK_COLOUR)
    hx, hy = plan.block_x / 2, plan.block_y / 2
    for sx in (-hx, hx):
        for sy in (-hy, hy):
            line([(sx, sy, z_lo), (sx, sy, z_hi)], BLOCK_COLOUR, closed=False)

    for cavity in plan.cavities:
        line(
            _rectangle(cavity.center.x, cavity.center.y,
                       lure_length, lure_height, 0.0),
            CAVITY_COLOUR,
        )

        if cavity.sprue is not None and cavity.sprue_entry is not None:
            # A channel on the parting plane, drawn as its two side walls
            # whichever axis it runs along.
            half_funnel = settings.funnel_diameter / 2
            half_sprue = settings.sprue_diameter / 2
            along_x = abs(cavity.sprue_entry.x - cavity.sprue.x) >= abs(
                cavity.sprue_entry.y - cavity.sprue.y
            )
            for side in (1, -1):
                if along_x:
                    a = (cavity.sprue_entry.x,
                         cavity.sprue_entry.y + side * half_funnel, 0.0)
                    b = (cavity.sprue.x, cavity.sprue.y + side * half_sprue, 0.0)
                else:
                    a = (cavity.sprue_entry.x + side * half_funnel,
                         cavity.sprue_entry.y, 0.0)
                    b = (cavity.sprue.x + side * half_sprue, cavity.sprue.y, 0.0)
                line([a, b], SPRUE_COLOUR, closed=False)
            line(_circle(cavity.sprue.x, cavity.sprue.y, half_sprue, 0.0),
                 SPRUE_COLOUR)
        elif cavity.sprue is not None:
            line(_circle(cavity.sprue.x, cavity.sprue.y,
                         settings.funnel_diameter / 2, z_hi), SPRUE_COLOUR)
            line(_circle(cavity.sprue.x, cavity.sprue.y,
                         settings.sprue_diameter / 2, 0.0), SPRUE_COLOUR)

        for vent in cavity.vents:
            radius = max(settings.vent_diameter / 2, 0.4)
            if vent.entry is not None:
                along_x = abs(vent.entry.x - vent.point.x) >= abs(
                    vent.entry.y - vent.point.y
                )
                for side in (1, -1):
                    if along_x:
                        a = (vent.point.x, vent.point.y + side * radius, 0.0)
                        b = (vent.entry.x, vent.entry.y + side * radius, 0.0)
                    else:
                        a = (vent.point.x + side * radius, vent.point.y, 0.0)
                        b = (vent.entry.x + side * radius, vent.entry.y, 0.0)
                    line([a, b], SPRUE_COLOUR, closed=False)
            else:
                line(_circle(vent.point.x, vent.point.y, radius, 0.0),
                     SPRUE_COLOUR)

    if getattr(plan, "runner", None) is not None:
        run = plan.runner
        for side in (1, -1):
            line([
                (run.x + side * run.diameter / 2, run.y_from, 0.0),
                (run.x + side * run.diameter / 2, run.y_to, 0.0),
            ], SPRUE_COLOUR, closed=False)
        line(_circle(run.x, run.y_to, settings.funnel_diameter / 2, 0.0),
             SPRUE_COLOUR)

    # The flat sealing land kept around each feature, if relief is on.
    if getattr(settings, "relief_enabled", False) and settings.relief_depth > 0:
        land = settings.relief_land
        for cavity in plan.cavities:
            line(
                _rectangle(cavity.center.x, cavity.center.y,
                           lure_length + 2 * land, lure_height + 2 * land, 0.0),
                LAND_COLOUR,
            )

    for peg in plan.pegs:
        line(_circle(peg.x, peg.y, settings.peg_diameter / 2, 0.0), PEG_COLOUR)
        line(_circle(peg.x, peg.y, settings.peg_diameter / 2,
                     settings.peg_height), PEG_COLOUR)

    # A bolt is drawn as the hole on the parting face and the head above it,
    # since the head is the part that has to find room near the block edge.
    for bolt in getattr(plan, "bolts", ()):
        line(_circle(bolt.x, bolt.y, settings.bolt_diameter / 2, 0.0),
             BOLT_COLOUR)
        if getattr(settings, "bolt_capture", True):
            line(
                _circle(bolt.x, bolt.y, settings.bolt_head_diameter / 2,
                        plan.top_thickness),
                BOLT_COLOUR,
            )


def draw(design, plan, lure_length, lure_height, settings):
    """Redraw the whole overlay for a computed layout.

    When the halves are going to be laid out flat, the preview shows them
    there rather than closed -- otherwise the ghost bears no resemblance to
    what Generate produces.
    """
    clear(design)
    group = design.rootComponent.customGraphicsGroups.add()
    group.id = GROUP_ID

    if getattr(settings, "lay_out_flat", True):
        halves = [
            (plan.bottom_placement, -plan.bottom_thickness, 0.0),
            (plan.top_placement, 0.0, plan.top_thickness),
        ]
    else:
        halves = [(None, -plan.bottom_thickness, plan.top_thickness)]

    for placement, z_lo, z_hi in halves:
        _draw_half(group, plan, settings, lure_length, lure_height,
                   placement, z_lo, z_hi)

    return group
