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
 * would go on passing after someone changed one in the file that ships. */
const constants = {};
for (const [, name, value] of src.matchAll(/var\s+([A-Z][A-Z_]+)\s*=\s*(-?[\d.]+)\s*;/g)) {
  constants[name] = Number(value);
}
/* Numeric arrays too -- the heat thresholds are one, and a harness carrying its
 * own copy of them would go on passing after someone retuned the real ones. */
const arrays = {};
for (const [, name, body] of src.matchAll(/var\s+([A-Z][A-Z_]+)\s*=\s*\[([\d,\s]+)\]\s*;/g)) {
  arrays[name] = body.split(",").map((n) => Number(n.trim()));
}
if (!arrays.HEAT_LEVELS) throw new Error("reading.js no longer defines numeric HEAT_LEVELS");
const HEAT_LEVELS = arrays.HEAT_LEVELS;

for (const name of [
  "MIN_ANCHOR",
  "WINDOW_WORDS",
  "WINDOW_HEARD",
  "SETTLED",
  "FUZZY_MIN_LENGTH",
  "MIN_SHARED_PREFIX",
  "MAX_LENGTH_DIFFERENCE",
  "PREFIX_SHARE",
  "CLOSE_ENOUGH",
]) {
  if (!(name in constants)) throw new Error(`reading.js no longer defines a numeric ${name}`);
}

let cursor = 0;
let streak = 0;
let status = [];
let reference = [];
let comparisons = {};
let anchorWord = 0;
let anchorHeard = 0;
function lightTo() {}

/* The follow-along's world. A scroller is a box with a height, a scrollable
 * height and a scrollTop, and a word is an offsetTop and a height -- which is
 * the whole of what centreOn() reads, so a plain object is not a simplified
 * stand-in for one, it is one. */
let running = true;
let furthest = 0;
let scrollTarget = 0;
let scrollNow = 0;
let scrollFrame = 0;
let scroller = null;
let wordSpans = [];

/* Frames are driven by hand rather than by a clock: the point of the glide is
 * how many frames it takes, and a test that waited for real ones could not say. */
let pending = null;
function requestAnimationFrame(fn) {
  pending = fn;
  return 1;
}
function cancelAnimationFrame() {
  pending = null;
}
function runFrames(limit) {
  let frames = 0;
  while (pending && frames < limit) {
    const fn = pending;
    pending = null;
    fn();
    frames++;
  }
  return frames;
}

for (const [name, value] of Object.entries(constants)) {
  eval(`var ${name} = ${value};`);
}

eval(grabFunction("similarity"));
eval(grabFunction("closeEnough"));
eval(grabFunction("same"));
eval(grabFunction("align"));
eval(grabFunction("heatFor"));
eval(grabFunction("advanceCursor"));
eval(grabFunction("centreOn"));
eval(grabFunction("glide"));
eval(grabFunction("keepInView"));

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
 * are what a coincidence match latches onto. */
const PASSAGE = (
  "katten min tror at den er en stovsuger den gar rundt i stua og suger opp " +
  "alt den finner i gar spiste den en sokk en blyant og halve avisa mamma " +
  "sier at katter ikke kan suge men jeg vet bedre for jeg har sett det selv"
).split(" ");

function reset() {
  cursor = 0;
  status = [];
  comparisons = {};
  anchorWord = 0;
  anchorHeard = 0;
  reference = PASSAGE;
}

/* One interim result: the recogniser re-sends the whole transcript so far. */
function hear(words, times) {
  for (let n = 0; n < (times || 1); n++) advanceCursor(words.slice());
}

function tally() {
  let read = 0;
  let misread = 0;
  for (let i = 0; i < PASSAGE.length; i++) {
    if (status[i] === "read") read++;
    else if (status[i] === "misread") misread++;
  }
  return { read, misread };
}

/* --- what the alignment is for ------------------------------------------ */

/* The two cases incremental matching could not tell apart, and the reason it
 * is gone. One word at a time they look identical: a word that does not match.
 * Aligned whole they are different shapes, and neither costs anything after
 * itself. */

reset();
const skipped = PASSAGE.filter((_, i) => i !== 12);
hear(skipped);
check("a skipped word costs exactly one word", tally().misread, 1);
check("...and the rest of the passage is unharmed", tally().read, PASSAGE.length - 1);

reset();
const stumbled = [];
PASSAGE.forEach((w, i) => {
  /* A child stuck on one word, saying something else five times before moving
   * on. The passage word itself is never said correctly. */
  if (i === 12) for (let k = 0; k < 5; k++) stumbled.push("blablabla");
  else stumbled.push(w);
});
hear(stumbled);
check("one word read wrong five times costs exactly one word", tally().misread, 1);
check("...and the rest of the passage is unharmed", tally().read, PASSAGE.length - 1);

/* A repeated word -- a child re-reading one they tripped on -- costs nothing,
 * because a subsequence match simply skips the extra copies. */
reset();
const repeated = [];
PASSAGE.forEach((w, i) => {
  repeated.push(w);
  if (i === 12) repeated.push(w, w);
});
hear(repeated);
check("re-reading a word costs nothing at all", tally().misread, 0);

/* --- being out of step is no longer a state ----------------------------- */

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
check("a spurious word early does not cost the passage", tally().misread, 0);
check("...and the whole passage still reads as read", tally().read, PASSAGE.length);

reset();
hear(heardWithInsertionAt(PASSAGE.length - 8, "eh"));
check("...nor does one near the end", tally().misread, 0);

/* --- the transcript arrives whole, over and over ------------------------ */

/* The recogniser re-sends everything on every interim result. Alignment is a
 * function of the transcript, so hearing the same thing again cannot move
 * anything -- the ratchet that used to march the highlight down the page is
 * not expressible any more. */
reset();
hear(PASSAGE.slice(0, 6), 12);
check("six words read, twelve interim results", cursor, 6);

reset();
hear(PASSAGE.slice(0, 3), 200);
check("three words, two hundred interim results", cursor, 3);

/* --- a clean reading, and a sloppy one ---------------------------------- */

reset();
hear(PASSAGE.slice());
check("a whole passage read through", cursor, PASSAGE.length);
check("...with every word correct", tally().read, PASSAGE.length);

reset();
hear(PASSAGE.filter((_, i) => i % 9 !== 4));
check("a child who drops every ninth word still reaches the end", cursor, PASSAGE.length);

reset();
hear(PASSAGE.map((w) => (w === "katten" ? "katta" : w === "stua" ? "stuen" : w)));
check("endings the recogniser inflects differently are not errors", tally().misread, 0);

/* --- coincidence must not place the highlight --------------------------- */

/* "og" appears twice, well into the passage. Hearing it once at the start is
 * not evidence of anything, and believing it would teleport the highlight
 * there -- which is the bug the old lookahead limits existed to prevent, and
 * which aligning whole would reopen without MIN_ANCHOR. */
reset();
hear(["og"]);
check(`one matching word is below MIN_ANCHOR (${MIN_ANCHOR})`, cursor, 1);
check("...and asserts nothing about any word", tally().read + tally().misread, 0);

/* Progress still keeps up when the recogniser produces nothing usable: the
 * reader has said five words, wherever they are. */
reset();
hear(["blablabla", "blablabla", "blablabla", "blablabla", "blablabla"]);
check("progress keeps up when nothing is recognised", cursor, 5);
check("...but no word is called wrong on no evidence", tally().misread, 0);

/* The same, but after the reader has been placed. Speech recognition is not
 * instant, so the transcript is always a little behind the voice -- and the
 * words it has not caught up with yet are still words that were said. Stopping
 * the highlight at the last confirmed match is what makes it lag visibly and
 * never recover. */
reset();
hear(PASSAGE.slice(0, 10).concat(["mmm", "mmm", "mmm", "mmm", "mmm"]));
check("the highlight keeps moving past the last confirmed word", cursor, 15);
check("...and still asserts nothing about the words it passed", tally().misread, 0);
check("...while the ten it did confirm stay confirmed", tally().read, 10);

/* --- how warm the line gets ------------------------------------------- */

/* A run of correct words, and nothing else. Not a score, not shown as one --
 * it decides the colour of the line and how many flames sit in the HUD. */

function runOf(correct, thenWrong) {
  status = [];
  for (let i = 0; i < correct; i++) status[i] = "read";
  if (thenWrong) status[correct] = "misread";
  return heatFor(correct + (thenWrong ? 1 : 0));
}

check("a short run is not yet warm", runOf(HEAT_LEVELS[0] - 1, false), 0);
check("the first threshold lights one flame", runOf(HEAT_LEVELS[0], false), 1);
check("the second lights two", runOf(HEAT_LEVELS[1], false), 2);
check("the third lights three", runOf(HEAT_LEVELS[2], false), 3);

/* One wrong word ends the run rather than reducing it: the line cooling the
 * moment it goes wrong is the honest answer to "how is it going right now",
 * and it warms again exactly as fast as it cooled. */
check("a wrong word puts it out", runOf(HEAT_LEVELS[2], true), 0);

/* And the run is the one ending where the reader is, not the best one in the
 * passage -- a child who read fifty words then stumbled is not still on fire. */
status = [];
for (let i = 0; i < HEAT_LEVELS[2]; i++) status[i] = "read";
status[HEAT_LEVELS[2]] = "misread";
for (let i = HEAT_LEVELS[2] + 1; i < HEAT_LEVELS[2] + 4; i++) status[i] = "read";
check("only the current run counts", heatFor(HEAT_LEVELS[2] + 4), 0);

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

/* --- following the reader down the page ---------------------------------- */

/* A screen 800 tall, a passage of 60 lines 40 apart, ten words to a line, and
 * the top padding that lets the first line reach the middle. */
const LINE = 40;
const PER_LINE = 10;
const PAD = 300;
const VIEW = 800;

function layOutPassage() {
  scroller = {
    clientHeight: VIEW,
    scrollHeight: PAD * 2 + 60 * LINE,
    scrollTop: 0,
  };
  wordSpans = [];
  for (let i = 0; i < 60 * PER_LINE; i++) {
    wordSpans.push({
      offsetTop: PAD + Math.floor(i / PER_LINE) * LINE,
      offsetHeight: LINE,
    });
  }
  running = true;
  furthest = 0;
  scrollTarget = 0;
  scrollNow = 0;
  scrollFrame = 0;
  pending = null;
}

layOutPassage();

/* The reason the passage used to lurch. The old rule left it alone while the
 * word stayed inside a band and then moved it far enough to put the word back
 * at the band's other edge -- so nothing, nothing, nothing, most of a screen.
 * Aiming at the middle of the screen every time makes every move one line. */
const line20 = centreOn(20 * PER_LINE);
const line21 = centreOn(21 * PER_LINE);
check("moving to the next line moves the passage one line", line21 - line20, LINE);

check("every word on a line has the same target", centreOn(20 * PER_LINE + 7), line20);
check(
  "so a line is read without the passage moving at all",
  centreOn(20 * PER_LINE + 9) - centreOn(20 * PER_LINE),
  0
);

/* The line the reader is on ends up in the middle of the screen, not a third of
 * the way down it, and not wherever a band's edge happens to fall. */
const middleOfScreen = wordSpans[30 * PER_LINE].offsetTop + LINE / 2 - centreOn(30 * PER_LINE);
check("the line being read sits in the middle of the screen", middleOfScreen, VIEW / 2);

/* Both ends. Without the clamp the first line asks the passage to scroll above
 * its own top, which browsers refuse, so the follow-along would think it had
 * moved and the passage would not have. */
check("the first line cannot scroll off the top", centreOn(0), 0);
check(
  "the last line cannot scroll past the bottom",
  centreOn(wordSpans.length - 1),
  scroller.scrollHeight - scroller.clientHeight
);

/* The glide itself: it has to arrive, and it has to take more than one frame
 * getting there. One frame is the old behaviour under a new name. */
layOutPassage();
keepInView(21 * PER_LINE);

/* The whole point, stated as the thing that must not happen: one frame must not
 * be enough. Counting frames is not enough to say that -- a straight assignment
 * still burns a second frame noticing it has arrived -- so this asks where the
 * passage actually is after the first one. */
runFrames(1);
const afterOne = scroller.scrollTop;
check("one frame does not get the whole way there", afterOne < scrollTarget - 1, true);
check("but it does move", afterOne > 0, true);
check(
  `and it covers about ${GLIDE} of the distance`,
  Math.round((afterOne / scrollTarget) * 100) / 100,
  GLIDE
);

const frames = 1 + runFrames(600);
check("the glide arrives", Math.round(scroller.scrollTop), Math.round(scrollTarget));
check("but settles rather than running forever", frames < 100, true);

/* Never past the target and never backwards: an overshoot would show as the
 * line the reader is on sliding up out of the middle and coming back. */
layOutPassage();
keepInView(30 * PER_LINE);
let overshot = false;
let backwards = false;
let previous = -1;
while (pending) {
  const fn = pending;
  pending = null;
  fn();
  if (scroller.scrollTop > scrollTarget + 0.5) overshot = true;
  if (scroller.scrollTop < previous) backwards = true;
  previous = scroller.scrollTop;
}
check("the glide never overshoots", overshot, false);
check("and never doubles back", backwards, false);

/* The recogniser revises, so the cursor moves back a word all the time. The
 * passage must not follow it back up the page. */
layOutPassage();
keepInView(30 * PER_LINE);
runFrames(600);
const settled = scrollTarget;
keepInView(10 * PER_LINE);
check("a cursor that moves back does not scroll the passage back", scrollTarget, settled);

/* A target that has not changed must not restart the loop -- that is what makes
 * this cheap enough to call on every recogniser update. */
layOutPassage();
keepInView(21 * PER_LINE);
runFrames(600);
check("an update that changes nothing starts no frame", (keepInView(21 * PER_LINE), pending), null);

/* Nothing happens once the reading has stopped. */
layOutPassage();
running = false;
keepInView(30 * PER_LINE);
check("a stopped reading does not scroll", scrollTarget, 0);

console.log(failures === 0 ? "\nall checks passed" : `\n${failures} check(s) failed`);
process.exit(failures === 0 ? 0 : 1);
