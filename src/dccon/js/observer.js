// Follow AJAX layer changes without depending on page navigation.
(function() {
  if (window.__dcconObserverInstalled) return;
  window.__dcconObserverInstalled = true;
  let timer = null;
  let previous = "";
  let bridge = null;

  function notify() {
    if (!bridge) return;
    const detail = window.__dcconReadDetail();
    const payload = JSON.stringify({hasLayer: !detail.error,
      packageId: detail.package_id, title: detail.title, count: detail.items.length});
    if (payload !== previous) {
      previous = payload;
      bridge.onDetailChanged(payload);
    }
  }

  function scheduleNotify() {
    clearTimeout(timer);
    timer = setTimeout(notify, 100);
  }

  function connect() {
    if (typeof QWebChannel !== "function" || typeof qt === "undefined" || !qt.webChannelTransport) {
      setTimeout(connect, 200);
      return;
    }
    new QWebChannel(qt.webChannelTransport, function(channel) {
      bridge = channel.objects.bridge;
      const observer = new MutationObserver(scheduleNotify);
      observer.observe(document.documentElement, {
        childList: true, subtree: true, characterData: true, attributes: true,
        attributeFilter: ["style", "class", "hidden", "src", "alt", "title",
          "data-title", "data-package-id", "data-package-idx", "package_idx", "data-id", "value"]
      });
      // Also re-read jQuery data after click handlers run; no request interception needed.
      document.addEventListener("click", scheduleNotify, true);
      window.addEventListener("hashchange", scheduleNotify);
      window.addEventListener("dccon-refresh-detail", function() {
        previous = "";
        notify();
      });
      notify();
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", connect, {once: true});
  } else {
    connect();
  }
})();
