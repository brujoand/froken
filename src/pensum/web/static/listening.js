/* The listening screen: a word is said, and the pupil either picks it or spells
 * it.
 *
 * Written by hand and vendored like everything else on this site: no bundler,
 * no dependency, no third-party origin. The voice is the browser's own
 * SpeechSynthesis, so no audio is fetched and none is sent.
 *
 * The one thing this file takes seriously is which voice speaks. An English
 * voice reading "kjøleskap" does not say a Norwegian word badly, it says a
 * different word entirely -- and the child is then marked on spelling something
 * they were never told. So the exercise refuses to start unless a voice for the
 * passage's own language exists, and says so plainly instead of guessing.
 *
 * Nothing is stored. The answers go to this site's own origin once, at the end,
 * and are gone.
 */
(function () {
  "use strict";

  /* Slower than conversation. A word said at reading pace is a word a
   * seven-year-old has to ask for twice. */
  var RATE = 0.85;

  /* How long to wait for the voice list. Chrome populates it asynchronously and
   * fires `voiceschanged`; some builds fire it late, and a few never fire it at
   * all, so the wait ends by itself rather than leaving a page that never
   * starts. */
  var VOICE_WAIT_MS = 1500;

  /* Which BCP-47 prefixes count as a voice for one of our languages. Norwegian
   * voices label themselves `nb-NO`, `no-NO` or, rarely, `nn-NO`, and all three
   * read bokmål text correctly; nynorsk has no voice of its own anywhere, and a
   * bokmål voice is much closer than silence. */
  var VOICE_LANGS = {
    nb: ["nb", "no", "nn"],
    nn: ["nn", "nb", "no"],
    en: ["en"],
  };

  var root = document.getElementById("listening");
  if (!root) return;

  var speech = window.speechSynthesis;
  var questions = Array.prototype.slice.call(root.querySelectorAll(".listening-question"));
  var position = document.getElementById("listening-position");
  var hint = document.getElementById("listening-hint");
  var output = document.getElementById("listening-result");

  var mode = root.dataset.mode;
  var language = root.dataset.language;
  var postUrl = root.dataset.postUrl;
  var total = questions.length;

  var given = [];
  for (var i = 0; i < total; i++) given.push("");

  var at = 0;
  var finished = false;
  var voice = null;

  /* --- the voice ---------------------------------------------------------- */

  /* Pure, and pulled out by the node test: picking the wrong voice is the one
   * failure on this page that is silent in every sense.
   *
   * A voice marked `localService` is preferred where there is a choice, because
   * a remote one sends the word to whoever the browser's vendor uses -- which is
   * an outbound request this site otherwise never makes. */
  function pickVoice(voices, language) {
    var wanted = VOICE_LANGS[language] || [language];
    var best = null;
    var bestRank = Infinity;
    for (var i = 0; i < voices.length; i++) {
      var tag = String(voices[i].lang || "")
        .toLowerCase()
        .replace("_", "-");
      for (var j = 0; j < wanted.length; j++) {
        if (tag === wanted[j] || tag.indexOf(wanted[j] + "-") === 0) {
          /* Earlier in `wanted` wins; a local voice beats a remote one at the
           * same language. */
          var rank = j * 2 + (voices[i].localService ? 0 : 1);
          if (rank < bestRank) {
            bestRank = rank;
            best = voices[i];
          }
          break;
        }
      }
    }
    return best;
  }

  function withVoices(then) {
    if (!speech) return then([]);
    var settled = false;
    function done() {
      if (settled) return;
      settled = true;
      then(speech.getVoices() || []);
    }
    var have = speech.getVoices() || [];
    if (have.length) return done();
    speech.addEventListener("voiceschanged", done);
    window.setTimeout(done, VOICE_WAIT_MS);
  }

  function speak(word) {
    if (!speech || !voice) return;
    /* One word at a time. Without this, tapping "say it again" twice queues two
     * readings and the second talks over nothing. */
    speech.cancel();
    var utterance = new window.SpeechSynthesisUtterance(word);
    utterance.voice = voice;
    utterance.lang = voice.lang;
    utterance.rate = RATE;
    speech.speak(utterance);
  }

  /* --- moving through the round ------------------------------------------- */

  function say(message) {
    if (!hint) return;
    hint.textContent = message || "";
    hint.hidden = !message;
  }

  function label(template, values) {
    var out = template || "";
    for (var key in values) {
      if (Object.prototype.hasOwnProperty.call(values, key)) {
        out = out.split(key).join(values[key]);
      }
    }
    return out;
  }

  function show(index) {
    for (var i = 0; i < total; i++) {
      questions[i].hidden = i !== index;
    }
    if (position) {
      position.textContent = label(root.dataset.labelPosition, {
        "%1": index + 1,
        "%2": total,
      });
    }
    var input = questions[index].querySelector(".listening-input");
    if (input) input.focus();
    speak(questions[index].dataset.word);
  }

  function record(index, answer) {
    given[index] = answer;
    if (index + 1 < total) {
      at = index + 1;
      show(at);
    } else {
      finish();
    }
  }

  function finish() {
    if (finished) return;
    finished = true;
    if (speech) speech.cancel();
    say(root.dataset.labelWorking);

    window
      .fetch(postUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ given: given }),
      })
      .then(function (response) {
        if (!response.ok) throw new Error("mark failed");
        return response.text();
      })
      .then(function (html) {
        say("");
        for (var i = 0; i < total; i++) questions[i].hidden = true;
        output.innerHTML = html;
        root.classList.add("listening--done");
        var again = document.getElementById("listening-again");
        if (again) again.addEventListener("click", restart);
        output.scrollIntoView({ block: "nearest" });
      })
      .catch(function () {
        finished = false;
        say(root.dataset.labelFailed);
      });
  }

  function restart() {
    for (var i = 0; i < total; i++) {
      given[i] = "";
      var input = questions[i].querySelector(".listening-input");
      if (input) input.value = "";
    }
    finished = false;
    at = 0;
    output.innerHTML = "";
    root.classList.remove("listening--done");
    show(at);
  }

  /* --- wiring -------------------------------------------------------------- */

  questions.forEach(function (question, index) {
    var again = question.querySelector(".listening-say");
    if (again) {
      again.addEventListener("click", function () {
        speak(question.dataset.word);
      });
    }

    if (mode === "pick") {
      var options = question.querySelectorAll(".listening-option");
      Array.prototype.forEach.call(options, function (option) {
        option.addEventListener("click", function () {
          record(index, option.dataset.value);
        });
      });
      return;
    }

    var input = question.querySelector(".listening-input");
    var submit = question.querySelector(".listening-submit");
    function send() {
      record(index, input ? input.value : "");
    }
    if (submit) {
      /* The last one says so. "Neste ord" on the final word promises a word
       * that is not coming. */
      if (index === total - 1) submit.textContent = root.dataset.labelFinish;
      submit.addEventListener("click", send);
    }
    if (input) {
      input.addEventListener("keydown", function (event) {
        /* Enter moves on, because a keyboard is what this mode is done with and
         * reaching for a button between every word is the slowest part of it. */
        if (event.key === "Enter") {
          event.preventDefault();
          send();
        }
      });
    }
  });

  /* Hidden until the voice question is settled: showing the first word and then
   * discovering nothing can say it is worse than a moment of nothing. */
  for (var q = 0; q < total; q++) questions[q].hidden = true;

  withVoices(function (voices) {
    voice = pickVoice(voices, language);
    if (!voice) {
      /* No voice for this language, so there is no exercise. Said plainly, and
       * the questions stay hidden -- offering two spellings with nothing spoken
       * would be a coin toss dressed up as a lesson. */
      say(root.dataset.labelNovoice);
      root.classList.add("listening--mute");
      return;
    }
    show(at);
  });
})();
