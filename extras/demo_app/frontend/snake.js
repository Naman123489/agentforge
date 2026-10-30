var Snake = (function () {
  var N = 20, SIZE = 20, timer = null, snake, dir, nextDir, food, score;
  var canvas = document.getElementById('snake-canvas');
  var ctx = canvas.getContext('2d');

  function placeFood() {
    do { food = { x: Math.floor(Math.random() * N), y: Math.floor(Math.random() * N) }; }
    while (snake.some(function (p) { return p.x === food.x && p.y === food.y; }));
  }

  function draw() {
    ctx.fillStyle = '#0b0c10'; ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = '#e04f5f'; ctx.fillRect(food.x * SIZE, food.y * SIZE, SIZE - 1, SIZE - 1);
    ctx.fillStyle = '#76b900';
    snake.forEach(function (p) { ctx.fillRect(p.x * SIZE, p.y * SIZE, SIZE - 1, SIZE - 1); });
    document.getElementById('snake-score').textContent = score;
  }

  function step() {
    dir = nextDir;
    var head = { x: snake[0].x + dir.x, y: snake[0].y + dir.y };
    var hit = head.x < 0 || head.y < 0 || head.x >= N || head.y >= N ||
      snake.some(function (p) { return p.x === head.x && p.y === head.y; });
    if (hit) { stop(); reportScore('snake', score); return; }
    snake.unshift(head);
    if (head.x === food.x && head.y === food.y) { score += 10; placeFood(); } else { snake.pop(); }
    draw();
  }

  function start() {
    stop();
    snake = [{ x: 10, y: 10 }, { x: 9, y: 10 }, { x: 8, y: 10 }];
    dir = nextDir = { x: 1, y: 0 }; score = 0;
    placeFood(); draw();
    timer = setInterval(step, 120);
  }

  function stop() { if (timer) clearInterval(timer); timer = null; }

  function key(k) {
    var d = { ArrowUp: { x: 0, y: -1 }, ArrowDown: { x: 0, y: 1 }, ArrowLeft: { x: -1, y: 0 }, ArrowRight: { x: 1, y: 0 } }[k];
    if (d && (d.x !== -dir.x || d.y !== -dir.y)) nextDir = d;
  }

  document.getElementById('snake-start').addEventListener('click', start);
  snake = [{ x: 10, y: 10 }]; dir = nextDir = { x: 1, y: 0 }; score = 0; placeFood(); draw();
  return { key: key, stop: stop };
})();
