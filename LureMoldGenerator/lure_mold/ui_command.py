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
        flip_lure=inputs.itemById("flipLure").value,
        lay_out_flat=inputs.itemById("layOutFlat").value,
        auto_repair=inputs.itemById("autoRepair").value,
        reduce_faces=inputs.itemById("reduceFaces").value,
        target_faces=inputs.itemById("targetFaces").value,
        combine_halves=inputs.itemById("combineHalves").value,
        relief_enabled=inputs.itemById("reliefEnabled").value,
        relief_land=_mm(inputs.itemById("reliefLand")),
        relief_depth=_mm(inputs.itemById("reliefDepth")),
        # Fusion hands angle inputs back in radians, whatever unit is shown.
        relief_angle=math.degrees(inputs.itemById("reliefAngle").value),
        parting_auto=inputs.itemById("partingAuto").value,
        parting_offset=_mm(inputs.itemById("partingOffset")),
    )


def _read_injection_mode(inputs):
    dropdown = inputs.itemById("injectionMode")
    if dropdown is None or dropdown.selectedItem is None:
        return "edge"
    label = dropdown.selectedItem.name
    for value, text in INJECTION_CHOICES:
        if text == label:
            return value
    return "edge"


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
            if args.input.id == "lureBody":
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
    for item in inputs.itemById("injectionMode").listItems:
        item.isSelected = any(
            text == item.name and value == settings.injection_mode
            for value, text in INJECTION_CHOICES
        )
    inputs.itemById("runnerDiameter").value = settings.runner_diameter * MM
    inputs.itemById("ventsEnabled").value = settings.vents_enabled
    inputs.itemById("ventDiameter").value = settings.vent_diameter * MM
    inputs.itemById("flipLure").value = settings.flip_lure
    inputs.itemById("layOutFlat").value = settings.lay_out_flat
    inputs.itemById("autoRepair").value = settings.auto_repair
    inputs.itemById("reduceFaces").value = settings.reduce_faces
    inputs.itemById("targetFaces").value = settings.target_faces
    inputs.itemById("combineHalves").value = settings.combine_halves
    inputs.itemById("reliefEnabled").value = settings.relief_enabled
    inputs.itemById("reliefLand").value = settings.relief_land * MM
    inputs.itemById("reliefDepth").value = settings.relief_depth * MM
    inputs.itemById("reliefAngle").value = math.radians(settings.relief_angle)
    inputs.itemById("partingAuto").value = settings.parting_auto
    inputs.itemById("partingOffset").value = settings.parting_offset * MM


class _CreatedHandler(adsk.core.CommandCreatedEventHandler):
    def notify(self, args):
        app = adsk.core.Application.get()
        try:
            command = args.command
            command.setDialogInitialSize(400, 640)
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
