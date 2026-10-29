/* portfolio-kit theme boot: a classic script for <head> that sets data-theme before the first paint. Shares localStorage['hh-theme'] with the hub. */
(function () {
  var KEY = 'hh-theme';
  var root = document.documentElement;
  var mq = window.matchMedia ? window.matchMedia('(prefers-color-scheme: light)') : null;
  function stored() {
    try {
      var v = window.localStorage.getItem(KEY);
      return v === 'light' || v === 'dark' ? v : null;
    } catch (e) {
      return null;
    }
  }
  function preferred() {
    return stored() || (mq && mq.matches ? 'light' : 'dark');
  }
  function get() {
    return root.getAttribute('data-theme') === 'light' ? 'light' : 'dark';
  }
  function apply(mode, notify) {
    root.setAttribute('data-theme', mode);
    if (notify) document.dispatchEvent(new CustomEvent('pf:theme', { detail: { theme: mode } }));
  }
  function set(mode) {
    var next = mode === 'light' ? 'light' : 'dark';
    try {
      window.localStorage.setItem(KEY, next);
    } catch (e) {
      /* storage blocked: the choice lasts for this page view */
    }
    apply(next, true);
  }
  function follow() {
    var mode = preferred();
    if (mode !== get()) apply(mode, true);
  }
  apply(preferred(), false);
  window.pfTheme = {
    get: get,
    set: set,
    toggle: function () {
      set(get() === 'light' ? 'dark' : 'light');
    },
  };
  if (mq && mq.addEventListener) mq.addEventListener('change', follow);
  else if (mq && mq.addListener) mq.addListener(follow);
  window.addEventListener('storage', function (e) {
    if (e.key === KEY || e.key === null) follow();
  });
})();
