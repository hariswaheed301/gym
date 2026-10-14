/* Vanilla JS enhancements. The app remains functional without JavaScript. CSP-safe: no inline code. */
(function () {
  'use strict';

  // ---- Confirmation dialogs + double-submit protection --------------------------------------------
  document.addEventListener('submit', function (e) {
    var form = e.target;
    var submitter = e.submitter;
    var msg = (submitter && submitter.dataset.confirmButton) || form.dataset.confirm;
    if (msg && !window.confirm(msg)) { e.preventDefault(); return; }
    // Disable submit buttons after the browser has collected the form (prevents repeated clicks).
    window.setTimeout(function () {
      form.querySelectorAll('button[type=submit]').forEach(function (b) { b.disabled = true; });
    }, 0);
  });
  window.addEventListener('pageshow', function (ev) {
    if (ev.persisted) { document.querySelectorAll('button[type=submit]:disabled').forEach(function (b) { b.disabled = false; }); }
  });
  document.addEventListener('click', function (e) {
    if (e.target.closest('[data-print]')) { window.print(); }
  });

  // ---- Elapsed-time display (computed in the browser from the stored check-in time) ---------------
  var skew = 0; // server clock minus browser clock, so a wrong PC clock does not distort timers
  function setSkew(el) {
    var s = el && Date.parse(el.dataset.serverNow);
    if (s && !isNaN(s)) { skew = s - Date.now(); }
  }
  function fmt(ms) {
    var m = Math.max(0, Math.floor(ms / 60000));
    if (m < 1) { return 'less than a minute'; }
    var h = Math.floor(m / 60), r = m % 60, parts = [];
    if (h) { parts.push(h + (h === 1 ? ' hour' : ' hours')); }
    if (r) { parts.push(r + (r === 1 ? ' minute' : ' minutes')); }
    return parts.join(' ');
  }
  function tick() {
    document.querySelectorAll('.elapsed[data-checkin]').forEach(function (el) {
      var t = Date.parse(el.dataset.checkin);
      if (!isNaN(t)) { el.textContent = fmt(Date.now() + skew - t); }
    });
  }

  function debounce(fn, wait) {
    var t; return function () { var a = arguments, c = this; clearTimeout(t); t = setTimeout(function () { fn.apply(c, a); }, wait); };
  }
  function fetchHtml(url) {
    return fetch(url, { headers: { 'X-Requested-With': 'fetch' }, credentials: 'same-origin' }).then(function (r) {
      if (r.status === 401) { window.location.reload(); throw new Error('session expired'); }
      if (!r.ok) { throw new Error('HTTP ' + r.status); }
      return r.text();
    });
  }

  // ---- Reception: live member search --------------------------------------------------------------
  var searchForm = document.getElementById('member-search');
  if (searchForm) {
    var input = document.getElementById('member-q');
    var results = document.getElementById('results');
    var run = debounce(function () {
      var q = input.value.trim();
      if (!q) { results.textContent = ''; return; }
      results.classList.add('is-loading');
      fetchHtml(searchForm.dataset.url + '?q=' + encodeURIComponent(q)).then(function (html) {
        results.innerHTML = html; // server-rendered, auto-escaped Jinja output
      }).catch(function () {
        results.textContent = 'Search failed. Check your connection and try again.';
      }).finally(function () { results.classList.remove('is-loading'); });
    }, 250);
    input.addEventListener('input', run);
    searchForm.addEventListener('submit', function (e) { e.preventDefault(); run(); });
  }

  // ---- Reception: currently-inside list refresh ---------------------------------------------------
  var live = document.getElementById('inside-live');
  if (live) {
    var filter = document.getElementById('inside-q');
    var status = document.getElementById('inside-status');
    setSkew(live);
    var refresh = function () {
      var url = live.dataset.url + (filter && filter.value.trim() ? '?q=' + encodeURIComponent(filter.value.trim()) : '');
      fetchHtml(url).then(function (html) {
        live.innerHTML = html;
        var count = document.getElementById('inside-count');
        if (count) { count.textContent = live.querySelectorAll('tbody tr').length; }
        tick();
        if (status) { status.textContent = 'Updated ' + new Date().toLocaleTimeString(); }
      }).catch(function () { if (status) { status.textContent = 'Could not refresh — retrying…'; } });
    };
    window.setInterval(refresh, 30000);
    if (filter) { filter.addEventListener('input', debounce(refresh, 300)); }
  }

  tick();
  window.setInterval(tick, 60000);
})();
