(function () {
  'use strict';
  var $ = function (id) { return document.getElementById(id); };
  var state = { files: [], roots: [], selected: {}, current: null, closed: {}, cfg: null, shown: [], starOnly: false };

  function api(path, body) {
    var opt = { cache: 'no-store' };
    if (body !== undefined) {
      opt.method = 'POST';
      opt.headers = { 'Content-Type': 'application/json', 'X-Requested-With': 'pystemon' };
      opt.body = JSON.stringify(body);
    }
    return fetch('api/' + path, opt).then(function (r) {
      return r.json().then(function (j) { if (!r.ok) { throw new Error(j.error || r.status); } return j; });
    });
  }
  var toastTimer;
  function toast(msg) {
    var t = $('toast'); t.textContent = msg; t.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(function () { t.hidden = true; }, 4000);
  }
  function el(tag, cls, text) {
    var e = document.createElement(tag); if (cls) { e.className = cls; } if (text !== undefined) { e.textContent = text; } return e;
  }
  function human(n) { return n < 1024 ? n + ' B' : n < 1048576 ? (n / 1024).toFixed(1) + ' KB' : (n / 1048576).toFixed(1) + ' MB'; }

  // ---- tabs (the current tab lives in the address as #pastes, #settings or #log, so a refresh keeps it)
  var TABS = ['pastes', 'settings', 'log'];
  function logToBottom() { var v = $('log'); v.scrollTop = v.scrollHeight; }
  function showTab(name) {
    if (TABS.indexOf(name) < 0) { name = 'pastes'; }
    document.querySelectorAll('.tab').forEach(function (x) { x.classList.toggle('on', x.dataset.tab === name); });
    document.querySelectorAll('.view').forEach(function (v) { v.hidden = v.id !== name; });
    try { history.replaceState(null, '', '#' + name); } catch (e) { /* address bar not available */ }
    if (name === 'settings') { loadConfig(); }
    if (name === 'log') { logToBottom(); }
  }
  document.querySelectorAll('.tab').forEach(function (b) { b.onclick = function () { showTab(b.dataset.tab); }; });
  window.addEventListener('hashchange', function () { showTab(location.hash.slice(1)); });

  // ---- status and controls
  function refreshStatus() {
    return api('status').then(function (s) {
      if (!state.mode) {
        state.mode = s.mode; document.title = s.title; document.querySelector('header strong').textContent = s.title;
        if (s.mode === 'telegram') { $('basicCard').hidden = true; $('chanCard').hidden = false; }
        else { checkIp(); setInterval(checkIp, 300000); }
      }
      var p = $('state'); p.textContent = s.running ? 'running' : 'stopped'; p.className = 'pill ' + (s.running ? 'run' : 'stop');
      $('uptime').textContent = s.running ? 'up ' + Math.floor(s.uptime / 60) + ' min (pid ' + s.pid + ')' : '';
      $('btnStart').disabled = s.running; $('btnStop').disabled = !s.running; $('btnRestart').disabled = false;
      var lv = $('log'), atBottom = lv.scrollHeight - lv.scrollTop - lv.clientHeight < 40;
      $('logBody').textContent = s.log.join('\n');
      if (atBottom) { logToBottom(); }
      $('npmTarget').textContent = (s.lan_ip || 'this-machine-ip') + ':' + s.port;
      $('here').textContent = location.origin + location.pathname;
    }).catch(function (e) { $('state').textContent = 'no connection'; });
  }
  function control(name, label) {
    $(name).onclick = function () {
      this.disabled = true;
      api(label, {}).then(function (r) { toast(r.message || 'done'); return refreshStatus(); }).catch(function (e) { toast(e.message); refreshStatus(); });
    };
  }
  control('btnStart', 'start'); control('btnStop', 'stop'); control('btnRestart', 'restart');

  // ---- tree
  function matches(f, q) { if (state.starOnly && !f.fav) { return false; } return !q || (f.name + ' ' + f.site + ' ' + f.day).toLowerCase().indexOf(q) >= 0; }
  function renderTree() {
    var q = $('filter').value.trim().toLowerCase();
    var tree = $('tree'); tree.textContent = '';
    var shown = state.files.filter(function (f) { return matches(f, q); });
    state.shown = shown;
    var groups = {}, order = [];
    shown.forEach(function (f) {
      var k = f.root + '|' + f.day;
      if (!groups[k]) { groups[k] = []; order.push(k); }
      groups[k].push(f);
    });
    order.sort(function (a, b) { return (b.split('|')[1] + b.split('|')[0]).localeCompare(a.split('|')[1] + a.split('|')[0]); });
    order.forEach(function (k) {
      var parts = k.split('|'), items = groups[k];
      var g = el('div', 'grp'); g.setAttribute('role', 'treeitem');
      var cb = el('input'); cb.type = 'checkbox';
      cb.checked = items.every(function (f) { return state.selected[f.id]; });
      cb.onclick = function (ev) { ev.stopPropagation(); items.forEach(function (f) { if (cb.checked) { state.selected[f.id] = 1; } else { delete state.selected[f.id]; } }); renderTree(); };
      var closed = state.closed[k];
      g.appendChild(el('span', '', closed ? '▸' : '▾'));
      g.appendChild(cb);
      g.appendChild(el('span', '', parts[1] + '  ·  ' + (state.roots[parts[0]] || '')));
      g.appendChild(el('span', 'n', String(items.length)));
      g.onclick = function () { state.closed[k] = !closed; renderTree(); };
      tree.appendChild(g);
      if (!closed) {
        items.forEach(function (f) {
          var row = el('div', 'file' + (state.current === f.id ? ' cur' : ''));
          var c = el('input'); c.type = 'checkbox'; c.checked = !!state.selected[f.id];
          c.onclick = function (ev) { ev.stopPropagation(); if (c.checked) { state.selected[f.id] = 1; } else { delete state.selected[f.id]; } updateSel(); };
          row.appendChild(c);
          row.appendChild(el('span', 'nm', (f.site ? f.site + ' / ' : '') + f.name));
          row.appendChild(el('span', 'sz', human(f.size)));
          var star = el('button', 'star' + (f.fav ? ' on' : ''), f.fav ? '\u2605\uFE0E' : '\u2606\uFE0E');
          star.title = f.fav ? 'Starred (protected from delete). Tap to unstar' : 'Star this paste';
          star.setAttribute('aria-label', star.title);
          star.onclick = function (ev) {
            ev.stopPropagation();
            api('star', { id: f.id, on: !f.fav }).then(function (r) { f.fav = r.fav; renderTree(); }).catch(function (e) { toast(e.message); });
          };
          row.appendChild(star);
          row.onclick = function () { openPaste(f); };
          tree.appendChild(row);
        });
      }
    });
    $('listInfo').textContent = shown.length + ' shown of ' + state.total + ' saved' + (state.total > state.files.length ? ' (newest ' + state.files.length + ' listed)' : '');
    updateSel();
  }
  function updateSel() {
    var n = Object.keys(state.selected).length;
    $('selCount').textContent = n ? n + ' selected' : '';
    $('btnDelete').disabled = !n;
    $('selAll').checked = state.shown.length > 0 && state.shown.every(function (f) { return state.selected[f.id]; });
  }
  $('selAll').onclick = function () {
    var on = this.checked;
    state.shown.forEach(function (f) { if (on) { state.selected[f.id] = 1; } else { delete state.selected[f.id]; } });
    renderTree();
  };
  $('filter').oninput = renderTree;
  $('btnStarOnly').onclick = function () {
    state.starOnly = !state.starOnly;
    this.classList.toggle('on', state.starOnly);
    this.textContent = (state.starOnly ? '\u2605\uFE0E' : '\u2606\uFE0E') + ' Starred';
    renderTree();
  };
  $('btnDelete').onclick = function () {
    var starred = {};
    state.files.forEach(function (f) { if (f.fav) { starred[f.id] = 1; } });
    var picked = Object.keys(state.selected), ids = picked.filter(function (i) { return !starred[i]; });
    var skipped = picked.length - ids.length;
    if (!ids.length) { toast(skipped ? 'Only starred pastes selected, nothing deleted. Unstar first to delete.' : 'Nothing selected'); return; }
    if (!confirm('Delete ' + ids.length + ' paste(s) from the disk? This cannot be undone.' + (skipped ? ' (' + skipped + ' starred will be kept.)' : ''))) { return; }
    api('delete', { ids: ids }).then(function (r) {
      toast('Deleted ' + r.deleted + (skipped ? ', kept ' + skipped + ' starred' : ''));
      state.selected = {};
      if (state.current && ids.indexOf(state.current) >= 0) { state.current = null; $('pastes').classList.add('noview'); $('vbody').textContent = ''; $('vhead').textContent = 'Pick a paste on the left'; }
      return loadList();
    }).catch(function (e) { toast(e.message); });
  };
  function loadList() {
    return api('pastes').then(function (r) { state.files = r.files; state.roots = r.roots; state.total = r.total; renderTree(); });
  }

  // ---- viewer
  function openPaste(f) {
    state.current = f.id; $('pastes').classList.remove('noview'); renderTree();
    $('vhead').textContent = 'Loading...'; $('vbody').textContent = '';
    api('paste?id=' + encodeURIComponent(f.id)).then(function (r) {
      var head = (f.site ? f.site + ' / ' : '') + f.name + '  ·  ' + human(r.size) + (r.truncated ? '  ·  first 1 MB only' : '');
      if (r.matched && r.matched.length) { head += '  ·  matches: ' + r.matched.join(', '); }
      $('vhead').textContent = head;
      var body = $('vbody'); body.textContent = '';
      var text = r.text, pos = 0;
      if (r.meta) { body.appendChild(el('div', 'muted', r.meta + '\n------\n')); }
      r.hits.forEach(function (h) {
        body.appendChild(document.createTextNode(text.slice(pos, h[0])));
        body.appendChild(el('mark', '', text.slice(h[0], h[1])));
        pos = h[1];
      });
      body.appendChild(document.createTextNode(text.slice(pos)));
      var first = body.querySelector('mark'); if (first) { first.scrollIntoView({ block: 'center' }); }
    }).catch(function (e) { $('vhead').textContent = 'Could not open: ' + e.message; });
  }

  // ---- settings
  function searchRow(v) {
    var d = el('div', 'srow');
    var mk = function (ph, val, w) { var i = el('input'); i.placeholder = ph; i.value = val == null ? '' : val; return i; };
    var a = mk('search term (regex)', v.search), b = mk('note', v.description), c = mk('min count', v.count), x = mk('ignore if also matches', v.exclude);
    c.type = 'number'; c.min = 1;
    var rm = el('button', '', 'Remove'); rm.onclick = function () { d.remove(); };
    [a, b, c, x, rm].forEach(function (n) { d.appendChild(n); });
    d.get = function () { return { search: a.value, description: b.value, count: c.value, exclude: x.value }; };
    return d;
  }
  function loadConfig() {
    api('config').then(function (r) {
      state.cfg = r.form; $('raw').value = r.raw;
      var box = $('searchRows'); box.textContent = '';
      r.form.search.forEach(function (s) { box.appendChild(searchRow(s)); });
      var sb = $('siteRows'); sb.textContent = '';
      Object.keys(r.form.sites).forEach(function (name) {
        var s = r.form.sites[name], row = el('div', 'card');
        row.dataset.site = name;
        row.appendChild(el('strong', '', name + (s.preset ? '  (preset: ' + s.preset + ')' : '')));
        var f = el('div', 'row'); row.appendChild(f);
        var en = el('input'); en.type = 'checkbox'; en.checked = s.enable !== false; en.dataset.k = 'enable';
        var l0 = el('label', '', ' enabled'); l0.prepend(en); f.appendChild(l0);
        [['mode', 'mode'], ['limit', 'pastes per poll'], ['lang', 'only language'], ['min-size', 'min size (bytes)'], ['max-size', 'max size (bytes)']].forEach(function (p) {
          var l = el('label', '', p[1] + ' '); var i = el('input'); i.dataset.k = p[0]; i.value = s[p[0]] == null ? '' : s[p[0]]; i.size = 8; l.appendChild(i); f.appendChild(l);
        });
        sb.appendChild(row);
      });
      $('chanText').value = (r.form.channels || []).join('\n');
      $('netIp').value = r.form.network_ip; $('emailAlert').checked = r.form.email_alert;
    }).catch(function (e) { toast(e.message); });
  }
  $('addSearch').onclick = function () { $('searchRows').appendChild(searchRow({})); };
  $('btnPubIp').onclick = function () { $('pubIp').textContent = 'checking...'; api('publicip').then(function (r) { $('pubIp').textContent = r.ip || 'could not detect'; }); };
  function collect() {
    var form = { search: [], sites: {} };
    if (state.mode === 'telegram') { form.channels = $('chanText').value.split('\n'); }
    else { form.network_ip = $('netIp').value; form.email_alert = $('emailAlert').checked; }
    $('searchRows').querySelectorAll('.srow').forEach(function (r) { form.search.push(r.get()); });
    $('siteRows').querySelectorAll('[data-site]').forEach(function (card) {
      var o = {};
      card.querySelectorAll('[data-k]').forEach(function (i) { o[i.dataset.k] = i.type === 'checkbox' ? i.checked : i.value; });
      form.sites[card.dataset.site] = o;
    });
    return form;
  }
  function saved(r) {
    $('saveMsg').textContent = 'Saved' + (r.applied ? ' and applied (' + r.applied + ')' : '. Start the scraper to use it.'); toast('Saved');
    loadConfig();
  }
  $('btnSave').onclick = function () { $('saveMsg').textContent = 'Saving...'; api('config', { form: collect() }).then(saved).catch(function (e) { $('saveMsg').textContent = 'Not saved: ' + e.message; }); };
  $('btnSaveRaw').onclick = function () { api('config', { raw: $('raw').value }).then(saved).catch(function (e) { toast('Not saved: ' + e.message); }); };

  // ---- public IP watch: warn when the address Pastebin sees is not the one the user confirmed
  var curIp = '';
  function checkIp() {
    api('publicip').then(function (r) {
      curIp = r.ip || '';
      var bad = curIp && r.ack !== curIp;
      $('ipbanner').hidden = !bad;
      if (bad) {
        $('ipmsg').textContent = r.ack ? 'Public IP changed from ' + r.ack + ' to ' + curIp + '. Whitelist the new one at Pastebin or scraping will stop.'
          : 'Public IP is ' + curIp + '. Confirm it is whitelisted at Pastebin.';
      }
    }).catch(function () {});
  }
  $('ipok').onclick = function () { api('ipack', { ip: curIp }).then(function () { $('ipbanner').hidden = true; toast('Saved'); }).catch(function (e) { toast(e.message); }); };

  // ---- go
  showTab(location.hash.slice(1));
  loadList(); refreshStatus().then(function () { if (!$('log').hidden) { logToBottom(); } });
  setInterval(refreshStatus, 5000);
  setInterval(function () { if (!$('pastes').hidden && document.visibilityState === 'visible') { loadList(); } }, 30000);
})();
