/* Which voice speaks the word.
 *
 * This is the one failure on the listening screen that is silent in every
 * sense. An English voice reading "kjøleskap" does not say a Norwegian word
 * badly -- it says a different word, and the child is then marked on spelling
 * something they were never told. Nothing on the page looks wrong while it
 * happens.
 *
 * listening.js is an IIFE that touches `document` the moment it loads, so it
 * cannot be require()d. Rather than restructure shipped code to suit a test,
 * this pulls the pure function and its table straight out of the file and runs
 * those. It therefore tests what actually ships, and a rename fails loudly here
 * instead of quietly testing a stale copy.
 *
 * Run directly with `node tests/js/listening_voice.test.js`, or through pytest,
 * which shells out to exactly that.
 */

const fs = require("fs");
const path = require("path");

const SOURCE = path.join(__dirname, "..", "..", "src", "pensum", "web", "static", "listening.js");
const src = fs.readFileSync(SOURCE, "utf8");

/* --- pulling the real code out ------------------------------------------ */

function grabFunction(name) {
  const start = src.indexOf("function " + name + "(");
  if (start < 0) throw new Error(`listening.js no longer defines ${name}()`);
  let depth = 0;
  for (let j = src.indexOf("{", start); j < src.length; j++) {
    if (src[j] === "{") depth++;
    else if (src[j] === "}" && --depth === 0) return src.slice(start, j + 1);
  }
  throw new Error(`unbalanced braces in ${name}()`);
}

/* The language table matters as much as the code: a harness carrying its own
 * copy would go on passing after someone changed the one that ships. */
function grabObject(name) {
  const start = src.indexOf("var " + name + " = {");
  if (start < 0) throw new Error(`listening.js no longer defines ${name}`);
  const end = src.indexOf("};", start);
  return src.slice(start, end + 2);
}

const loaded = new Function(
  [grabObject("VOICE_LANGS"), grabFunction("pickVoice"), "return pickVoice;"].join("\n")
)();
const pickVoice = loaded;

/* --- a tiny harness ------------------------------------------------------ */

let failures = 0;

function check(what, condition) {
  if (condition) return;
  failures++;
  console.error(`FAIL: ${what}`);
}

function voice(lang, name, localService = true) {
  return { lang, name, localService };
}

/* --- the checks ---------------------------------------------------------- */

const norwegian = voice("nb-NO", "Nora");
const norwegianOld = voice("no-NO", "Nils");
const english = voice("en-GB", "Daniel");
const american = voice("en-US", "Samantha");
const german = voice("de-DE", "Anna");

check("a bokmål voice reads bokmål", pickVoice([english, norwegian], "nb") === norwegian);
check(
  "the older `no` tag is a Norwegian voice too",
  pickVoice([english, norwegianOld], "nb") === norwegianOld
);
check("an English voice reads English", pickVoice([norwegian, english], "en") === english);
check(
  "any English region will do",
  pickVoice([norwegian, american], "en") === american
);

/* The whole point: silence beats the wrong language. */
check("no Norwegian voice means no voice", pickVoice([english, german], "nb") === null);
check("no voices at all means no voice", pickVoice([], "nb") === null);
check("an unknown language is not answered with whatever is first", pickVoice([english], "de") === null);

/* Nynorsk has no voice of its own anywhere. A bokmål one is much closer than
 * silence, and closer than English by a distance that does not need arguing. */
check("nynorsk falls back to a Norwegian voice", pickVoice([english, norwegian], "nn") === norwegian);
check("nynorsk is not read in English", pickVoice([english, german], "nn") === null);

/* A remote voice sends the word to whoever the browser's vendor uses, which is
 * an outbound request this site otherwise never makes. */
const remote = voice("nb-NO", "Cloud", false);
check(
  "a local voice is preferred over a remote one",
  pickVoice([remote, norwegian], "nb") === norwegian
);
check("a remote voice beats no voice at all", pickVoice([remote, english], "nb") === remote);

/* Case and separator vary between browsers and platforms. */
check("an underscore tag is understood", pickVoice([voice("nb_NO", "X")], "nb").name === "X");
check("an upper-case tag is understood", pickVoice([voice("NB-NO", "Y")], "nb").name === "Y");
check("a bare language tag is understood", pickVoice([voice("nb", "Z")], "nb").name === "Z");

/* `en` must not match `en`-prefixed languages that are not English. */
check(
  "a prefix match needs a separator",
  pickVoice([voice("eng-XX", "Wrong")], "en") === null
);

if (failures) {
  console.error(`${failures} check(s) failed`);
  process.exit(1);
}
console.log("listening.js: all checks passed");
