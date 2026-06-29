// Shared console-nav behaviour. Each console link normally opens (and reuses) its own named
// browser tab (target="cm_..."); the "Open server pages in separate tabs" setting can switch
// them to open in place (_self) instead. Cached in localStorage so it applies instantly on
// load and stays in sync across the operator's open console tabs.
(function () {
  function apply() {
    var on = localStorage.getItem('cm_opentabs') !== '0';        // default ON
    var links = document.querySelectorAll('a[target]');
    for (var i = 0; i < links.length; i++) {
      var a = links[i];
      if (a.dataset.cmtgt === undefined) a.dataset.cmtgt = a.getAttribute('target') || '';   // remember the original once
      if (a.dataset.cmtgt.indexOf('cm_') === 0) a.setAttribute('target', on ? a.dataset.cmtgt : '_self');
    }
  }
  window.cmOpenTabs = function (on) {                             // pages call this when they learn the setting
    if (on !== undefined) localStorage.setItem('cm_opentabs', on ? '1' : '0');
    apply();
  };
  window.addEventListener('storage', function (e) { if (e.key === 'cm_opentabs') apply(); });   // live across tabs
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', apply);
  else apply();
})();
