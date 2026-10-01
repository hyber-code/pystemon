(function () {
  var KEY = 'pystemon-theme', root = document.documentElement;
  function get() { try { return localStorage.getItem(KEY) || 'system'; } catch (e) { return 'system'; } }
  function apply(t) { if (t === 'light' || t === 'dark') { root.setAttribute('data-theme', t); } else { root.removeAttribute('data-theme'); } }
  apply(get());
  window.addEventListener('DOMContentLoaded', function () {
    var b = document.getElementById('btnTheme');
    if (!b) { return; }
    function label() { b.textContent = 'Theme: ' + get(); }
    label();
    b.onclick = function () {
      var next = { system: 'light', light: 'dark', dark: 'system' }[get()] || 'system';
      try { localStorage.setItem(KEY, next); } catch (e) { /* private mode */ }
      apply(next); label();
    };
  });
})();
