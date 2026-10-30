var activeGame = 'snake';

function showMessage(text) { document.getElementById('message').textContent = text; }

async function refreshLeaderboard(game) {
  var list = document.getElementById('leaderboard');
  try {
    var rows = await Api.leaderboard(game || activeGame);
    list.innerHTML = rows.length ? '' : '<li>No scores yet</li>';
    rows.forEach(function (r) {
      var li = document.createElement('li');
      li.textContent = r.username + ' - ' + r.score;
      list.appendChild(li);
    });
  } catch (e) { list.innerHTML = '<li>Leaderboard unavailable</li>'; }
}

function renderAuth() {
  var loggedIn = !!Api.token;
  document.getElementById('who').textContent = loggedIn ? 'Playing as ' + Api.username : '';
  document.getElementById('auth-form').hidden = loggedIn;
  document.getElementById('logout').hidden = !loggedIn;
}

document.getElementById('auth-form').addEventListener('submit', async function (e) {
  e.preventDefault();
  var mode = e.submitter ? e.submitter.dataset.mode : 'login';
  var u = document.getElementById('username').value.trim();
  var p = document.getElementById('password').value;
  try {
    if (mode === 'register') await Api.register(u, p); else await Api.login(u, p);
    showMessage('Welcome, ' + Api.username + '!');
    renderAuth();
  } catch (err) { showMessage(err.message); }
});

document.getElementById('logout').addEventListener('click', function () { Api.logout(); renderAuth(); });

document.querySelectorAll('.tab').forEach(function (tab) {
  tab.addEventListener('click', function () {
    activeGame = tab.dataset.game;
    document.querySelectorAll('.tab').forEach(function (t) { t.classList.toggle('active', t === tab); });
    document.querySelectorAll('.game').forEach(function (s) { s.hidden = s.id !== 'game-' + activeGame; });
    if (activeGame !== 'snake') Snake.stop();
    refreshLeaderboard(activeGame);
  });
});

document.addEventListener('keydown', function (e) {
  if (!e.key.startsWith('Arrow') || e.target.tagName === 'INPUT') return;
  e.preventDefault();
  if (activeGame === 'snake') Snake.key(e.key);
  if (activeGame === '2048') Game2048.key(e.key);
});

renderAuth();
refreshLeaderboard('snake');
