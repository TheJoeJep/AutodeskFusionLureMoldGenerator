# What is left to build

A sweep of the whole add-in, written after the mesh audit landed. Ordered by
what would change most for someone actually pouring baits, not by what is
interesting to build.

Nothing here is committed to. It is a map of the gaps.

---

## Where it stands

Solid, and verified on real models:

- Orientation, parting-plane choice, cavity cutting, relief, pegs, sprues,
  runners, vents, laying out flat, merging.
- Vent placement per trapped pocket, with manual override and a vertical
  option.
- Mesh audit both ways: stray shells out of the lure, loose islands out of the
  mold, sealed pockets reported, cut volume sanity-checked.
- 263 tests, all outside Fusion.

The mold that comes out is **geometrically** right. Most of what follows is
about making it a thing you can actually use at the bench.

---

## 1. The biggest gap: nothing holds the mold shut

A printed soft-plastic mold that is not clamped flashes badly along the whole
parting line. Every commercial mold has clamping; this one has alignment pegs
and nothing else. That is the largest distance between "correct geometry" and
"usable tool", and it is why this is first.

Options, roughly in order of how well they print:

- **Bolt holes** — counterbored through the top half, clearance or tapped in
  the bottom, at the corners. Needs peg-style placement logic, which already
  exists and can be reused nearly wholesale.
- **Clip slots** — a shallow relief along two edges for a bulldog clip or a
  spring clamp. Cheapest to build, no hardware, and what most people actually
  use.
- **Rubber-band grooves** — a channel right round the block.

A **perimeter lip** is worth considering alongside: a step around the whole
parting face, one half male and one female. It locates far better than two pegs
and resists the peel-open force that flashing comes from. It is also a
straightforward offset of the block outline, so the geometry is easy.

**Effort:** moderate. Bolt holes reuse `_peg_candidates` and the collision
tests. A lip is a new layout primitive but a simple one.

## 2. Will it fit the printer?

There is no bed-size setting, so nothing stops you generating a 340 mm mold for
a 220 mm printer. Most people are bed-limited before they are anything-else
limited, and the grid is currently chosen by hand.

- Enter bed X/Y, warn when the block exceeds it.
- Better: pick the grid *from* the bed — "as many cavities as fit on 220 x 220".
- Better still: report both halves laid out, since that is what actually has to
  fit, and it is `2 x block_y + gap`, not `block_y`.

**Effort:** small. It is arithmetic over `compute_layout`, plus a dialog group.
The third point is a real trap worth fixing regardless — the readout currently
reports the block, not the printed footprint.

## 3. Tell me what the bait weighs

The cavity volume is already known to four decimal places. Multiplied by
plastisol density it gives grams per bait and grams per shot, which is the
number anglers actually talk in — and it tells you how much plastic to heat.

**Effort:** trivial. One line of arithmetic and a readout line. Highest value
per unit of work in this document.

## 4. A Check button

A build costs 50 seconds and up. Every diagnostic in the pipeline — mesh
audit, undercuts, release percentage, peg placement, thin walls — could run in
about a second without cutting anything, and say whether it is worth pressing
Generate.

**Effort:** small; the analysis path is already separate from the build path.

## 5. The robustness holes still open

The mesh audit closed the big one. These are what is left:

- **Self-intersecting meshes.** The single most common cause of a corrupt
  boolean, and not detected. Fusion's repair usually fixes it, but "usually" is
  doing work in that sentence. A uniform spatial hash plus a triangle/triangle
  test would catch it before the build rather than after.
  *Effort: moderate. It is the only item here that is real geometry code.*

- **Scale sanity.** A model exported in metres imports as a 0.09 mm lure; in
  inches, as a 2170 mm one. Both produce absurd molds with no warning. A check
  that the detected length is between roughly 5 mm and 500 mm costs one line.
  *Effort: trivial.*

- **Degenerate triangles.** Zero-area faces and duplicate vertices make
  booleans noisy. Cheap to count, cheap to report.

- **Wall thickness, measured rather than assumed.** Margins govern the wall
  between the cavity and the outside, and that is warned on. Not checked: the
  floor under the deepest point of the cavity, the material between a cavity
  and a channel, and the wall between adjacent cavities in a grid. The
  silhouette distance field can answer all three.

- **Undercuts are reported as a number, not a place.** "93% releases cleanly"
  is honest but not actionable. The rays that fail are already computed —
  drawing them in the preview overlay would show you *which* fin is the
  problem.

## 6. Injection, for people who pour a lot

- **More than one gate** on a long bait, so it fills from both ends.
- **Balanced runners** — equal flow length to every cavity, so they fill
  together instead of the near ones packing out first.
- **Overflow wells** past the last cavity to catch the cold slug.
- **Gate shape** — a tab or fan gate breaks off more cleanly than a round one.

**Effort:** moderate, and only worth it for multi-cavity users.

## 7. Model handling

- **Different lures in one mold.** Today it is N x M of the same body. A mixed
  plate is a bigger change: `compute_layout` assumes one `LureDims`.
- **A size family from one model** — 3", 4" and 5" of the same bait in one
  mold, which is just per-cavity scaling.
- **Roll control.** PCA picks the split plane, and the ray-cast scoring picks
  the height, but there is no way to rotate the lure about its own long axis.
  For a bait whose best split is not the principal plane, that is the missing
  handle.

## 8. Output and housekeeping

- **Export from the dialog** — STL or 3MF, both halves, correct orientation,
  one click. The grouping work for this is already done.
- **Clean up old molds.** In a Part Design document the timeline will not
  release previous molds, and they accumulate: the test document reached ten of
  them, about 600,000 triangles, and that alone took a build from 50 to 76
  seconds. Deleting the timeline features rather than the bodies may work —
  worth an experiment, because right now the only advice is "use a different
  document type".
- **Named presets** — worm, craw, swimbait — rather than settings remembered
  per body.

---

## What I would do first

**Weight readout, bed fit, and scale sanity**, together. They are a couple of
hours between them, and they change what the tool tells you on every single
run.

**Then clamping.** It is the difference between a mold that is right and a mold
that works.

**Then the Check button**, because by that point there is enough to check that
waiting 50 seconds to find out is the wrong shape for the tool.
