/* Regression cover for the meter on the writing screen.
 *
 * writing.js is an IIFE that touches `document` the moment it loads, so it
 * cannot be require()d. Rather than restructure shipped code to suit a test,
 * this pulls the pure functions and their tuning constants straight out of the
 * file and runs those. It therefore tests what actually ships, and a rename
 * fails loudly here instead of quietly testing a stale copy.
 *
 * What is covered is the arithmetic behind two promises the page makes while a
 * finger is still on the screen: the meter fills as the letter is traced, and a
 * spark appears only where the finger is genuinely on the line. Both are the
 * page's own approximations -- the mark comes from the server -- but a meter
 * that fills for ink nowhere near the letter is a lie told in real time.
 *
 * Run directly with `node tests/js/writing_trace.test.js`, or through pytest,
 * which shells out to exactly that.
 */

const fs = require("fs");
const path = require("path");

const SOURCE = path.join(__dirname, "..", "..", "src", "pensum", "web", "static", "writing.js");
const src = fs.readFileSync(SOURCE, "utf8");

/* --- pulling the real code out ------------------------------------------ */

function grabFunction(name) {
  const start = src.indexOf("function " + name + "(");
  if (start < 0) throw new Error(`writing.js no longer defines ${name}()`);
  let depth = 0;
  for (let j = src.indexOf("{", start); j < src.length; j++) {
    if (src[j] === "{") depth++;
    else if (src[j] === "}" && --depth === 0) return src.slice(start, j + 1);
  }
  throw new Error(`unbalanced braces in ${name}()`);
}

/* The constants matter as much as the code: a harness carrying its own copy
 * would go on passing after someone changed one in the file that ships. */
const constants = {};
for (const [, name, value] of src.matchAll(/var\s+([A-Z][A-Z_]+)\s*=\s*(-?[\d.]+)\s*;/g)) {
  constants[name] = Number(value);
}
for (const name of ["TOLERANCE", "MIN_STEP", "MAX_POINTS", "MAX_STROKES", "SNAP_PULL", "TAP_MARK"]) {
  if (!(name in constants)) throw new Error(`writing.js no longer defines ${name}`);
}
const TOLERANCE = constants.TOLERANCE;
const SNAP_PULL = constants.SNAP_PULL;
const TAP_MARK = constants.TAP_MARK;

const loaded = new Function(
  [
    "var TOLERANCE = " + TOLERANCE + ";",
    grabFunction("distance"),
    grabFunction("nearest"),
    grabFunction("nearestPoint"),
    grabFunction("snapped"),
    grabFunction("tapMark"),
    grabFunction("covered"),
    grabFunction("pathData"),
    "return { distance, nearest, nearestPoint, snapped, tapMark, covered, pathData };",
  ].join("\n")
)();

const { distance, nearest, nearestPoint, snapped, tapMark, covered, pathData } = loaded;

/* --- a tiny harness ------------------------------------------------------ */

let failures = 0;

function check(what, condition) {
  if (condition) return;
  failures++;
  console.error("FAIL: " + what);
}

function near(what, actual, expected, slack) {
  check(`${what} (got ${actual}, wanted ${expected})`, Math.abs(actual - expected) <= slack);
}

/* A vertical stem, sampled the way the page samples a guide path. */
function stem(from, to, step) {
  const points = [];
  for (let y = from; y <= to; y += step || 2) points.push([50, y]);
  return points;
}

/* --- distance and nearest ------------------------------------------------ */

near("distance across a right triangle", distance([0, 0], [3, 4]), 5, 0.0001);
near("distance to itself", distance([7, 7], [7, 7]), 0, 0.0001);

const guide = stem(20, 120);
near("ink on the line is on the line", nearest([50, 70], guide), 0, 1.01);
near("ink beside the line", nearest([62, 70], guide), 12, 1.01);
near("ink beyond the end of the line", nearest([50, 140], guide), 20, 1.01);

/* --- the meter ----------------------------------------------------------- */

check("no ink fills nothing", covered(guide, []) === 0);

near("a full trace fills the meter", covered(guide, [stem(20, 120, 3)]), 1, 0.0001);

/* Half the stem, plus the fingertip's worth of guide the ink's end reaches --
 * the halo is the tolerance doing its job, not slack in the measurement. The
 * guide runs 100 units, the ink stops at 70, and everything within TOLERANCE of
 * that counts. */
near(
  "ink half way up fills half the meter, plus a fingertip",
  covered(guide, [stem(20, 70, 3)]),
  (70 + TOLERANCE - 20) / 100,
  0.03
);

near(
  "ink a whole letter away fills nothing",
  covered(guide, [stem(20, 120, 3).map((p) => [p[0] + 40, p[1]])]),
  0,
  0.0001
);

/* Two strokes together cover what neither covers alone -- the case that made
 * this function take a list rather than one stroke. */
near(
  "two part-strokes together fill the meter",
  covered(guide, [stem(20, 70, 3), stem(70, 120, 3)]),
  1,
  0.0001
);

/* The tolerance is a fingertip, and it has to behave like one at its edge: just
 * inside counts, just outside does not. */
near(
  "ink just inside the tolerance still counts",
  covered(guide, [stem(20, 120, 3).map((p) => [p[0] + (TOLERANCE - 1), p[1]])]),
  1,
  0.0001
);
near(
  "ink just outside the tolerance does not",
  covered(guide, [stem(20, 120, 3).map((p) => [p[0] + (TOLERANCE + 1), p[1]])]),
  0,
  0.0001
);

/* --- tidying the ink ----------------------------------------------------- */

/* The promise the snapping makes is narrow, and both halves of it matter: ink
 * that is already on the line is pulled onto it, and ink that is not is left
 * exactly where the finger put it. Without the second half, "auto-perfecting"
 * would quietly turn a scribble into a letter and score it as one. */
{
  const guides = [stem(20, 120)];

  const wobble = snapped([53, 70], guides, TOLERANCE, SNAP_PULL);
  check("ink beside the line is pulled towards it", distance(wobble, [50, 70]) < 3);
  check("ink beside the line is not teleported onto it", distance(wobble, [50, 70]) > 0);

  const astray = snapped([90, 70], guides, TOLERANCE, SNAP_PULL);
  check("ink well off the line is left alone", astray[0] === 90 && astray[1] === 70);

  const already = snapped([50, 70], guides, TOLERANCE, SNAP_PULL);
  check("ink on the line stays on the line", nearest(already, guides[0]) < 0.001);

  near("the nearest guide point is the one below the ink", nearestPoint([53, 70], guides).gap, 3, 1.01);
  check("no guides means nothing to snap to", nearestPoint([53, 70], []).at === null);
}

/* --- a tap is a dot ------------------------------------------------------- */

/* The dot on an `i` is a 4-unit stroke in the letterforms, and a tap has no
 * length at all. Before this, a tap was discarded for having one point, so the
 * only way to dot an `i` was to wiggle -- and the letter scored half marks for
 * a dot that was never accepted. */
{
  const dot = tapMark([50, 40], TAP_MARK);
  check("a tap becomes two points", dot.length === 2);
  check("a tap keeps its place", dot[0][0] === 50 && dot[1][0] === 50);
  near("a tap is as long as the dot it draws", distance(dot[0], dot[1]), TAP_MARK, 0.0001);
  /* Downwards, which is the direction the `i` and `j` dots are authored in.
   * Backwards would cost the stroke a quarter of its mark for a gesture that
   * has no direction at all. */
  check("a tap runs downwards", dot[0][1] < dot[1][1]);

  const guide = [
    [50, 38],
    [50, 40],
    [50, 42],
  ];
  near("a tapped dot covers the dot", covered(guide, [dot]), 1, 0.0001);
}

/* --- what gets drawn ----------------------------------------------------- */

const drawn = pathData([
  [1, 2],
  [3.14159, 4],
  [5, 6],
]);
check("ink starts with a move", drawn.startsWith("M1.0 2.0"));
check("ink continues with lines", drawn.split("L").length === 3);
check("ink coordinates are trimmed", drawn.indexOf("3.1 4.0") > 0);
/* The server parses exactly this subset. A page that emitted anything else
 * would draw fine and post something unmarkable. */
check("ink uses only M and L", /^[ML\d\s.,-]+$/.test(drawn));

if (failures) {
  console.error(`${failures} check(s) failed`);
  process.exit(1);
}
console.log("writing.js: all checks passed");
