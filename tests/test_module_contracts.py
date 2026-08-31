"""Static checks on the Fusion-side modules.

ui_command.py, mold_builder.py and preview.py all import adsk, so they cannot
be imported or exercised outside Fusion and none of the other tests touch them.
That gap is not theoretical: renaming MoldLayout.half_thickness to
top_thickness/bottom_thickness updated the layout, the builder and the preview
but missed one line in the dialog, and the add-in blew up on open.

These tests read those files as source and check every attribute taken off a
plan / cavity / settings object against the real dataclass fields. No Fusion
needed, and it catches exactly that kind of rename.

Runs OUTSIDE Fusion:
    python -m unittest discover -s tests -v
"""

import ast
import dataclasses
import os
import sys
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..", "LureMoldGenerator")
sys.path.insert(0, ROOT)

from lure_mold import layout  # noqa: E402

PACKAGE = os.path.join(ROOT, "lure_mold")
FUSION_MODULES = ["ui_command.py", "mold_builder.py", "preview.py"]

# Local variable name -> the dataclass it holds.
BINDINGS = {
    "plan": layout.MoldLayout,
    "cavity": layout.Cavity,
    "settings": layout.MoldSettings,
    "defaults": layout.MoldSettings,
    "placement": layout.HalfPlacement,
}


def attributes_used(path, variable):
    """Every attribute read off `variable` in this source file."""
    with open(path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename=path)

    found = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == variable
        ):
            found.add(node.attr)
    return found


class TestDataclassAttributesExist(unittest.TestCase):
    def test_every_attribute_used_is_a_real_field(self):
        for filename in FUSION_MODULES:
            path = os.path.join(PACKAGE, filename)
            self.assertTrue(os.path.isfile(path), f"missing {filename}")

            for variable, cls in BINDINGS.items():
                # Properties are real attributes too, not just dataclass fields.
                fields = {f.name for f in dataclasses.fields(cls)}
                fields |= {
                    name for name in dir(cls) if not name.startswith("_")
                }
                for attr in attributes_used(path, variable):
                    self.assertIn(
                        attr,
                        fields,
                        f"{filename} reads {variable}.{attr}, "
                        f"but {cls.__name__} has no such field",
                    )

    def test_the_check_would_catch_a_rename(self):
        # Guard the guard: prove the walker actually sees attribute reads.
        path = os.path.join(PACKAGE, "mold_builder.py")
        used = attributes_used(path, "plan")
        self.assertIn("cavities", used)
        self.assertIn("top_thickness", used)


class TestPureModulesStayPure(unittest.TestCase):
    """The testable core must never grow a Fusion import."""

    PURE = [
        "layout.py", "meshgen.py", "orient.py", "mesh_repair.py",
        "parting.py", "relief.py",
    ]

    def test_no_fusion_imports_in_the_pure_modules(self):
        for filename in self.PURE:
            path = os.path.join(PACKAGE, filename)
            with open(path, encoding="utf-8") as handle:
                tree = ast.parse(handle.read(), filename=path)

            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    self.assertFalse(
                        name.startswith("adsk"),
                        f"{filename} imports {name}; it must stay testable "
                        "outside Fusion",
                    )


class TestBuildStepOrder(unittest.TestCase):
    """Some build steps are order-dependent in ways types cannot express."""

    def source(self, name="mold_builder.py"):
        with open(os.path.join(PACKAGE, name), encoding="utf-8") as f:
            return f.read()

    def test_halves_are_laid_out_before_they_are_merged(self):
        """Merging replaces the two named bodies with one.

        Run the other way round, the placement step finds nothing to move and
        silently leaves the mold closed -- which is exactly what happened: the
        Lay halves out flat tick was on and did nothing.
        """
        text = self.source()
        place = text.index("lay the halves out flat")
        merge = text.index("fuse the halves into a single body")
        self.assertLess(
            place, merge,
            "the merge must come after the flat layout, or laying out is a no-op",
        )

    def test_the_lure_is_prepared_before_it_is_analysed(self):
        # Analysis must see the repaired, reduced mesh, not the raw one.
        text = self.source()
        self.assertLess(
            text.index("mesh_prep.prepare"),
            text.index("lure_analysis.analyze"),
        )

    def test_the_face_is_relieved_before_anything_is_added_to_it(self):
        """The relief cutter takes everything above its surface.

        Run after the pegs are joined, it would shear the pins off.
        """
        text = self.source()
        self.assertLess(
            text.index("warnings += apply_relief"),
            text.index("--- alignment pegs"),
        )

    def test_loose_pieces_are_swept_before_the_halves_are_merged(self):
        """Merging replaces two bodies with one body holding two pieces.

        Run the sweep after that and the smaller half IS the loose piece, so
        it gets cut away and the mold comes out as one half.
        """
        text = self.source()
        self.assertLess(
            text.index("sweep_islands(component, bottom"),
            text.index("lay the halves out flat"),
        )
        self.assertLess(
            text.index("sweep_islands(component, top"),
            text.index("MergeMeshCombineType"),
        )

    def test_stray_shells_go_before_the_volume_is_measured(self):
        # Otherwise the reported volume includes debris that never gets cut.
        text = self.source("lure_analysis.py")
        self.assertLess(
            text.index("mesh_audit.keep_usable_shells"),
            text.index("volume_mm3 = mesh_repair.volume"),
        )

    def test_the_component_exists_before_preparation_needs_it(self):
        # The working copy is put inside the component so a regenerate sweeps
        # it away; the component therefore has to exist first.
        text = self.source()
        self.assertLess(
            text.index("component = fresh_component(design"),
            text.index("mesh_prep.prepare"),
        )


if __name__ == "__main__":
    unittest.main()
