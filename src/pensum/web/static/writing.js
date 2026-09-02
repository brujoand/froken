/* The writing screen: a finger, the ink it leaves, the sparks it throws and a
 * meter that fills.
 *
 * Written by hand and vendored like everything else on this site: no bundler,
 * no dependency, no third-party origin.
 *
 * The page draws and measures the tracing as it happens, and the server marks
 * it once at the end. Those two numbers are not the same arithmetic and are not
 * meant to be: this one exists so a child can see that they are on the line
 * while their finger is still on it, and the one that comes back is the one the
 * result page shows. It is the same split the reading screen makes between the
 * words it lights up live and the score it prints afterwards.
 *
 * Nothing is stored. The points go to this site's own origin once and are gone.
 */
(function () {
  "use strict";

  /* How far off the guide a finger may be and still count as on it, in the
   * glyph's own units. The server marks with the same figure; a page that was
   * kinder than the server would sparkle its way to a disappointing result. */
  var TOLERANCE = 12;

  /* Sampling: points closer together than this are dropped. A touchscreen fires
   * far faster than a finger moves, and unsampled points make a slow tracing
   * look like a longer one. */
  var MIN_STEP = 1.5;

  /* Ceilings that match what the server will accept, so a very long tracing is
   * trimmed here rather than rejected there. */
  var MAX_POINTS = 500;
  var MAX_STROKES = 8;

  /* Sparks per second while the finger is on the line, and how long one lives.
   * Enough to feel like something is happening, few enough that a cheap tablet
   * keeps up. */
  var SPARK_EVERY_MS = 45;
  var SPARK_LIFE_MS = 700;
  var SPARK_SPREAD = 9;

  /* How long the demonstration takes to draw one stroke. Slow enough to follow
   * with your eyes, and it is the only animation on the page that a child is
   * asked to watch rather than cause. */
  var SHOW_MS_PER_STROKE = 900;

  var root = document.getElementById("writing");
  if (!root) return;

  var sheet = document.getElementById("writing-sheet");
  var meter = document.getElementById("writing-meter");
  var meterFill = document.getElementById("writing-meter-fill");
  var position = document.getElementById("writing-position");
  var hint = document.getElementById("writing-hint");
  var output = document.getElementById("writing-result");
  var showButton = document.getElementById("writing-show");
  var clearButton = document.getElementById("writing-clear");
  var nextButton = document.getElementById("writing-next");

  var cards = Array.prototype.slice.call(sheet.querySelectorAll(".writing-card"));
  var total = cards.length;
  var postUrl = root.dataset.postUrl;
  var reduceMotion =
    window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* One entry per letter: the strokes drawn on it, as arrays of [x, y] in the
   * glyph's own coordinates. This is exactly what gets posted. */
  var traced = [];
  for (var i = 0; i < total; i++) traced.push([]);

  var at = 0;
  var startedAt = null;
  var drawing = null;
  var lastSparkAt = 0;
  var finished = false;

  /* --- geometry ---------------------------------------------------------- */

  /* Sampled points along one guide stroke, cached per path element. The browser
   * measures its own paths, so the page and the letterform data never disagree
   * about where the line is. */
  function guidePoints(path) {
    if (path.__points) return path.__points;
    var length = path.getTotalLength();
    var step = 2;
    var points = [];
    for (var d = 0; d <= length; d += step) {
      var point = path.getPointAtLength(d);
      points.push([point.x, point.y]);
    }
    points.push([path.getPointAtLength(length).x, path.getPointAtLength(length).y]);
    path.__points = points;
    return points;
  }

  function distance(a, b) {
    var dx = a[0] - b[0];
    var dy = a[1] - b[1];
    return Math.sqrt(dx * dx + dy * dy);
  }

  /* How near a point is to the nearest sampled point of a guide stroke.
   *
   * Point-to-point rather than point-to-segment, which the server uses: the
   * guide is sampled every two units, so the two answers differ by less than a
   * unit and this one is the arithmetic a phone can afford on every pointer
   * event. */
  function nearest(point, points) {
    var best = Infinity;
    for (var i = 0; i < points.length; i++) {
      var gap = distance(point, points[i]);
      if (gap < best) best = gap;
    }
    return best;
  }

  /* The share of a guide stroke that has ink within tolerance of it. The meter
   * is the mean of this over the letter's strokes -- an approximation of
   * coverage, which is the largest part of the server's mark. */
  function covered(points, strokes) {
    if (!strokes.length) return 0;
    var hit = 0;
    for (var i = 0; i < points.length; i++) {
      for (var s = 0; s < strokes.length; s++) {
        if (nearest(points[i], strokes[s]) <= TOLERANCE) {
          hit++;
          break;
        }
      }
    }
    return hit / points.length;
  }

  function progressFor(index) {
    var paths = guidePathsOf(cards[index]);
    var strokes = traced[index];
    if (!paths.length) return 0;
    var sum = 0;
    for (var i = 0; i < paths.length; i++) sum += covered(guidePoints(paths[i]), strokes);
    return sum / paths.length;
  }

  function guidePathsOf(card) {
    return Array.prototype.slice.call(card.querySelectorAll(".writing-guide path"));
  }

  /* --- the page ---------------------------------------------------------- */

  function svgOf(card) {
    return card.querySelector("svg");
  }

  /* Where a pointer is, in the glyph's coordinates rather than the screen's.
   * getScreenCTM is what makes the tracing independent of how big the letter is
   * drawn, which is the whole reason the letterforms are authored on a grid. */
  function pointIn(svg, event) {
    var matrix = svg.getScreenCTM();
    if (!matrix) return null;
    var point = svg.createSVGPoint();
    point.x = event.clientX;
    point.y = event.clientY;
    var local = point.matrixTransform(matrix.inverse());
    return [local.x, local.y];
  }

  function show(index) {
    for (var i = 0; i < cards.length; i++) {
      cards[i].classList.toggle("writing-card--active", i === index);
    }
    position.textContent = (root.dataset.labelPosition || "%1 / %2")
      .replace("%1", String(index + 1))
      .replace("%2", String(total));
    nextButton.textContent =
      index === total - 1 ? root.dataset.labelFinish : root.dataset.labelNext;
    paint();
  }

  function paint() {
    var share = progressFor(at);
    meterFill.style.width = Math.round(share * 100) + "%";
    meter.setAttribute("aria-valuenow", String(Math.round(share * 100)));
    meter.classList.toggle("writing-meter--full", share >= 0.9);
  }

  function inkLayer(card) {
    return card.querySelector(".writing-ink");
  }

  function redrawInk(index) {
    var layer = inkLayer(cards[index]);
    while (layer.firstChild) layer.removeChild(layer.firstChild);
    for (var s = 0; s < traced[index].length; s++) {
      layer.appendChild(inkPath(traced[index][s]));
    }
  }

  function inkPath(points) {
    var path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", pathData(points));
    return path;
  }

  function pathData(points) {
    var parts = [];
    for (var i = 0; i < points.length; i++) {
      parts.push((i === 0 ? "M" : "L") + points[i][0].toFixed(1) + " " + points[i][1].toFixed(1));
    }
    return parts.join(" ");
  }

  function spark(card, point) {
    if (reduceMotion) return;
    var layer = card.querySelector(".writing-sparks");
    var dot = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    dot.setAttribute("class", "writing-spark");
    dot.setAttribute("cx", String(point[0] + (Math.random() - 0.5) * SPARK_SPREAD));
    dot.setAttribute("cy", String(point[1] + (Math.random() - 0.5) * SPARK_SPREAD));
    dot.setAttribute("r", String(1.5 + Math.random() * 2));
    layer.appendChild(dot);
    window.setTimeout(function () {
      if (dot.parentNode) dot.parentNode.removeChild(dot);
    }, SPARK_LIFE_MS);
  }

  /* --- drawing ----------------------------------------------------------- */

  function onDown(event) {
    if (finished) return;
    var card = cards[at];
    var svg = svgOf(card);
    if (!svg.contains(event.target) && svg !== event.target) return;
    if (traced[at].length >= MAX_STROKES) return;

    event.preventDefault();
    if (svg.setPointerCapture && event.pointerId !== undefined) {
      try {
        svg.setPointerCapture(event.pointerId);
      } catch (ignored) {
        /* Safari refuses capture on some elements; the document listeners below
         * are what actually keep a stroke alive, so this is decoration. */
      }
    }
    if (startedAt === null) startedAt = Date.now();

    var point = pointIn(svg, event);
    if (!point) return;
    drawing = { index: at, points: [point], element: inkPath([point]) };
    inkLayer(card).appendChild(drawing.element);
    hide(hint);
  }

  function onMove(event) {
    if (!drawing) return;
    event.preventDefault();
    var card = cards[drawing.index];
    var point = pointIn(svgOf(card), event);
    if (!point) return;

    var points = drawing.points;
    var last = points[points.length - 1];
    if (distance(point, last) < MIN_STEP) return;
    if (points.length >= MAX_POINTS) return;

    points.push(point);
    drawing.element.setAttribute("d", pathData(points));

    /* Sparks only where the finger is actually on the line. A page that
     * sparkles wherever a child drags would be telling them that anything they
     * do is right, which is the opposite of a guide. */
    var now = Date.now();
    if (now - lastSparkAt >= SPARK_EVERY_MS) {
      var paths = guidePathsOf(card);
      for (var i = 0; i < paths.length; i++) {
        if (nearest(point, guidePoints(paths[i])) <= TOLERANCE) {
          spark(card, point);
          lastSparkAt = now;
          break;
        }
      }
    }
  }

  function onUp() {
    if (!drawing) return;
    var index = drawing.index;
    /* A tap is not a stroke. Two points is what the server's smallest stroke
     * needs, and a single point would post as an empty movement. */
    if (drawing.points.length >= 2) traced[index].push(drawing.points);
    else if (drawing.element.parentNode) {
      drawing.element.parentNode.removeChild(drawing.element);
    }
    drawing = null;
    /* The letter the stroke belongs to, which is not necessarily the one on
     * screen by the time the finger comes up. */
    redrawInk(index);
    paint();
  }

  /* --- the demonstration ------------------------------------------------- */

  function demonstrate() {
    var card = cards[at];
    var paths = guidePathsOf(card);
    if (!paths.length) return;
    if (reduceMotion) {
      /* No animation, so the numbered start dots are all there is -- and they
       * are already on the page. Flashing them is the whole affordance. */
      card.classList.add("writing-card--hinting");
      window.setTimeout(function () {
        card.classList.remove("writing-card--hinting");
      }, 1200);
      return;
    }

    var layer = card.querySelector(".writing-sparks");
    var index = 0;
    (function next() {
      if (index >= paths.length) return;
      var source = paths[index];
      var trail = document.createElementNS("http://www.w3.org/2000/svg", "path");
      trail.setAttribute("class", "writing-demo");
      trail.setAttribute("d", source.getAttribute("d"));
      layer.appendChild(trail);

      var length = source.getTotalLength();
      trail.style.strokeDasharray = String(length);
      trail.style.strokeDashoffset = String(length);
      var startedAtMs = null;
      (function step(now) {
        if (startedAtMs === null) startedAtMs = now;
        var share = Math.min(1, (now - startedAtMs) / SHOW_MS_PER_STROKE);
        trail.style.strokeDashoffset = String(length * (1 - share));
        if (share < 1) {
          window.requestAnimationFrame(step);
          return;
        }
        window.setTimeout(function () {
          if (trail.parentNode) trail.parentNode.removeChild(trail);
          index++;
          next();
        }, 250);
      })(performance.now());
    })();
  }

  /* --- moving on --------------------------------------------------------- */

  function clearCurrent() {
    traced[at] = [];
    redrawInk(at);
    paint();
  }

  function advance() {
    if (at < total - 1) {
      at++;
      show(at);
      return;
    }
    finish();
  }

  function hide(element) {
    if (element) element.hidden = true;
  }

  function say(message) {
    if (!hint) return;
    hint.textContent = message;
    hint.hidden = !message;
  }

  function payload() {
    var glyphs = [];
    for (var i = 0; i < traced.length; i++) {
      if (!traced[i].length) continue;
      var strokes = [];
      for (var s = 0; s < traced[i].length; s++) {
        strokes.push({
          points: traced[i][s].map(function (point) {
            return [Math.round(point[0] * 10) / 10, Math.round(point[1] * 10) / 10];
          }),
        });
      }
      glyphs.push({ index: i, strokes: strokes });
    }
    return {
      /* Never zero: the server refuses a tracing that claims to have taken no
       * time, and a very fast one is a real thing a child can do. */
      seconds: Math.max(0.1, (Date.now() - (startedAt || Date.now())) / 1000),
      glyphs: glyphs,
    };
  }

  function finish() {
    if (finished) return;
    finished = true;
    nextButton.disabled = true;
    say(root.dataset.labelWorking);

    window
      .fetch(postUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload()),
      })
      .then(function (response) {
        if (!response.ok) throw new Error("mark failed");
        return response.text();
      })
      .then(function (html) {
        say("");
        output.innerHTML = html;
        root.classList.add("writing--done");
        var again = document.getElementById("writing-again");
        if (again) again.addEventListener("click", restart);
        output.scrollIntoView({ block: "nearest" });
      })
      .catch(function () {
        finished = false;
        nextButton.disabled = false;
        say(root.dataset.labelFailed);
      });
  }

  function restart() {
    for (var i = 0; i < traced.length; i++) {
      traced[i] = [];
      redrawInk(i);
    }
    finished = false;
    startedAt = null;
    at = 0;
    nextButton.disabled = false;
    output.innerHTML = "";
    root.classList.remove("writing--done");
    show(at);
  }

  /* --- wiring ------------------------------------------------------------ */

  root.classList.add("writing--live");
  show(0);

  sheet.addEventListener("pointerdown", onDown);
  /* On the document rather than on the SVG: a finger that leaves the letter
   * mid-stroke must still finish the stroke it started, and going outside the
   * lines is exactly what a child learning to write does. */
  document.addEventListener("pointermove", onMove, { passive: false });
  document.addEventListener("pointerup", onUp);
  document.addEventListener("pointercancel", onUp);

  showButton.addEventListener("click", demonstrate);
  clearButton.addEventListener("click", clearCurrent);
  nextButton.addEventListener("click", advance);

  /* Said once, on a device that has no touchscreen. Not a refusal: a mouse can
   * drive this and a teacher may well want to look at it on a laptop. It is
   * simply not the exercise, and a page that let that pass unsaid would be
   * scoring the mouse. */
  if (window.matchMedia && !window.matchMedia("(any-pointer: coarse)").matches) {
    say(root.dataset.labelMouse);
  }
})();
