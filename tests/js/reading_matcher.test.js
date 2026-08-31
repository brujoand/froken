/* Regression cover for the live highlight.
 *
 * reading.js is an IIFE that touches `document` the moment it loads, so it
 * cannot be require()d. Rather than restructure shipped code to suit a test,
 * this pulls the pure functions and their tuning constants straight out of the
 * file and runs those. It therefore tests what actually ships, and a rename
 * fails loudly here instead of quietly testing a stale copy.
 *
 * Run directly with `node tests/js/reading_matcher.test.js`, or through pytest,
 * which shells out to exactly that.
 */

const fs = require("fs");
const path = require("path");

const SOURCE = path.join(__dirname, "..", "..", "src", "pensum", "web", "static", "reading.js");
const src = fs.readFileSync(SOURCE, "utf8");

/* --- pulling the real code out ------------------------------------------ */

function grabFunction(name) {
  const start = src.indexOf("function " + name + "(");
  if (start < 0) throw new Error(`reading.js no longer defines ${name}()`);
  let depth = 0;
  for (let j = src.indexOf("{", start); j < src.length; j++) {
    if (src[j] === "{") depth++;
    else if (src[j] === "}" && --depth === 0) return src.slice(start, j + 1);
  }
  throw new Error(`unbalanced braces in ${name}()`);
}

/* The constants matter as much as the code: a harness carrying its own copy
 * would go on passing after someone widened the lookahead in the real file.
 * So every SHOUTING_CASE number in reading.js is pulled in by name and brought
 * into scope for the scraped functions to close over. The ones this file names
 * are then asserted to exist -- a scraper that silently finds nothing is worse
 * than no scraper. */
const constants = {};
for (const [, name, value] of src.matchAll(/var\s+([A-Z][A-Z_]+)\s*=\s*(-?[\d.]+)\s*;/g)) {
  constants[name] = Number(value);
}

for (const name of [
  "NEAR",
  "FAR",
  "DISTINCTIVE",
  "FUZZY_MIN_LENGTH",
  "MIN_SHARED_PREFIX",
  "MAX_LENGTH_DIFFERENCE",
  "PREFIX_SHARE",
  "CLOSE_ENOUGH",
]) {
  if (!(name in constants)) throw new Error(`reading.js no longer defines a numeric ${name}`);
}

let cursor = 0;
let consumed = 0;
let reference = [];
function lightTo() {}

/* Declares NEAR, FAR and the rest at this scope, which is what the scraped
 * functions below close over -- and what the messages here read back. */
for (const [name, value] of Object.entries(constants)) {
  eval(`var ${name} = ${value};`);
}

eval(grabFunction("similarity"));
eval(grabFunction("closeEnough"));
eval(grabFunction("advanceCursor"));

/* --- the harness -------------------------------------------------------- */

let failures = 0;

function check(what, got, want) {
  if (got === want) {
    console.log(`  ok    ${what}`);
    return;
  }
  console.log(`  FAIL  ${what}: got ${got}, wanted ${want}`);
  failures++;
}

/* Written for the test, and deliberately full of the short common words that
 * are what made the old lookahead misbehave. */
const PASSAGE = (
  "katten min tror at den er en stovsuger den gar rundt i stua og suger opp " +
  "alt den finner i gar spiste den en sokk en blyant og halve avisa mamma " +
  "sier at katter ikke kan suge men jeg vet bedre for jeg har sett det selv"
).split(" ");

function reset() {
  cursor = 0;
  consumed = 0;
  reference = PASSAGE;
}

/* One interim result: the recogniser re-sends the whole transcript so far. */
function hear(words, times) {
  for (let n = 0; n < (times || 1); n++) advanceCursor(words.slice());
}

/* --- the bug this file exists for --------------------------------------- */

/* The recogniser revises constantly, and the cursor only moves forward. Before
 * the fix, re-reading already-consumed words ratcheted the highlight down the
 * page: six words read reached word 53 of 57. */
reset();
hear(PASSAGE.slice(0, 6), 12);
check("six words read, twelve interim results", cursor, 6);

/* The same, sustained. Silence is not progress. */
reset();
hear(PASSAGE.slice(0, 3), 200);
check("three words, two hundred interim results", cursor, 3);

/* --- and the failure the fix could have introduced ---------------------- */

function readThrough(transform) {
  reset();
  for (let n = 1; n <= PASSAGE.length; n++) {
    const said = PASSAGE.slice(0, n);
    hear(transform ? transform(said) : said, 3);
  }
  return cursor;
}

check("a whole passage read through", readThrough(), PASSAGE.length);
check(
  "a child who drops every ninth word",
  readThrough((said) => said.filter((_, i) => i % 9 !== 4)),
  PASSAGE.length
);
check(
  "a recogniser that inflects endings differently",
  readThrough((said) =>
    said.map((w) => (w === "katten" ? "katta" : w === "stua" ? "stuen" : w))
  ),
  PASSAGE.length
);

/* --- the lookahead rule itself ------------------------------------------ */

/* A short common word must not reach across the passage to a later copy of
 * itself. "og" appears at 13 and again at 30; from a cursor of 0 neither is
 * within NEAR, so it must move nothing. */
reset();
hear(["og"]);
check(`a common short word reaches no further than ${NEAR}`, cursor, 0);

/* A distinctive one may, because hearing it really is evidence of position. */
reset();
hear(["stovsuger"]);
check(`a distinctive word reaches up to ${FAR}`, cursor, PASSAGE.indexOf("stovsuger") + 1);

/* ...but not past the far limit. "avisa" sits beyond it. */
reset();
hear(["avisa"]);
check(`nothing reaches past ${FAR}`, cursor, PASSAGE.indexOf("avisa") < FAR ? PASSAGE.indexOf("avisa") + 1 : 0);

/* --- what counts as the same word --------------------------------------- */

check("a word matches itself", closeEnough("sokk", "sokk"), true);
check("an inflected ending is the same word", closeEnough("trappa", "trappen"), true);
check("a different word is not", closeEnough("hest", "hus"), false);
check(
  `below ${FUZZY_MIN_LENGTH} characters only an exact match counts`,
  closeEnough("kan", "ken"),
  false
);
/* The accepted cost of the stem rule, recorded so it is a decision and not a
 * surprise. The server makes the same trade in fluency.close_enough. */
check("the stem rule's known cost: store/storm", closeEnough("store", "storm"), true);
check("a shared stem carries a changed ending", closeEnough("boka", "boken"), true);

console.log(failures === 0 ? "\nall checks passed" : `\n${failures} check(s) failed`);
process.exit(failures === 0 ? 0 : 1);
