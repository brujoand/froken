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
  function lightTo(cursor) {
    for (var i = 0; i < wordSpans.length; i++) {
      wordSpans[i].classList.toggle("lit", i < cursor);
    }
    if (bar) bar.style.width = Math.min(100, (cursor / totalWords) * 100) + "%";
    if (progress) progress.setAttribute("aria-valuenow", String(cursor));
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

    if (!checked) {
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
        checked = false;
        live = false;
        postUrl = baseUrl + "/tid";
        say(root.dataset.labelDenied);
        begin();
      }
    );
  }

  function stop() {
    running = false;
    clearInterval(ticker);
    leaveFocus();
    var seconds = (performance.now() - startedAt) / 1000;

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
})();
