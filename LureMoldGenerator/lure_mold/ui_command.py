"""The Lure Mold Generator command.

A native Fusion command dialog rather than an HTML palette, so the numeric
fields are unit-aware (type "10 mm" or "0.4 in" and both work), body selection
behaves the way it does everywhere else in Fusion, and we get the
executePreview event that drives the live overlay.

The expensive part of the pipeline -- analysing the lure mesh -- is cached
against the selected body, so dragging a margin value around only recomputes
the cheap layout maths.
"""

import math
import os
import traceback

import adsk.core
import adsk.fusion

from . import layout, lure_analysis, mold_builder, preview, store

CMD_ID = "lureMoldGeneratorCmd"
CMD_NAME = "Lure Mold Generator"
CMD_TOOLTIP = (
    "Generate a printable two-part injection mold around a lure mesh, with "
    "alignment pegs, injection sprues and vents placed automatically."
)
# Its own panel, not a stock one. Dropped into SOLID > CREATE it landed as
# control 39 of 40 -- last in a long dropdown, effectively unfindable. A
# dedicated panel shows up as its own labelled group on the toolbar.
PANEL_ID = "LureMoldPanel"
PANEL_NAME = "Lure Mold"
PANEL_AFTER = "SolidCreatePanel"
ADDINS_PANEL_ID = "SolidScriptsAddinsPanel"
WORKSPACE_ID = "FusionSolidEnvironment"

MM = 0.1  # millimetres -> centimetres

# Dialog labels for the injection modes, in display order.
INJECTION_CHOICES = [
    ("edge", "Edge - through the face (standard)"),
    ("runner", "Runner - one sprue feeds all cavities"),
    ("top", "Top - down through the lid"),
    ("none", "None - no injection hole"),
]

VENT_PLACEMENT_CHOICES = [
    ("auto", "Automatic - find the trapped pockets"),
    ("add", "Automatic, plus my own points"),
    ("manual", "My own points only"),
]

VENT_DIRECTION_CHOICES = [
    ("edge", "Along the parting line (standard)"),
    ("top", "Straight up through the top half"),
]

# Handlers must be kept alive or Fusion garbage-collects them mid-command.
_handlers = []

# Cache of the analysed lure, keyed by the body's entity token.
_analysis_cache = {}

# Last traceback from building the dialog. If commandCreated raises, Fusion
# aborts the command silently -- the button appears to do nothing at all -- so
# the error has to be stashed somewhere it can be read back.
_last_error = None


def _mm(value_input):
    """A ValueCommandInput's value, in millimetres."""
    return value_input.value / MM


def _analyse(mesh_body):
    """Analyse a lure, reusing the previous result for the same body."""
    try:
        key = mesh_body.entityToken
    except Exception:
        key = None
    if key is not None and key in _analysis_cache:
        return _analysis_cache[key]
    result = lure_analysis.analyze(mesh_body)
    if key is not None:
        _analysis_cache.clear()
        _analysis_cache[key] = result
    return result


def _selected_body(inputs):
    selection = inputs.itemById("lureBody")
    if selection is None or selection.selectionCount == 0:
        return None
    return adsk.fusion.MeshBody.cast(selection.selection(0).entity)


def _read_settings(inputs):
    return layout.MoldSettings(
        target_length=_mm(inputs.itemById("targetLength")),
        columns=inputs.itemById("columns").value,
        rows=inputs.itemById("rows").value,
        margin_x=_mm(inputs.itemById("marginX")),
        margin_y=_mm(inputs.itemById("marginY")),
        margin_z=_mm(inputs.itemById("marginZ")),
        peg_count=inputs.itemById("pegCount").value,
        peg_diameter=_mm(inputs.itemById("pegDiameter")),
        peg_height=_mm(inputs.itemById("pegHeight")),
        peg_clearance=_mm(inputs.itemById("pegClearance")),
        peg_chamfer=_mm(inputs.itemById("pegChamfer")),
        sprue_diameter=_mm(inputs.itemById("sprueDiameter")),
        funnel_diameter=_mm(inputs.itemById("funnelDiameter")),
        injection_mode=_read_injection_mode(inputs),
        runner_diameter=_mm(inputs.itemById("runnerDiameter")),
        vents_enabled=inputs.itemById("ventsEnabled").value,
        vent_diameter=_mm(inputs.itemById("ventDiameter")),
        vent_placement=_read_choice(
            inputs, "ventPlacement", VENT_PLACEMENT_CHOICES, "auto"
        ),
        vent_direction=_read_choice(
            inputs, "ventDirection", VENT_DIRECTION_CHOICES, "edge"
        ),
        manual_vents=_read_vent_rows(inputs),
        flip_lure=inputs.itemById("flipLure").value,
        lay_out_flat=inputs.itemById("layOutFlat").value,
        auto_repair=inputs.itemById("autoRepair").value,
        reduce_faces=inputs.itemById("reduceFaces").value,
        target_faces=inputs.itemById("targetFaces").value,
        remove_islands=inputs.itemById("removeIslands").value,
        combine_halves=inputs.itemById("combineHalves").value,
        relief_enabled=inputs.itemById("reliefEnabled").value,
        relief_land=_mm(inputs.itemById("reliefLand")),
        relief_depth=_mm(inputs.itemById("reliefDepth")),
        # Fusion hands angle inputs back in radians, whatever unit is shown.
        relief_angle=math.degrees(inputs.itemById("reliefAngle").value),
        bed_check=inputs.itemById("bedCheck").value,
        bed_x=_mm(inputs.itemById("bedX")),
        bed_y=_mm(inputs.itemById("bedY")),
        fit_grid_to_bed=inputs.itemById("fitGridToBed").value,
        plastisol_density=inputs.itemById("plastisolDensity").value,
        parting_auto=inputs.itemById("partingAuto").value,
        parting_offset=_mm(inputs.itemById("partingOffset")),
    )


def _read_choice(inputs, ident, choices, fallback):
    dropdown = inputs.itemById(ident)
    if dropdown is None or dropdown.selectedItem is None:
        return fallback
    label = dropdown.selectedItem.name
    for value, text in choices:
        if text == label:
            return value
    return fallback


def _select_choice(inputs, ident, choices, wanted):
    dropdown = inputs.itemById(ident)
    if dropdown is None:
        return
    for item in dropdown.listItems:
        item.isSelected = any(
            text == item.name and value == wanted for value, text in choices
        )


def _read_injection_mode(inputs):
    return _read_choice(inputs, "injectionMode", INJECTION_CHOICES, "edge")


# --- the manual vent table -------------------------------------------
# Row 0 is a header of read-only strings. Nothing counts the header out
# explicitly; the readers cast each cell to a ValueCommandInput, and the
# header simply does not cast, which keeps them right whatever the layout.

_vent_row_serial = [0]


def _add_vent_row(inputs, x_mm, y_mm):
    table = inputs.itemById("ventTable")
    if table is None:
        return
    if len(_read_vent_rows(inputs)) >= layout.MAX_VENTS_PER_CAVITY:
        return
    _vent_row_serial[0] += 1
    serial = _vent_row_serial[0]
    children = table.commandInputs
    row = table.rowCount
    table.addCommandInput(
        children.addValueInput(
            "ventX%d" % serial, "X", "mm",
            adsk.core.ValueInput.createByReal(x_mm * MM),
        ),
        row, 0,
    )
    table.addCommandInput(
        children.addValueInput(
            "ventY%d" % serial, "Y", "mm",
            adsk.core.ValueInput.createByReal(y_mm * MM),
        ),
        row, 1,
    )


def _read_vent_rows(inputs):
    """The typed-in vent positions, in mm from the cavity centre."""
    table = inputs.itemById("ventTable")
    if table is None:
        return ()
    points = []
    for row in range(table.rowCount):
        try:
            x_in = adsk.core.ValueCommandInput.cast(
                table.getInputAtPosition(row, 0)
            )
            y_in = adsk.core.ValueCommandInput.cast(
                table.getInputAtPosition(row, 1)
            )
        except Exception:
            continue
        if x_in is None or y_in is None:
            continue
        points.append((x_in.value / MM, y_in.value / MM))
    return tuple(points)


def _set_vent_rows(inputs, points):
    table = inputs.itemById("ventTable")
    if table is None:
        return
    for row in reversed(range(table.rowCount)):
        if adsk.core.ValueCommandInput.cast(table.getInputAtPosition(row, 0)):
            table.deleteRow(row)
    for x, y in list(points)[: layout.MAX_VENTS_PER_CAVITY]:
        _add_vent_row(inputs, x, y)


def _detected_vent_points(inputs):
    """What the automatic detection would place, at the finished size."""
    body = _selected_body(inputs)
    if body is None:
        return ()
    try:
        info = _analyse(body)
        settings = _read_settings(inputs)
        length = info.length
        if settings.target_length > 0 and info.length > 0:
            length = settings.target_length
        found = mold_builder.scaled_vent_points(info, settings, length)
    except Exception:
        return ()
    return tuple(found or ())


def _sync_vent_inputs(inputs):
    """Grey out the table unless the placement actually uses it."""
    placement = _read_choice(
        inputs, "ventPlacement", VENT_PLACEMENT_CHOICES, "auto"
    )
    enabled = inputs.itemById("ventsEnabled")
    on = bool(enabled.value) if enabled is not None else True
    manual = on and placement in ("add", "manual")
    for ident in ("ventTable", "ventAdd", "ventRemove", "ventReset"):
        item = inputs.itemById(ident)
        if item is not None:
            item.isEnabled = manual
    for ident in ("ventPlacement", "ventDirection", "ventDiameter"):
        item = inputs.itemById(ident)
        if item is not None:
            item.isEnabled = on


def _plan_for(inputs):
    """Analyse the selected lure and compute its layout. May return None."""
    body = _selected_body(inputs)
    if body is None:
        return None, None, None

    info = _analyse(body)
    settings = _read_settings(inputs)

    length = info.length
    height = info.height
    thickness = info.thickness
    if settings.target_length > 0 and length > 0:
        factor = settings.target_length / length
        length, height, thickness = length * factor, height * factor, thickness * factor

    dims = layout.LureDims(
        length=length,
        height=height,
        thickness=thickness,
        nose_at_positive_x=info.nose_at_positive_x,
    )
    settings = mold_builder.resolve_parting(settings, info, length)
    settings = layout.resolve_grid(dims, settings)
    plan = layout.compute_layout(
        dims, settings,
        cavity_distance=mold_builder.scaled_footprint(info, length),
        vent_points=mold_builder.scaled_vent_points(info, settings, length),
    )
    return plan, settings, (length, height, thickness)


def _sync_parting(inputs):
    """Mirror the automatic split into the manual field, and grey it out.

    Showing the resolved number keeps the two in step: switching auto off then
    starts from wherever auto had put it, rather than jumping back to zero.
    """
    auto = inputs.itemById("partingAuto")
    manual = inputs.itemById("partingOffset")
    if auto is None or manual is None:
        return
    manual.isEnabled = not auto.value
    if not auto.value:
        return
    try:
        body = _selected_body(inputs)
        if body is None:
            return
        info = _analyse(body)
        factor = 1.0
        target = _mm(inputs.itemById("targetLength"))
        if target > 0 and info.length > 0:
            factor = target / info.length
        manual.value = info.suggested_parting_mm * factor * MM
    except Exception:
        pass


def _weight_line(inputs, plan, settings):
    """What the shot weighs -- the number anglers actually talk in."""
    try:
        info = _analyse(_selected_body(inputs))
        volume = info.volume_mm3
        if settings.target_length > 0 and info.length > 0:
            volume *= (settings.target_length / info.length) ** 3
        bait, total, feed = layout.shot_weight(plan, settings, volume)
    except Exception:
        return "&nbsp;"
    if len(plan.cavities) == 1:
        return "%.1f g per bait (+%.1f g feed)" % (bait, feed)
    return "%.1f g per bait, %.1f g the shot (+%.1f g feed)" % (bait, total, feed)


def _sync_grid(inputs):
    """Grey out the grid spinners when the bed is deciding them."""
    fit = inputs.itemById("fitGridToBed")
    if fit is None:
        return
    for ident in ("columns", "rows"):
        item = inputs.itemById(ident)
        if item is not None:
            item.isEnabled = not fit.value
    for ident in ("bedX", "bedY"):
        item = inputs.itemById(ident)
        if item is not None:
            check = inputs.itemById("bedCheck")
            item.isEnabled = fit.value or (check is not None and check.value)


def _update_readout(inputs):
    readout = inputs.itemById("readout")
    if readout is None:
        return
    try:
        plan, settings, _ = _plan_for(inputs)
    except lure_analysis.LureError as error:
        readout.formattedText = "<b>Cannot use this mesh</b><br/>%s" % error
        return
    except Exception:
        readout.formattedText = "<b>Could not read that mesh.</b>"
        return

    if plan is None:
        readout.formattedText = "<i>Select a lure mesh body to begin.</i>"
        return

    lines = [
        "<b>Mold: %.1f x %.1f x %.1f mm</b>"
        % (plan.block_x, plan.block_y, plan.block_z),
        "printed %.0f x %.0f mm &nbsp;|&nbsp; %s"
        % (plan.printed_x, plan.printed_y, _weight_line(inputs, plan, settings)),
        "%d cavit%s &nbsp;|&nbsp; %d peg%s &nbsp;|&nbsp; halves %.1f / %.1f mm"
        % (
            len(plan.cavities),
            "y" if len(plan.cavities) == 1 else "ies",
            len(plan.pegs),
            "" if len(plan.pegs) == 1 else "s",
            plan.top_thickness,
            plan.bottom_thickness,
        ),
    ]
    try:
        info = _analyse(_selected_body(inputs))
        if info.parting_score > 0:
            lines.append(
                "split %+.1f mm from centre &nbsp;|&nbsp; %d%% of the lure "
                "releases cleanly"
                % (plan.parting_offset, round(info.parting_score * 100))
            )
            if info.parting_score - info.centred_parting_score > 0.05:
                lines.append(
                    '<font color="#307030">A centred split would only manage '
                    "%d%%.</font>" % round(info.centred_parting_score * 100)
                )
            elif info.parting_score < 0.9:
                lines.append(
                    '<font color="#b06000">Some of this shape is undercut '
                    "whatever the split - it may not release.</font>"
                )
    except Exception:
        pass

    for warning in plan.warnings:
        lines.append('<font color="#b06000">%s</font>' % warning)
    readout.formattedText = "<br/>".join(lines)


class _ExecuteHandler(adsk.core.CommandEventHandler):
    def notify(self, args):
        app = adsk.core.Application.get()
        ui = app.userInterface
        try:
            design = adsk.fusion.Design.cast(app.activeProduct)
            inputs = args.command.commandInputs
            body = _selected_body(inputs)
            if body is None:
                ui.messageBox("Select a lure mesh body first.", CMD_NAME)
                return

            settings = _read_settings(inputs)
            preview.clear(design)
            result = mold_builder.build(design, body, settings)
            store.save(body, settings)

            try:
                info = _analyse(body)
                undercut = lure_analysis.find_undercuts(body, info)
                if undercut > lure_analysis.UNDERCUT_REPORT_THRESHOLD:
                    result.warnings.append(
                        "About %d%% of this lure sits in an undercut. Soft "
                        "plastic will usually still release, but a deep one "
                        "can lock the mold shut." % round(undercut * 100)
                    )
            except Exception:
                pass

            if result.warnings:
                ui.messageBox(
                    "Mold generated.\n\n- " + "\n- ".join(result.warnings), CMD_NAME
                )
        except lure_analysis.LureError as error:
            ui.messageBox(str(error), CMD_NAME)
        except Exception:
            ui.messageBox(
                "Lure Mold Generator failed:\n\n%s" % traceback.format_exc(), CMD_NAME
            )


def _show_previous_mold(design, visible):
    """Show or hide a mold left over from an earlier run.

    While the dialog is open the old mold sits underneath the ghost preview,
    which reads as though the new settings had already been applied -- an old
    two-cavity mold behind a one-cavity preview is genuinely confusing. Hide it
    during preview and put it back if the user cancels.
    """
    try:
        root = design.rootComponent
        for i in range(root.occurrences.count):
            occurrence = root.occurrences.item(i)
            if occurrence.component.name == mold_builder.COMPONENT_NAME:
                occurrence.isLightBulbOn = visible
        # In a Part Design document there is no component to switch off: the
        # mold is loose in the root, so hide the bodies themselves.
        for i in range(root.meshBodies.count):
            body = root.meshBodies.item(i)
            if mold_builder.is_generated_body(body.name):
                body.isLightBulbOn = visible
    except Exception:
        pass


class _PreviewHandler(adsk.core.CommandEventHandler):
    def notify(self, args):
        try:
            app = adsk.core.Application.get()
            design = adsk.fusion.Design.cast(app.activeProduct)
            plan, settings, dims = _plan_for(args.command.commandInputs)
            if plan is None:
                preview.clear(design)
                _show_previous_mold(design, True)
                return
            _show_previous_mold(design, False)
            preview.draw(design, plan, dims[0], dims[1], settings)
            # Do NOT set args.isValidResult here. Setting it tells Fusion the
            # preview IS the finished result and the execute event is then
            # never fired -- pressing Generate just closes the dialog and
            # silently does nothing. Our preview is only custom graphics, so
            # the real build must still run on execute.
        except Exception:
            pass


class _InputChangedHandler(adsk.core.InputChangedEventHandler):
    def notify(self, args):
        try:
            inputs = args.inputs
            changed = args.input.id

            if changed == "ventAdd":
                _add_vent_row(inputs, 0.0, 0.0)
            elif changed == "ventRemove":
                table = inputs.itemById("ventTable")
                if table is not None and table.selectedRow > 0:
                    table.deleteRow(table.selectedRow)
            elif changed == "ventReset":
                _set_vent_rows(inputs, _detected_vent_points(inputs))
            elif changed == "ventPlacement":
                # Switching to a manual mode with nothing typed in yet starts
                # from what the detection found, so it is an edit rather than
                # a blank sheet.
                placement = _read_choice(
                    inputs, "ventPlacement", VENT_PLACEMENT_CHOICES, "auto"
                )
                if placement == "manual" and not _read_vent_rows(inputs):
                    _set_vent_rows(inputs, _detected_vent_points(inputs))

            if changed == "lureBody":
                body = _selected_body(inputs)
                if body is not None:
                    saved = store.load(body)
                    if saved is not None:
                        _apply_settings(inputs, saved)
                    else:
                        try:
                            info = _analyse(body)
                            inputs.itemById("targetLength").value = info.length * MM
                        except Exception:
                            pass
            _sync_parting(inputs)
            _sync_grid(inputs)
            _sync_vent_inputs(inputs)
            _update_readout(inputs)
        except Exception:
            pass


class _DestroyHandler(adsk.core.CommandEventHandler):
    def notify(self, args):
        try:
            app = adsk.core.Application.get()
            design = adsk.fusion.Design.cast(app.activeProduct)
            preview.clear(design)
            # If the user cancelled, the old mold is still the real one.
            _show_previous_mold(design, True)
        except Exception:
            pass


def _apply_settings(inputs, settings):
    inputs.itemById("targetLength").value = settings.target_length * MM
    inputs.itemById("columns").value = settings.columns
    inputs.itemById("rows").value = settings.rows
    inputs.itemById("marginX").value = settings.margin_x * MM
    inputs.itemById("marginY").value = settings.margin_y * MM
    inputs.itemById("marginZ").value = settings.margin_z * MM
    inputs.itemById("pegCount").value = settings.peg_count
    inputs.itemById("pegDiameter").value = settings.peg_diameter * MM
    inputs.itemById("pegHeight").value = settings.peg_height * MM
    inputs.itemById("pegClearance").value = settings.peg_clearance * MM
    inputs.itemById("pegChamfer").value = settings.peg_chamfer * MM
    inputs.itemById("sprueDiameter").value = settings.sprue_diameter * MM
    inputs.itemById("funnelDiameter").value = settings.funnel_diameter * MM
    _select_choice(inputs, "injectionMode", INJECTION_CHOICES,
                   settings.injection_mode)
    inputs.itemById("runnerDiameter").value = settings.runner_diameter * MM
    inputs.itemById("ventsEnabled").value = settings.vents_enabled
    inputs.itemById("ventDiameter").value = settings.vent_diameter * MM
    _select_choice(inputs, "ventPlacement", VENT_PLACEMENT_CHOICES,
                   settings.vent_placement)
    _select_choice(inputs, "ventDirection", VENT_DIRECTION_CHOICES,
                   settings.vent_direction)
    _set_vent_rows(inputs, settings.manual_vents)
    inputs.itemById("flipLure").value = settings.flip_lure
    inputs.itemById("layOutFlat").value = settings.lay_out_flat
    inputs.itemById("autoRepair").value = settings.auto_repair
    inputs.itemById("reduceFaces").value = settings.reduce_faces
    inputs.itemById("targetFaces").value = settings.target_faces
    inputs.itemById("removeIslands").value = settings.remove_islands
    inputs.itemById("combineHalves").value = settings.combine_halves
    inputs.itemById("reliefEnabled").value = settings.relief_enabled
    inputs.itemById("reliefLand").value = settings.relief_land * MM
    inputs.itemById("reliefDepth").value = settings.relief_depth * MM
    inputs.itemById("reliefAngle").value = math.radians(settings.relief_angle)
    inputs.itemById("bedCheck").value = settings.bed_check
    inputs.itemById("bedX").value = settings.bed_x * MM
    inputs.itemById("bedY").value = settings.bed_y * MM
    inputs.itemById("fitGridToBed").value = settings.fit_grid_to_bed
    inputs.itemById("plastisolDensity").value = settings.plastisol_density
    inputs.itemById("partingAuto").value = settings.parting_auto
    inputs.itemById("partingOffset").value = settings.parting_offset * MM


class _CreatedHandler(adsk.core.CommandCreatedEventHandler):
    def notify(self, args):
        app = adsk.core.Application.get()
        try:
            command = args.command
            command.setDialogInitialSize(420, 720)
            command.okButtonText = "Generate"
            inputs = command.commandInputs
            defaults = layout.MoldSettings()

            def value(parent, ident, name, millimetres):
                return parent.addValueInput(
                    ident, name, "mm",
                    adsk.core.ValueInput.createByReal(millimetres * MM),
                )

            lure_group = inputs.addGroupCommandInput("lureGroup", "Lure").children
            selection = lure_group.addSelectionInput(
                "lureBody", "Lure body", "Select the imported lure mesh"
            )
            selection.addSelectionFilter("MeshBodies")
            selection.setSelectionLimits(1, 1)
            value(lure_group, "targetLength", "Finished length", 0.0)
            lure_group.addBoolValueInput(
                "flipLure", "Flip nose/tail", True, "", defaults.flip_lure
            )

            grid_group = inputs.addGroupCommandInput("gridGroup", "Grid").children
            grid_group.addIntegerSpinnerCommandInput(
                "columns", "Columns", 1, 20, 1, defaults.columns
            )
            grid_group.addIntegerSpinnerCommandInput(
                "rows", "Rows", 1, 20, 1, defaults.rows
            )

            printer_group = inputs.addGroupCommandInput(
                "printerGroup", "Printer"
            ).children
            printer_group.addBoolValueInput(
                "bedCheck", "Check it fits the bed", True, "",
                defaults.bed_check,
            )
            value(printer_group, "bedX", "Bed width (X)", defaults.bed_x)
            value(printer_group, "bedY", "Bed depth (Y)", defaults.bed_y)
            fit = printer_group.addBoolValueInput(
                "fitGridToBed", "Fit the grid to the bed", True, "",
                defaults.fit_grid_to_bed,
            )
            fit.tooltip = (
                "Work Columns and Rows out from the bed instead of typing "
                "them, fitting as many cavities as will print in one go."
            )
            density = printer_group.addValueInput(
                "plastisolDensity", "Plastisol density (g/cm^3)", "",
                adsk.core.ValueInput.createByReal(defaults.plastisol_density),
            )
            density.tooltip = (
                "Used for the shot weight readout. Plain plastisol is about "
                "1.02; salt-loaded plastic runs a good deal heavier."
            )

            margin_group = inputs.addGroupCommandInput(
                "marginGroup", "Wall thickness around each lure"
            ).children
            value(margin_group, "marginX", "Along length (X)", defaults.margin_x)
            value(margin_group, "marginY", "Across width (Y)", defaults.margin_y)
            value(margin_group, "marginZ", "Above and below (Z)", defaults.margin_z)

            peg_group = inputs.addGroupCommandInput(
                "pegGroup", "Alignment pegs"
            ).children
            peg_group.addIntegerSpinnerCommandInput(
                "pegCount", "Number of pegs", 0, 24, 1, defaults.peg_count
            )
            value(peg_group, "pegDiameter", "Diameter", defaults.peg_diameter)
            value(peg_group, "pegHeight", "Height", defaults.peg_height)
            value(peg_group, "pegClearance", "Fit clearance", defaults.peg_clearance)
            value(peg_group, "pegChamfer", "Lead-in chamfer",
                  defaults.peg_chamfer)

            inject_group = inputs.addGroupCommandInput(
                "injectGroup", "Injection"
            ).children
            mode = inject_group.addDropDownCommandInput(
                "injectionMode", "Injection port",
                adsk.core.DropDownStyles.TextListDropDownStyle,
            )
            for choice, text in INJECTION_CHOICES:
                mode.listItems.add(text, choice == defaults.injection_mode)
            value(inject_group, "sprueDiameter", "Sprue diameter", defaults.sprue_diameter)
            value(inject_group, "funnelDiameter", "Funnel diameter", defaults.funnel_diameter)
            value(inject_group, "runnerDiameter", "Runner diameter",
                  defaults.runner_diameter)
            inject_group.addBoolValueInput(
                "ventsEnabled", "Add vents", True, "", defaults.vents_enabled
            )
            value(inject_group, "ventDiameter", "Vent diameter", defaults.vent_diameter)

            vent_group = inputs.addGroupCommandInput(
                "ventGroup", "Vent placement"
            ).children
            positions_input = vent_group.addDropDownCommandInput(
                "ventPlacement", "Positions",
                adsk.core.DropDownStyles.TextListDropDownStyle,
            )
            for choice, text in VENT_PLACEMENT_CHOICES:
                positions_input.listItems.add(
                    text, choice == defaults.vent_placement
                )
            positions_input.tooltip = (
                "Automatic simulates the shot filling from the gate and vents "
                "wherever air ends up trapped - one per limb on a figure. Add "
                "your own on top of those, or replace them entirely."
            )
            route_input = vent_group.addDropDownCommandInput(
                "ventDirection", "Direction",
                adsk.core.DropDownStyles.TextListDropDownStyle,
            )
            for choice, text in VENT_DIRECTION_CHOICES:
                route_input.listItems.add(
                    text, choice == defaults.vent_direction
                )
            route_input.tooltip = (
                "Along the parting line is easiest to clean - the channel "
                "splits open with the mold. Straight up is for a pocket well "
                "above the split that nothing on the parting line can reach, "
                "such as the curl of a curly-tail worm; it leaves a hole "
                "through the top half to clear out after printing."
            )

            table = vent_group.addTableCommandInput(
                "ventTable", "My vent points", 2, "1:1"
            )
            table.maximumVisibleRows = 9
            table.minimumVisibleRows = 2
            header_x = table.commandInputs.addStringValueInput(
                "ventHeadX", "X", "X: along the lure (mm)"
            )
            header_x.isReadOnly = True
            header_y = table.commandInputs.addStringValueInput(
                "ventHeadY", "Y", "Y: across it (mm)"
            )
            header_y.isReadOnly = True
            table.addCommandInput(header_x, 0, 0)
            table.addCommandInput(header_y, 0, 1)

            add_button = inputs.addBoolValueInput(
                "ventAdd", "Add", False, "", False
            )
            add_button.tooltip = "Add a vent point at the cavity centre"
            table.addToolbarCommandInput(add_button)
            remove_button = inputs.addBoolValueInput(
                "ventRemove", "Remove", False, "", False
            )
            remove_button.tooltip = "Remove the selected vent point"
            table.addToolbarCommandInput(remove_button)
            reset_button = inputs.addBoolValueInput(
                "ventReset", "Reset to detected", False, "", False
            )
            reset_button.tooltip = (
                "Replace the list with the pockets the automatic detection "
                "found, as a starting point to adjust"
            )
            table.addToolbarCommandInput(reset_button)

            prep_group = inputs.addGroupCommandInput(
                "prepGroup", "Mesh preparation"
            ).children
            prep_group.addBoolValueInput(
                "autoRepair", "Repair the mesh first", True, "",
                defaults.auto_repair,
            )
            prep_group.addBoolValueInput(
                "reduceFaces", "Reduce the triangle count", True, "",
                defaults.reduce_faces,
            )
            prep_group.addIntegerSpinnerCommandInput(
                "targetFaces", "Triangle limit", 500, 500000, 2500,
                defaults.target_faces,
            )
            islands = prep_group.addBoolValueInput(
                "removeIslands", "Remove loose pieces from the mold", True, "",
                defaults.remove_islands,
            )
            islands.tooltip = (
                "A chunk of the block left joined to nothing prints as a lump "
                "rattling around in the cavity. Turn this off only to see one "
                "in place while working out what caused it."
            )

            relief_group = inputs.addGroupCommandInput(
                "reliefGroup", "Parting face relief"
            ).children
            relief_group.addBoolValueInput(
                "reliefEnabled", "Recess the face away from features", True, "",
                defaults.relief_enabled,
            )
            value(relief_group, "reliefLand", "Flat band around features",
                  defaults.relief_land)
            value(relief_group, "reliefDepth", "Recess depth",
                  defaults.relief_depth)
            relief_group.addValueInput(
                "reliefAngle", "Slope angle", "deg",
                adsk.core.ValueInput.createByReal(
                    math.radians(defaults.relief_angle)
                ),
            )

            parting_group = inputs.addGroupCommandInput(
                "partingGroup", "Parting plane"
            ).children
            parting_group.addBoolValueInput(
                "partingAuto", "Find the best split automatically", True, "",
                defaults.parting_auto,
            )
            value(parting_group, "partingOffset", "Split offset from centre",
                  defaults.parting_offset)

            output_group = inputs.addGroupCommandInput(
                "outputGroup", "Output"
            ).children
            output_group.addBoolValueInput(
                "layOutFlat", "Lay halves out flat", True, "",
                defaults.lay_out_flat,
            )
            output_group.addBoolValueInput(
                "combineHalves", "Merge halves into one body", True, "",
                defaults.combine_halves,
            )

            readout = inputs.addTextBoxCommandInput("readout", "", "", 6, True)
            readout.isFullWidth = True

            # If the document holds exactly one mesh body, pick it for them.
            design = adsk.fusion.Design.cast(app.activeProduct)
            root = design.rootComponent
            if root.meshBodies.count == 1:
                selection.addSelection(root.meshBodies.item(0))
                body = root.meshBodies.item(0)
                saved = store.load(body)
                if saved is not None:
                    _apply_settings(inputs, saved)
                else:
                    try:
                        inputs.itemById("targetLength").value = _analyse(body).length * MM
                    except Exception:
                        pass

            on_execute = _ExecuteHandler()
            command.execute.add(on_execute)
            _handlers.append(on_execute)

            on_preview = _PreviewHandler()
            command.executePreview.add(on_preview)
            _handlers.append(on_preview)

            on_changed = _InputChangedHandler()
            command.inputChanged.add(on_changed)
            _handlers.append(on_changed)

            on_destroy = _DestroyHandler()
            command.destroy.add(on_destroy)
            _handlers.append(on_destroy)

            _update_readout(inputs)
        except Exception:
            global _last_error
            _last_error = traceback.format_exc()
            app.userInterface.messageBox(
                "Failed to open Lure Mold Generator:\n\n%s" % _last_error
            )


def start():
    """Register the command and put a button on the toolbar."""
    ui = adsk.core.Application.get().userInterface

    definition = ui.commandDefinitions.itemById(CMD_ID)
    if definition:
        definition.deleteMe()

    # Icons live in resources/LureMoldGenerator/{16x16,32x32,64x64}.png.
    # Without them a promoted toolbar button renders blank and is unfindable.
    icons = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "resources", "LureMoldGenerator",
    )
    if os.path.isdir(icons):
        definition = ui.commandDefinitions.addButtonDefinition(
            CMD_ID, CMD_NAME, CMD_TOOLTIP, icons
        )
    else:
        definition = ui.commandDefinitions.addButtonDefinition(
            CMD_ID, CMD_NAME, CMD_TOOLTIP
        )

    on_created = _CreatedHandler()
    definition.commandCreated.add(on_created)
    _handlers.append(on_created)

    workspace = ui.workspaces.itemById(WORKSPACE_ID)
    panels = workspace.toolbarPanels

    # Our own labelled group on the Solid toolbar.
    panel = panels.itemById(PANEL_ID)
    if panel is None:
        panel = panels.add(PANEL_ID, PANEL_NAME, PANEL_AFTER, False)
    if panel.controls.itemById(CMD_ID) is None:
        panel.controls.addCommand(definition).isPromoted = True

    # Also in Utilities > Add-Ins, where people expect to find add-ins, so
    # there is always a second place to look.
    try:
        addins = panels.itemById(ADDINS_PANEL_ID)
        if addins is not None and addins.controls.itemById(CMD_ID) is None:
            addins.controls.addCommand(definition)
    except Exception:
        pass

    # A freshly added panel often does not render until the workspace is
    # reactivated, which makes the button look like it was never installed.
    try:
        if workspace.isActive:
            workspace.activate()
    except Exception:
        pass

    return definition


def stop():
    """Remove the button and command definition."""
    ui = adsk.core.Application.get().userInterface
    # Clean up both our own panel and the stock panel we used to live in.
    for panel_id in (PANEL_ID, ADDINS_PANEL_ID, "SolidCreatePanel"):
        try:
            panel = ui.workspaces.itemById(WORKSPACE_ID).toolbarPanels.itemById(
                panel_id
            )
            if panel is None:
                continue
            control = panel.controls.itemById(CMD_ID)
            if control:
                control.deleteMe()
            if panel_id == PANEL_ID and panel.controls.count == 0:
                panel.deleteMe()
        except Exception:
            pass
    try:
        definition = ui.commandDefinitions.itemById(CMD_ID)
        if definition:
            definition.deleteMe()
    except Exception:
        pass
    _handlers.clear()
    _analysis_cache.clear()
