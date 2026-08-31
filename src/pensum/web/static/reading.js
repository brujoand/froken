/* Reading aloud: a clock, and -- when the deployment has speech models -- a
 * recording that is posted for checking.
 *
 * Written by hand and vendored like everything else on this site: no bundler,
 * no dependency, no third-party origin. The audio is captured, converted to the
 * one format the server accepts, posted, and dropped. It is never stored, and
 * it never reaches anything but this site's own origin.
 */
(function () {
  "use strict";

  var SAMPLE_RATE = 16000;

  var root = document.getElementById("reading");
  if (!root) return;

  var toggle = document.getElementById("reading-toggle");
  var clock = document.getElementById("reading-clock");
  var output = document.getElementById("reading-result");

  var checked = root.dataset.checked === "true";
  var postUrl = root.dataset.postUrl;

  var running = false;
  var startedAt = 0;
  var ticker = null;
  var capture = null;

  function say(message) {
    output.innerHTML = "";
    var p = document.createElement("p");
    p.className = "note note--error";
    p.textContent = message;
    output.appendChild(p);
  }

  function showClock() {
    var seconds = Math.floor((performance.now() - startedAt) / 1000);
    clock.textContent = Math.floor(seconds / 60) + ":" + String(seconds % 60).padStart(2, "0");
  }

  /* Float32 at whatever rate the microphone gave us -> 16 kHz 16-bit PCM.
   * Linear interpolation is crude, but the recogniser's acoustic model cares
   * about a band far below Nyquist here, and doing it in the page is what keeps
   * ffmpeg out of the container. */
  function toWav(chunks, inputRate) {
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
       * needs a second file served as a module. For a few seconds of speech the
       * old node is universally supported and does the job; if a browser drops
       * it, the catch below falls back to timing. */
      var node = context.createScriptProcessor(4096, 1, 1);
      var chunks = [];

      node.onaudioprocess = function (event) {
        chunks.push(new Float32Array(event.inputBuffer.getChannelData(0)));
      };
      source.connect(node);
      node.connect(context.destination);

      return {
        stop: function () {
          node.disconnect();
          source.disconnect();
          stream.getTracks().forEach(function (track) {
            track.stop();
          });
          var wav = toWav(chunks, context.sampleRate);
          context.close();
          return wav;
        },
      };
    });
  }

  function send(body, headers) {
    toggle.disabled = true;
    toggle.textContent = root.dataset.labelWorking;
    fetch(postUrl, { method: "POST", body: body, headers: headers })
      .then(function (response) {
        if (!response.ok) throw new Error(String(response.status));
        return response.text();
      })
      .then(function (html) {
        output.innerHTML = html;
      })
      .catch(function () {
        say(root.dataset.labelFailed);
      })
      .finally(function () {
        toggle.disabled = false;
        toggle.textContent = root.dataset.labelStart;
      });
  }

  function start() {
    output.innerHTML = "";
    var begin = function () {
      running = true;
      startedAt = performance.now();
      toggle.textContent = root.dataset.labelStop;
      showClock();
      ticker = setInterval(showClock, 1000);
    };

    if (!checked) {
      begin();
      return;
    }
    startCapture().then(
      function (session) {
        capture = session;
        begin();
      },
      function () {
        /* Denied, or no microphone. The exercise still works as a timed one, so
         * say what changed rather than refusing to start. */
        checked = false;
        postUrl = postUrl.replace(/\/opptak$/, "/tid");
        say(root.dataset.labelDenied);
        begin();
      }
    );
  }

  function stop() {
    running = false;
    clearInterval(ticker);
    var seconds = (performance.now() - startedAt) / 1000;

    if (capture) {
      var wav = capture.stop();
      capture = null;
      send(wav, { "Content-Type": "audio/wav" });
      return;
    }
    var form = new URLSearchParams();
    form.set("seconds", seconds.toFixed(2));
    send(form, { "Content-Type": "application/x-www-form-urlencoded" });
  }

  toggle.addEventListener("click", function () {
    if (running) stop();
    else start();
  });
})();
