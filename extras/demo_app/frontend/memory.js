var Memory = (function () {
  var SYMBOLS = ['🍎', '🚀', '🎲', '🐱', '🌵', '⚡', '🎧', '🍩'];
  var grid = document.getElementById('memory-grid');
  var cards, open, moves, matched, started, busy;

  function shuffle(a) {
    for (var i = a.length - 1; i > 0; i--) { var j = Math.floor(Math.random() * (i + 1)); var t = a[i]; a[i] = a[j]; a[j] = t; }
    return a;
  }

  function render() {
    grid.innerHTML = '';
    cards.forEach(function (c, i) {
      var b = document.createElement('button');
      b.className = 'card' + (c.matched ? ' matched' : '');
      b.textContent = c.open || c.matched ? c.symbol : '?';
      b.addEventListener('click', function () { flip(i); });
      grid.appendChild(b);
    });
    document.getElementById('memory-moves').textContent = moves;
  }

  function flip(i) {
    var c = cards[i];
    if (busy || c.open || c.matched) return;
    if (!started) started = Date.now();
    c.open = true; open.push(i); render();
    if (open.length < 2) return;
    moves++;
    var a = cards[open[0]], b = cards[open[1]];
    if (a.symbol === b.symbol) {
      a.matched = b.matched = true; matched += 2; open = []; render();
      if (matched === cards.length) {
        var seconds = Math.round((Date.now() - started) / 1000);
        reportScore('memory', Math.max(0, 1000 - moves * 10 - seconds));
      }
    } else {
      busy = true;
      setTimeout(function () { a.open = b.open = false; open = []; busy = false; render(); }, 700);
    }
  }

  function reset() {
    cards = shuffle(SYMBOLS.concat(SYMBOLS)).map(function (s) { return { symbol: s, open: false, matched: false }; });
    open = []; moves = 0; matched = 0; started = null; busy = false; render();
  }

  document.getElementById('memory-new').addEventListener('click', reset);
  reset();
  return { reset: reset };
})();
