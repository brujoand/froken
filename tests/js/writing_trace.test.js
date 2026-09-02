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
for (const name of ["TOLERANCE", "MIN_STEP", "MAX_POINTS", "MAX_STROKES"]) {
  if (!(name in constants)) throw new Error(`writing.js no longer defines ${name}`);
}
const TOLERANCE = constants.TOLERANCE;

const loaded = new Function(
  [
    "var TOLERANCE = " + TOLERANCE + ";",
    grabFunction("distance"),
    grabFunction("nearest"),
    grabFunction("covered"),
    grabFunction("pathData"),
    "return { distance: distance, nearest: nearest, covered: covered, pathData: pathData };",
  ].join("\n")
)();

const { distance, nearest, covered, pathData } = loaded;

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
