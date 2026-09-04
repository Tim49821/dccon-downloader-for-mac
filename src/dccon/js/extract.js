// Re-read the active DOM at save time; never use a cached package or URL hash.
(function() {
  try {
    return JSON.stringify(window.__dcconReadDetail());
  } catch (error) {
    return JSON.stringify({error: "추출 예외: " + error.message,
      package_id: "", title: "", source_url: location.href, items: []});
  }
})();
