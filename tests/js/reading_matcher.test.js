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
let status = [];
let misses = 0;
let reference = [];
function lightTo() {}

/* Declares NEAR, FAR and the rest at this scope, which is what the scraped
 * functions below close over -- and what the messages here read back. */
for (const [name, value] of Object.entries(constants)) {
  eval(`var ${name} = ${value};`);
}

eval(grabFunction("similarity"));
eval(grabFunction("closeEnough"));
eval(grabFunction("runMatchesAt"));
eval(grabFunction("resync"));
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
  status = [];
  misses = 0;
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
 * itself. "og" appears well beyond NEAR, so hearing it at the start must not
 * jump the highlight there -- it counts as one word of progress, wrongly read,
 * and nothing more. */
reset();
hear(["og"]);
check(`a common short word reaches no further than ${NEAR}`, cursor, 1);
check("...and is recorded as misread rather than as a jump", status[0], "misread");

/* A distinctive one may, because hearing it really is evidence of position. */
reset();
hear(["stovsuger"]);
check(`a distinctive word reaches up to ${FAR}`, cursor, PASSAGE.indexOf("stovsuger") + 1);

/* ...but not past the far limit. A word sitting beyond it moves progress by
 * one, like any word the passage does not account for here. */
reset();
const beyond = PASSAGE.findIndex((w, i) => i >= FAR && w.length >= DISTINCTIVE);
hear([PASSAGE[beyond]]);
check(`nothing reaches past ${FAR}`, cursor, 1);

/* --- progress and correctness are two questions -------------------------- */

/* The reported failure: the highlight fell behind and never caught up, because
 * a word the recogniser got wrong stopped the cursor dead. Progress must track
 * how much was said, whether or not it was said correctly. */
reset();
hear(["blablabla", "blablabla", "blablabla", "blablabla", "blablabla"]);
check("progress keeps up even when nothing is recognised", cursor, 5);
check("...and every one of them is marked misread", status.slice(0, 5).join(), "misread,misread,misread,misread,misread");

/* One bad word in the middle must not cost the words after it. */
reset();
const withOneWrong = PASSAGE.slice(0, 5);
withOneWrong[2] = "blablabla";
hear(withOneWrong);
check("a misheard word does not stall the rest", cursor, 5);
check("...the bad one is marked misread", status[2], "misread");
check("...and the one after it is not", status[3], "read");

/* Skipping ahead marks what was passed, rather than silently lighting it. */
reset();
hear([PASSAGE[0], PASSAGE[7]]);
check("a skipped-to word marks what was stepped over", status[3], "misread");
check("...and the word actually read is not", status[7], "read");

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


/* --- losing the reader, and finding them again --------------------------- */

/* The reported failure, and the reason resync exists. The recogniser emits one
 * word that is not in the passage -- a filler, a cough, one word heard as two --
 * and the cursor is one ahead for ever after. Every word the child then reads is
 * compared against the word after the one they are saying, so all of it is
 * marked wrong. With resync disabled this passage scores 3 read and 44 misread. */
function tally() {
  let read = 0;
  let misread = 0;
  for (let i = 0; i < PASSAGE.length; i++) {
    if (status[i] === "read") read++;
    else if (status[i] === "misread") misread++;
  }
  return { read, misread };
}

function heardWithInsertionAt(position, word) {
  const heard = [];
  PASSAGE.forEach((w, i) => {
    heard.push(w);
    if (i === position) heard.push(word);
  });
  return heard;
}

reset();
hear(heardWithInsertionAt(1, "eh"));
check("a spurious word does not cost the rest of the passage", tally().misread, 0);
check("...and the whole passage still reads as read", tally().read, PASSAGE.length);

/* Late in the passage as well as early: recovery must not depend on there being
 * plenty of text left to recover across. */
reset();
hear(heardWithInsertionAt(PASSAGE.length - 8, "eh"));
check("...including when it happens near the end", tally().misread, 0);

/* A clean reading must be untouched by any of this. */
reset();
hear(PASSAGE.slice());
check("a clean reading is still entirely correct", tally().read, PASSAGE.length);

/* One genuinely wrong word is not a lost cursor, and must not move anything.
 * Resync waking up on every mishearing would drag the highlight around on
 * exactly the readings that need it to stay still. */
reset();
const oneWrong = PASSAGE.slice();
oneWrong[10] = "blablabla";
hear(oneWrong);
check("one wrong word costs exactly one word", tally().misread, 1);

console.log(failures === 0 ? "\nall checks passed" : `\n${failures} check(s) failed`);
process.exit(failures === 0 ? 0 : 1);
