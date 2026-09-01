/* The reading screen: focus mode, a live highlight, a replay and the badges.
 *
 * Written by hand and vendored like everything else on this site: no bundler,
 * no dependency, no third-party origin. Audio is captured, converted to the one
 * format the server accepts, posted to this site's own origin and dropped. It
 * is never stored here and never sent anywhere else.
 *
 * Personal bests and streaks live in this browser's localStorage and are never
 * sent to the server, which is what lets Pensum say it keeps no history of who
 * read what.
 */
(function () {
  "use strict";

  var SAMPLE_RATE = 16000;
  /* How often a slice of audio goes to the server while reading. Every slice
   * costs a transcription there, so this is a latency/CPU dial: lower feels
   * more alive and heats the machine faster. */
  var CHUNK_MS = 2000;
  var STORE_KEY = "pensum.reading.v1";

  var root = document.getElementById("reading");
  if (!root) return;

  var stage = document.getElementById("reading-stage");
  var toggle = document.getElementById("reading-toggle");
  var clock = document.getElementById("reading-clock");
  var bar = document.getElementById("reading-bar");
  var progress = document.getElementById("reading-progress");
  var output = document.getElementById("reading-result");
  var passage = document.getElementById("reading-passage");
  var wordSpans = passage ? Array.prototype.slice.call(passage.querySelectorAll(".w")) : [];

  var checked = root.dataset.checked === "true";
  var live = root.dataset.live === "true";
  var baseUrl = root.dataset.baseUrl;
  var postUrl = root.dataset.postUrl;
  var totalWords = parseInt(root.dataset.words, 10) || wordSpans.length;

  var deviceAllowed = root.dataset.device === "true";
  var speechLocale = root.dataset.speechLocale || "nb-NO";
  var Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  var engineElement = document.getElementById("reading-engine");
  var consentElement = document.getElementById("reading-consent");
  var consentBox = document.getElementById("reading-consent-box");
  var consentText = document.getElementById("reading-consent-text");
  var timedBox = document.getElementById("reading-timed-box");
  /* Whether the privacy opt-in has anything to say on this browser. It only
   * appears where recognition would leave the device. */
  var consentApplies = false;

  /* "device" | "server" | "timed". Decided once at load, and again if the pupil
   * ticks the consent box. */
  var engine = "timed";
  /* Set only when the browser has said it can recognise speech without the
   * audio leaving the machine. */
  var localRecognition = false;
  var recogniser = null;
  var heardWords = [];
  var cursor = 0;
  /* Correctness, per word, kept apart from progress on purpose. Progress is
   * "how far has the reader got", and it must keep up with speech or the
   * highlight is useless. Whether each word came out right is a slower and
   * less certain question -- the recogniser revises, and mishears. Deciding
   * both with one number is what made the highlight stall: a word the
   * recogniser got wrong stopped the cursor, and everything after it lagged.
   *
   * Indexed like `reference`. Absent means undecided. */
  var status = [];
  /* The passage as plain lowercase words, in the same order the server numbered
   * them. Read off the spans so the two cannot drift apart. */
  var reference = wordSpans.map(function (span) {
    return span.textContent.toLowerCase();
  });

  var running = false;
  var startedAt = 0;
  var ticker = null;
  var capture = null;
  var streamId = null;
  var queue = Promise.resolve();
  var replayTimers = [];

  /* --- small helpers ---------------------------------------------------- */

  function say(message) {
    output.innerHTML = "";
    var p = document.createElement("p");
    p.className = "note note--error";
    p.textContent = message;
    output.appendChild(p);
  }

  function fill(template, values) {
    return String(template).replace(/\{(\w+)\}/g, function (match, key) {
      return Object.prototype.hasOwnProperty.call(values, key) ? values[key] : match;
    });
  }

  function showClock() {
    var seconds = Math.floor((performance.now() - startedAt) / 1000);
    clock.textContent = Math.floor(seconds / 60) + ":" + String(seconds % 60).padStart(2, "0");
  }

  /* Light every word up to `cursor`, and nothing after it. Idempotent, so a
   * cursor that arrives late or twice cannot make the highlight flicker. */
  /* Keep the word being read on screen. In focus mode the passage is taller
   * than the display, and nothing was scrolling it -- so a child could see the
   * text before starting and then read the rest of it off the bottom of the
   * screen.
   *
   * A band rather than a line: scrolling to centre every single word would slide
   * the passage continuously under someone trying to follow it. It moves only
   * when the current word has left the comfortable middle. */
  function keepInView(index) {
    if (!running || !stage) return;
    var span = wordSpans[Math.min(index, wordSpans.length - 1)];
    if (!span) return;

    var frame = stage.getBoundingClientRect();
    var box = span.getBoundingClientRect();
    var high = frame.top + frame.height * 0.25;
    var low = frame.top + frame.height * 0.7;
    if (box.top >= high && box.bottom <= low) return;

    stage.scrollTop += box.top - (frame.top + frame.height * 0.35);
  }

  function lightTo(cursor) {
    for (var i = 0; i < wordSpans.length; i++) {
      var passed = i < cursor;
      var wrong = status[i] === "misread";
      wordSpans[i].classList.toggle("lit", passed && !wrong);
      wordSpans[i].classList.toggle("missed", passed && wrong);
    }
    if (bar) bar.style.width = Math.min(100, (cursor / totalWords) * 100) + "%";
    if (progress) progress.setAttribute("aria-valuenow", String(cursor));
    keepInView(cursor);
  }

  function clearWords() {
    for (var i = 0; i < wordSpans.length; i++) {
      wordSpans[i].classList.remove("lit", "missed", "current");
    }
    if (bar) bar.style.width = "0%";
  }

  /* --- audio ------------------------------------------------------------ */

  /* Float32 at whatever rate the microphone gave us -> 16 kHz 16-bit PCM.
   * Linear interpolation is crude, but the recogniser's acoustic model cares
   * about a band far below Nyquist here, and doing it in the page is what keeps
   * ffmpeg out of the container. */
  function toPcm(chunks, inputRate) {
    var length = 0;
    var i;
    for (i = 0; i < chunks.length; i++) length += chunks[i].length;

    var joined = new Float32Array(length);
    var offset = 0;
    for (i = 0; i < chunks.length; i++) {
      joined.set(chunks[i], offset);
      offset += chunks[i].length;
    }

    var ratio = inputRate / SAMPLE_RATE;
    var outLength = Math.floor(joined.length / ratio);
    var samples = new Int16Array(outLength);
    for (i = 0; i < outLength; i++) {
      var position = i * ratio;
      var low = Math.floor(position);
      var high = Math.min(low + 1, joined.length - 1);
      var value = joined[low] + (joined[high] - joined[low]) * (position - low);
      value = Math.max(-1, Math.min(1, value));
      samples[i] = value < 0 ? value * 0x8000 : value * 0x7fff;
    }
    return samples;
  }

  function toWav(samples) {
    var buffer = new ArrayBuffer(44 + samples.length * 2);
    var view = new DataView(buffer);
    function text(at, string) {
      for (var n = 0; n < string.length; n++) view.setUint8(at + n, string.charCodeAt(n));
    }
    text(0, "RIFF");
    view.setUint32(4, 36 + samples.length * 2, true);
    text(8, "WAVEfmt ");
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true); /* PCM */
    view.setUint16(22, 1, true); /* mono */
    view.setUint32(24, SAMPLE_RATE, true);
    view.setUint32(28, SAMPLE_RATE * 2, true);
    view.setUint16(32, 2, true);
    view.setUint16(34, 16, true);
    text(36, "data");
    view.setUint32(40, samples.length * 2, true);
    new Int16Array(buffer, 44).set(samples);
    return new Blob([buffer], { type: "audio/wav" });
  }

  function startCapture() {
    return navigator.mediaDevices.getUserMedia({ audio: true }).then(function (stream) {
      var context = new (window.AudioContext || window.webkitAudioContext)();
      var source = context.createMediaStreamSource(stream);
      /* ScriptProcessorNode is deprecated in favour of AudioWorklet, which
       * needs a second file served as a module. For a few minutes of speech the
       * old node is universally supported and does the job; if a browser drops
       * it, the catch below falls back to timing. */
      var node = context.createScriptProcessor(4096, 1, 1);
      /* `pending` is what has not been sent yet; `all` is the whole reading,
       * kept so a failed stream can still be posted in one piece at the end. */
      var pending = [];
      var all = [];

      node.onaudioprocess = function (event) {
        var copy = new Float32Array(event.inputBuffer.getChannelData(0));
        pending.push(copy);
        all.push(copy);
      };
      source.connect(node);
      node.connect(context.destination);

      return {
        rate: context.sampleRate,
        takePending: function () {
          var taken = pending;
          pending = [];
          return taken;
        },
        stop: function () {
          node.disconnect();
          source.disconnect();
          stream.getTracks().forEach(function (track) {
            track.stop();
          });
          var rate = context.sampleRate;
          context.close();
          return { pending: pending, all: all, rate: rate };
        },
      };
    });
  }

  /* --- talking to the server -------------------------------------------- */

  function sendChunk(samples) {
    if (!streamId || !samples.length) return Promise.resolve();
    return fetch(baseUrl + "/strom/" + streamId, {
      method: "POST",
      body: samples.buffer,
      headers: { "Content-Type": "application/octet-stream" },
    })
      .then(function (response) {
        if (!response.ok) throw new Error(String(response.status));
        return response.json();
      })
      .then(function (data) {
        if (running) lightTo(data.cursor);
      })
      .catch(function () {
        /* The live highlight is a nicety. Losing it must not lose the reading,
         * so the stream is abandoned and the whole recording goes in one piece
         * at the end. */
        streamId = null;
      });
  }

  function pump() {
    if (!capture || !streamId) return;
    var taken = capture.takePending();
    if (!taken.length) return;
    var samples = toPcm(taken, capture.rate);
    queue = queue.then(function () {
      return sendChunk(samples);
    });
  }

  function render(html) {
    output.innerHTML = html;
    wireResult();
  }

  function post(url, body, headers) {
    toggle.disabled = true;
    toggle.textContent = root.dataset.labelWorking;
    return fetch(url, { method: "POST", body: body, headers: headers })
      .then(function (response) {
        if (!response.ok) throw new Error(String(response.status));
        return response.text();
      })
      .then(render)
      .catch(function () {
        say(root.dataset.labelFailed);
      })
      .finally(function () {
        toggle.disabled = false;
        toggle.textContent = root.dataset.labelStart;
      });
  }

  /* --- what this browser remembers -------------------------------------- */

  function load() {
    try {
      return JSON.parse(localStorage.getItem(STORE_KEY) || "{}") || {};
    } catch (error) {
      return {};
    }
  }

  function save(state) {
    try {
      localStorage.setItem(STORE_KEY, JSON.stringify(state));
    } catch (error) {
      /* Private window, or storage full. The reading still worked. */
    }
  }

  function today() {
    var now = new Date();
    return now.getFullYear() + "-" + (now.getMonth() + 1) + "-" + now.getDate();
  }

  function yesterday() {
    var then = new Date();
    then.setDate(then.getDate() - 1);
    return then.getFullYear() + "-" + (then.getMonth() + 1) + "-" + then.getDate();
  }

  /* Personal best and streak, computed here and never sent anywhere. */
  function recordAndDescribe(card, wpm) {
    var state = load();
    state.texts = state.texts || {};
    state.streak = state.streak || { days: 0, last: null };

    var textId = card.dataset.textId;
    var entry = state.texts[textId] || { best: null, runs: 0 };
    var previous = entry.best;

    entry.runs += 1;
    if (wpm && (entry.best === null || wpm > entry.best)) entry.best = wpm;
    state.texts[textId] = entry;

    if (state.streak.last === yesterday()) state.streak.days += 1;
    else if (state.streak.last !== today()) state.streak.days = 1;
    state.streak.last = today();
    save(state);

    var lines = [];
    if (!wpm) return lines;
    if (previous === null) lines.push(card.dataset.labelFirst);
    else if (wpm > previous)
      lines.push(fill(card.dataset.labelBest, { delta: wpm - previous, best: wpm }));
    else lines.push(fill(card.dataset.labelPreviousBest, { best: previous }));
    if (state.streak.days > 1) lines.push(fill(card.dataset.labelStreak, { days: state.streak.days }));
    return lines;
  }

  /* --- the replay ------------------------------------------------------- */

  function stopReplay() {
    replayTimers.forEach(clearTimeout);
    replayTimers = [];
  }

  function playBack(timeline) {
    stopReplay();
    clearWords();
    timeline.forEach(function (entry) {
      replayTimers.push(
        setTimeout(function () {
          var span = wordSpans[entry.i];
          if (!span) return;
          span.classList.add(entry.ok ? "lit" : "missed");
          if (bar) bar.style.width = Math.min(100, ((entry.i + 1) / totalWords) * 100) + "%";
        }, entry.at * 1000)
      );
    });
  }

  function wireResult() {
    var card = document.getElementById("reading-result-card");
    if (!card) return;

    var wpm = parseInt(card.dataset.wpm, 10);
    var personal = document.getElementById("reading-personal");
    var lines = recordAndDescribe(card, isNaN(wpm) ? null : wpm);
    if (personal && lines.length) {
      personal.textContent = lines.join(" ");
      personal.hidden = false;
    }

    var data = document.getElementById("reading-timeline");
    var timeline = [];
    try {
      timeline = data ? JSON.parse(data.textContent) : [];
    } catch (error) {
      timeline = [];
    }

    var replayButton = document.getElementById("reading-replay");
    if (replayButton && timeline.length) {
      replayButton.addEventListener("click", function () {
        playBack(timeline);
      });
      /* Play it once unprompted: the point of the screen is watching the words
       * come back, and a child should not have to find a button for it. */
      playBack(timeline);
    }

    var again = document.getElementById("reading-again");
    if (again) {
      again.addEventListener("click", function () {
        stopReplay();
        clearWords();
        output.innerHTML = "";
        enterFocus();
        start();
      });
    }
  }

  /* --- focus mode ------------------------------------------------------- */

  function enterFocus() {
    document.body.classList.add("reading-focus");
    /* Best effort. Fullscreen is refused outside a user gesture and on some
     * mobile browsers entirely, and the class above already does the work. */
    if (stage && stage.requestFullscreen) {
      stage.requestFullscreen().catch(function () {});
    }
  }

  function leaveFocus() {
    document.body.classList.remove("reading-focus");
    if (document.fullscreenElement && document.exitFullscreen) {
      document.exitFullscreen().catch(function () {});
    }
  }

  document.addEventListener("fullscreenchange", function () {
    /* Escape leaves fullscreen without telling us. Keep the class in step, or
     * the page is left in focus mode inside a normal window. */
    if (!document.fullscreenElement && !running) leaveFocus();
  });

  /* --- the device's own recogniser --------------------------------------- */

  /* The same tokeniser the passage went through on the server, near enough: the
   * two only have to agree about what counts as a word. */
  function tokenize(text) {
    var matches = String(text).toLowerCase().match(/\p{L}+(?:['’-]\p{L}+)*/gu);
    return matches || [];
  }

  /* A rough stand-in for the server's similarity test. It does not have to
   * agree exactly: this only moves the live highlight, and the score is
   * recomputed server-side from the transcript when the reading ends. */
  function similarity(a, b) {
    var rows = a.length;
    var columns = b.length;
    var previous = [];
    var i;
    var j;
    for (j = 0; j <= columns; j++) previous[j] = j;
    for (i = 1; i <= rows; i++) {
      var current = [i];
      for (j = 1; j <= columns; j++) {
        var cost = a.charAt(i - 1) === b.charAt(j - 1) ? 0 : 1;
        current[j] = Math.min(current[j - 1] + 1, previous[j] + 1, previous[j - 1] + cost);
      }
      previous = current;
    }
    return 1 - previous[columns] / Math.max(rows, columns, 1);
  }

  /* The same rule as fluency.close_enough server-side, and for the same reason:
   * Norwegian offers endless legitimate variation in endings (boka/boken,
   * trappa/trappen), and counting it as errors marks down exactly the children
   * who are reading fine. A flat similarity threshold cannot express this --
   * "trappa" and "trappen" score 0.71 here, below any threshold that still
   * rejects real substitutions -- so a shared stem is what carries it. */
  var MIN_SHARED_PREFIX = 3;
  var PREFIX_SHARE = 0.6;
  var MAX_LENGTH_DIFFERENCE = 3;
  var CLOSE_ENOUGH = 0.85;
  /* Short words are held to an exact match: that is where a real substitution
   * hides, and there is not enough word left to tell the two apart. */
  var FUZZY_MIN_LENGTH = 4;

  /* Reading "trappen" for "trappa" is pronunciation, not the wrong word.
   * Reading "hest" for "hus" is. The accepted cost of the stem rule is that
   * "store" and "storm" pass as the same word; the server makes the same trade
   * deliberately, and the two must agree or the highlight contradicts the
   * score. */
  function closeEnough(printed, said) {
    if (printed === said) return true;
    var shortest = Math.min(printed.length, said.length);
    if (shortest < FUZZY_MIN_LENGTH) return false;
    if (Math.abs(printed.length - said.length) > MAX_LENGTH_DIFFERENCE) return false;

    var shared = 0;
    while (shared < shortest && printed.charAt(shared) === said.charAt(shared)) shared++;
    if (shared >= MIN_SHARED_PREFIX && shared >= PREFIX_SHARE * shortest) return true;

    /* No shared stem, so this is only pronunciation if the two are very nearly
     * the same throughout. */
    return similarity(printed, said) >= CLOSE_ENOUGH;
  }

  /* Passage words and transcript words considered at once. The table below is
   * quadratic, and it is rebuilt on every interim result -- several times a
   * second, on a school iPad. Aligning a whole 250-word passage against a whole
   * transcript measured 55ms a go on a developer machine, which is jank on the
   * one screen that has taken over the display. A window costs a twentieth of
   * that and gives up nothing that matters: a reader is somewhere specific, and
   * the far end of the passage is not evidence about where. */
  var WINDOW_WORDS = 80;
  var WINDOW_HEARD = 160;
  /* How much of the recent alignment stays open to revision. Everything before
   * it is settled and never reconsidered, which is what keeps the window from
   * growing as the reading goes on. Being able to revise the last twenty words
   * is what tells a skipped word from one read wrong five times; being able to
   * revise the first twenty is worth nothing. */
  var SETTLED = 20;

  /* The oldest passage word and transcript word still open to revision. They
   * advance together, so the two always mean the same point in the reading. */
  var anchorWord = 0;
  var anchorHeard = 0;

  /* closeEnough runs once per cell of the table, so the same pair of words is
   * compared many times over a reading. Comparing them once is what makes
   * rebuilding the alignment on every update affordable. */
  var comparisons = {};

  function same(printed, said) {
    var key = printed + "\u0000" + said;
    var known = comparisons[key];
    if (known === undefined) {
      known = closeEnough(printed, said);
      comparisons[key] = known;
    }
    return known;
  }

  /* Longest common subsequence of a stretch of the passage and a stretch of the
   * transcript, under `same`. Returns [passageIndex, heardIndex] pairs, both
   * absolute.
   *
   * This replaces matching each heard word as it arrives. Incremental matching
   * decides once, in order, and cannot revise -- so it had no way to tell a
   * word that was skipped from a word that was read wrong five times running,
   * and no way to notice that a decision it made ten words ago was what put
   * everything since out of step. Both are ordinary things a child does, and
   * both used to derail the rest of the passage.
   *
   * Aligned together they are just two shapes in one table: a skipped word is a
   * passage word with nothing opposite it, a word read wrong five times is five
   * transcript words with nothing opposite them, and a word read twice is one
   * of them ignored. None of them costs anything after itself.
   *
   * The server scores the finished reading with this same algorithm
   * (fluency._align). Two different answers to "did they read that word" is
   * what makes a highlight contradict the result the child is shown. */
  function align(tokens, wordFrom, wordTo, heardFrom, heardTo) {
    var rows = wordTo - wordFrom;
    var columns = heardTo - heardFrom;
    if (rows <= 0 || columns <= 0) return [];

    var width = columns + 1;
    var table = new Int32Array((rows + 1) * width);
    for (var i = rows - 1; i >= 0; i--) {
      for (var j = columns - 1; j >= 0; j--) {
        table[i * width + j] = same(reference[wordFrom + i], tokens[heardFrom + j])
          ? table[(i + 1) * width + j + 1] + 1
          : Math.max(table[(i + 1) * width + j], table[i * width + j + 1]);
      }
    }

    var pairs = [];
    var r = 0;
    var h = 0;
    while (r < rows && h < columns) {
      if (same(reference[wordFrom + r], tokens[heardFrom + h])) {
        pairs.push([wordFrom + r, heardFrom + h]);
        r++;
        h++;
      } else if (table[(r + 1) * width + h] >= table[r * width + h + 1]) {
        r++;
      } else {
        h++;
      }
    }
    return pairs;
  }

  /* Matched words needed before the alignment is believed about *where* the
   * reader is. One word matching is a coincidence -- every passage contains
   * "og", "the", "den" -- and believing a single match would let one heard word
   * place the highlight anywhere in the window. */
  var MIN_ANCHOR = 2;

  function advanceCursor(tokens) {
    var wordTo = Math.min(reference.length, anchorWord + WINDOW_WORDS);
    var heardFrom = anchorHeard;
    /* A transcript that grows without ever matching -- a recogniser stuck in a
     * loop, a microphone left on in a noisy room. Drop the oldest rather than
     * let the table grow without limit. */
    if (tokens.length - heardFrom > WINDOW_HEARD) heardFrom = tokens.length - WINDOW_HEARD;

    var pairs = align(tokens, anchorWord, wordTo, heardFrom, tokens.length);

    if (pairs.length < MIN_ANCHOR) {
      /* Nothing placed. Everything heard since the anchor is worth a word of
       * progress and no verdict at all: calling words wrong on this evidence
       * would be asserting something we do not know. */
      cursor = Math.min(reference.length, anchorWord + (tokens.length - heardFrom));
      lightTo(cursor);
      return;
    }

    var lastWord = pairs[pairs.length - 1][0];
    var lastHeard = pairs[pairs.length - 1][1];

    for (var p = 0; p < pairs.length; p++) status[pairs[p][0]] = "read";

    /* Everything up to the last word we are sure of, that we are not sure of,
     * was passed without being heard. Beyond it we genuinely do not know. */
    for (var i = anchorWord; i < lastWord; i++) {
      if (status[i] !== "read") status[i] = "misread";
    }

    /* Words heard after the last one we could place are worth one position
     * each. This is what keeps the highlight moving while the reader is
     * somewhere the recogniser has not caught up with. */
    cursor = Math.min(reference.length, lastWord + 1 + (tokens.length - 1 - lastHeard));

    /* Settle everything except the tail, so the next table starts from here
     * rather than from the beginning of the passage. */
    if (pairs.length > SETTLED) {
      var settled = pairs[pairs.length - SETTLED];
      anchorWord = settled[0];
      anchorHeard = settled[1];
    }

    lightTo(cursor);
  }

  function collect(event) {
    var text = "";
    for (var i = 0; i < event.results.length; i++) text += event.results[i][0].transcript + " ";
    var tokens = tokenize(text);
    var at = Math.round((performance.now() - startedAt)) / 1000;

    /* Interim results are rewritten as the recogniser changes its mind, so a
     * word keeps the time it was first seen and has its text updated in place.
     * These times are "when the page first heard it", not "when it was said" --
     * good enough to replay, and labelled as approximate nowhere because the
     * difference is a fraction of a second. */
    for (var n = 0; n < tokens.length; n++) {
      if (n < heardWords.length) heardWords[n].t = tokens[n];
      else heardWords.push({ t: tokens[n], at: at });
    }
    heardWords.length = tokens.length;
    advanceCursor(tokens);
  }

  /* The reading carries on as a timed one. Called when checking becomes
   * impossible -- microphone refused, recogniser refused -- and never to hide
   * a failure: every caller passes something to say. */
  function fallbackToTimed(message) {
    checked = false;
    live = false;
    engine = "timed";
    postUrl = baseUrl + "/tid";
    say(message);
  }

  /* Which recogniser failures end the attempt, and what to say about each.
   * Anything absent here is treated as passing weather: `no-speech` is a child
   * pausing to think, `aborted` is the recogniser being stopped on purpose. */
  function fatalMessage(code) {
    if (code === "not-allowed" || code === "service-not-allowed") {
      /* Two different refusals with one remedy the child cannot guess at. On
       * iOS this is what a device with dictation switched off reports, and it
       * is indistinguishable from a denied permission prompt from in here. */
      return root.dataset.labelSpeechBlocked;
    }
    if (code === "audio-capture") return root.dataset.labelDenied;
    if (code === "language-not-supported") return root.dataset.labelSpeechUnsupported;
    if (code === "network") return root.dataset.labelFailed;
    return null;
  }

  function startRecognition() {
    var stopped = false;

    recogniser = new Recognition();
    recogniser.lang = speechLocale;
    recogniser.continuous = true;
    recogniser.interimResults = true;
    /* Only set when the browser said it can honour it. Setting it blindly
     * throws on the browsers that have never heard of it. */
    if (engine === "device" && localRecognition) {
      try {
        recogniser.processLocally = true;
      } catch (error) {
        /* Nothing to do: the probe already told us it was available. */
      }
    }
    recogniser.onresult = collect;
    recogniser.onend = function () {
      /* Recognisers stop on their own after a pause. A child thinking about a
       * long word is a pause. A refusal is not: restarting through one spins
       * as fast as the browser will answer, for as long as the child reads. */
      if (running && !stopped) {
        try {
          recogniser.start();
        } catch (error) {
          /* Already restarting. */
        }
      }
    };
    recogniser.onerror = function (event) {
      var message = fatalMessage(event && event.error);
      if (!message) return;
      stopped = true;
      stopRecognition();
      fallbackToTimed(message);
    };
    try {
      recogniser.start();
    } catch (error) {
      stopped = true;
      fallbackToTimed(root.dataset.labelFailed);
    }
  }

  function stopRecognition() {
    if (!recogniser) return;
    var used = recogniser;
    recogniser = null;
    used.onend = null;
    try {
      used.stop();
    } catch (error) {
      /* Already stopped. */
    }
  }

  /* Can this browser recognise speech without sending the audio anywhere? Only
   * some can answer, and only "available" is a yes -- a model that merely could
   * be downloaded is not one that is going to run locally today. */
  function probeLocal() {
    if (!Recognition || typeof Recognition.available !== "function") {
      return Promise.resolve(false);
    }
    try {
      return Promise.resolve(
        Recognition.available({ langs: [speechLocale], processLocally: true })
      )
        .then(function (state) {
          return state === "available";
        })
        .catch(function () {
          return false;
        });
    } catch (error) {
      return Promise.resolve(false);
    }
  }

  function announce(element, message) {
    element.textContent = message;
    element.hidden = false;
  }

  /* Which recogniser will listen, decided once and said out loud before the
   * microphone is touched. On-device recognition is used without asking --
   * it is strictly more private than posting the audio to Pensum, which is
   * what the server path does. Anything else is opt-in and names whose
   * servers are involved. */
  /* Who listens, decided from what the browser can do and what the reader has
   * asked for -- recomputed whenever either changes, so there is one answer and
   * one place it comes from. */
  function settleEngine() {
    if (timedBox && timedBox.checked) {
      engine = "timed";
    } else if (deviceAllowed && Recognition && (localRecognition || consentBox.checked)) {
      engine = "device";
    } else {
      engine = checked ? "server" : "timed";
    }

    /* Both notices are about recognition, so neither has anything to say to a
     * reader who has asked not to be recognised. */
    var declined = Boolean(timedBox && timedBox.checked);
    consentElement.hidden = !consentApplies || declined;
    engineElement.hidden = declined || !engineElement.textContent;
  }

  function chooseEngine() {
    if (timedBox) timedBox.addEventListener("change", settleEngine);

    if (!deviceAllowed || !Recognition) {
      settleEngine();
      return Promise.resolve();
    }
    return probeLocal().then(function (isLocal) {
      localRecognition = isLocal;
      if (isLocal) {
        announce(engineElement, root.dataset.labelDeviceLocal);
      } else {
        consentApplies = true;
        consentText.textContent = root.dataset.labelDeviceCloud;
        consentBox.addEventListener("change", settleEngine);
      }
      settleEngine();
    });
  }

  /* --- the reading itself ----------------------------------------------- */

  function begin() {
    running = true;
    startedAt = performance.now();
    toggle.textContent = root.dataset.labelStop;
    showClock();
    ticker = setInterval(function () {
      showClock();
      if (live) pump();
    }, 1000);
  }

  function openStream() {
    return fetch(baseUrl + "/strom", { method: "POST" })
      .then(function (response) {
        if (!response.ok) throw new Error(String(response.status));
        return response.json();
      })
      .then(function (data) {
        streamId = data.stream;
      })
      .catch(function () {
        /* No stream: no live highlight, but the reading is still checked in one
         * piece at the end. */
        streamId = null;
      });
  }

  function start() {
    stopReplay();
    clearWords();
    output.innerHTML = "";
    enterFocus();
    heardWords = [];
    cursor = 0;
    status = [];
    anchorWord = 0;
    anchorHeard = 0;
    comparisons = {};

    if (engine === "device") {
      /* No getUserMedia here: the recogniser asks for the microphone itself,
       * and on the browsers that can do this locally the audio never reaches
       * this script at all. */
      begin();
      startRecognition();
      return;
    }

    if (engine !== "server") {
      begin();
      return;
    }

    startCapture().then(
      function (session) {
        capture = session;
        if (live) openStream().then(begin);
        else begin();
      },
      function () {
        /* Denied, or no microphone. The exercise still works as a timed one, so
         * say what changed rather than refusing to start. */
        fallbackToTimed(root.dataset.labelDenied);
        begin();
      }
    );
  }

  function stop() {
    running = false;
    clearInterval(ticker);
    leaveFocus();
    var seconds = (performance.now() - startedAt) / 1000;

    if (engine === "device") {
      stopRecognition();
      /* A transcript, not audio. Nothing was recorded here and there is nothing
       * to discard. */
      post(
        baseUrl + "/enhet",
        JSON.stringify({ seconds: Number(seconds.toFixed(2)), words: heardWords }),
        { "Content-Type": "application/json" }
      );
      return;
    }

    if (!capture) {
      var form = new URLSearchParams();
      form.set("seconds", seconds.toFixed(2));
      post(postUrl, form, { "Content-Type": "application/x-www-form-urlencoded" });
      return;
    }

    var final = capture.stop();
    capture = null;

    if (streamId) {
      var tail = toPcm(final.pending, final.rate);
      queue = queue
        .then(function () {
          return sendChunk(tail);
        })
        .then(function () {
          if (!streamId) {
            /* The tail failed and took the stream with it. Fall back to the
             * one-shot path rather than losing the reading. */
            return post(baseUrl + "/opptak", toWav(toPcm(final.all, final.rate)), {
              "Content-Type": "audio/wav",
            });
          }
          return post(baseUrl + "/strom/" + streamId + "/ferdig", null, {});
        })
        .finally(function () {
          streamId = null;
        });
      return;
    }

    post(baseUrl + "/opptak", toWav(toPcm(final.all, final.rate)), {
      "Content-Type": "audio/wav",
    });
  }

  toggle.addEventListener("click", function () {
    if (running) stop();
    else start();
  });

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && running) stop();
  });

  chooseEngine();
})();
