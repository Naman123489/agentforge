// API client: field names and paths follow the contract exactly.
// Storage can be unavailable (sandboxed preview iframes, private mode): the session then lives in memory.
var Store = {
  get: function (k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
  set: function (k, v) { try { localStorage.setItem(k, v); } catch (e) { /* memory only */ } },
  del: function (k) { try { localStorage.removeItem(k); } catch (e) { /* memory only */ } }
};

var Api = {
  token: Store.get('token'),
  username: Store.get('username'),

  send: async function (url, method, body) {
    var headers = { 'Content-Type': 'application/json' };
    if (Api.token) headers['Authorization'] = 'Bearer ' + Api.token;
    var res = await fetch(url, { method: method, headers: headers, body: body ? JSON.stringify(body) : undefined });
    var isJson = (res.headers.get('content-type') || '').indexOf('application/json') !== -1;
    var data = isJson ? await res.json().catch(function () { return null; }) : null;
    if (!res.ok) throw new Error((data && data.detail) || ('HTTP ' + res.status));
    if (data === null) throw new Error('API unavailable (no JSON response)');
    return data;
  },

  saveSession: function (data) {
    Api.token = data.token;
    Api.username = data.username;
    Store.set('token', data.token);
    Store.set('username', data.username);
  },

  register: async function (username, password) {
    Api.saveSession(await Api.send('api/register', 'POST', { username: username, password: password }));
  },

  login: async function (username, password) {
    Api.saveSession(await Api.send('api/login', 'POST', { username: username, password: password }));
  },

  logout: function () {
    Api.token = Api.username = null;
    Store.del('token');
    Store.del('username');
  },

  submitScore: async function (game, score) {
    return Api.send('api/scores', 'POST', { game: game, score: score });
  },

  leaderboard: async function (game) {
    return Api.send(`api/leaderboard?game=${encodeURIComponent(game)}`, 'GET');
  }
};

async function reportScore(game, score) {
  if (!Api.token) { showMessage('Log in to save your score of ' + score); return; }
  try {
    await Api.submitScore(game, score);
    showMessage('Saved ' + game + ' score: ' + score);
    refreshLeaderboard(game);
  } catch (e) { showMessage('Could not save score: ' + e.message); }
}
