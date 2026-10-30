var Game2048 = (function () {
  var grid, score, over;
  var board = document.getElementById('board-2048');

  function empty() {
    var cells = [];
    for (var r = 0; r < 4; r++) for (var c = 0; c < 4; c++) if (!grid[r][c]) cells.push([r, c]);
    return cells;
  }

  function addTile() {
    var cells = empty();
    if (!cells.length) return;
    var p = cells[Math.floor(Math.random() * cells.length)];
    grid[p[0]][p[1]] = Math.random() < 0.9 ? 2 : 4;
  }

  function render() {
    board.innerHTML = '';
    grid.forEach(function (row) {
      row.forEach(function (v) {
        var d = document.createElement('div');
        d.className = 'cell' + (v >= 64 ? ' big' : '');
        d.dataset.v = v;
        d.textContent = v || '';
        board.appendChild(d);
      });
    });
    document.getElementById('score-2048').textContent = score;
  }

  function slideRow(row) {
    var vals = row.filter(Boolean), out = [];
    for (var i = 0; i < vals.length; i++) {
      if (vals[i] === vals[i + 1]) { out.push(vals[i] * 2); score += vals[i] * 2; i++; } else out.push(vals[i]);
    }
    while (out.length < 4) out.push(0);
    return out;
  }

  function rotate(g) { return g[0].map(function (_, c) { return g.map(function (row) { return row[c]; }).reverse(); }); }

  function move(times) {
    var g = grid;
    for (var i = 0; i < times; i++) g = rotate(g);
    g = g.map(slideRow);
    for (var j = 0; j < (4 - times) % 4; j++) g = rotate(g);
    return g;
  }

  function canMove() {
    if (empty().length) return true;
    for (var r = 0; r < 4; r++) for (var c = 0; c < 4; c++) {
      if ((c < 3 && grid[r][c] === grid[r][c + 1]) || (r < 3 && grid[r][c] === grid[r + 1][c])) return true;
    }
    return false;
  }

  function key(k) {
    if (over) return;
    var turns = { ArrowLeft: 0, ArrowDown: 1, ArrowRight: 2, ArrowUp: 3 }[k];
    if (turns === undefined) return;
    var before = JSON.stringify(grid);
    grid = move(turns);
    if (JSON.stringify(grid) !== before) addTile();
    render();
    if (!canMove()) { over = true; reportScore('2048', score); }
  }

  function reset() {
    grid = [[0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0]];
    score = 0; over = false; addTile(); addTile(); render();
  }

  document.getElementById('new-2048').addEventListener('click', reset);
  document.getElementById('submit-2048').addEventListener('click', function () { reportScore('2048', score); });
  reset();
  return { key: key };
})();
