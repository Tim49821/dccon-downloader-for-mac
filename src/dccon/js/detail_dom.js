// Shared reader for live notifications and the save-time snapshot.
(function() {
  function isVisible(element) {
    for (let node = element; node; node = node.parentElement) {
      const style = getComputedStyle(node);
      if (node.hidden || style.display === "none" || style.visibility === "hidden" ||
          style.visibility === "collapse" || style.opacity === "0") return false;
    }
    return true;
  }

  function findDetailLayer() {
    // Never infer a package from a catalog/list container or image count.
    const selectors = [
      "#package_detail", "#dccon_detail", ".dccon_detail", ".detail_layer",
      ".dccon_layer", ".layer_dccon", ".dccon_view", ".dccon_pop",
      ".pop_dccon", ".view_dccon", ".dccon_detail_box"
    ];
    for (const selector of selectors) {
      const layers = Array.from(document.querySelectorAll(selector)).filter(isVisible);
      if (layers.length) return layers[layers.length - 1];
    }
    return Array.from(document.querySelectorAll(".pop_wrap"))
      .filter(el => isVisible(el) && el.querySelector(".dccon_list") &&
        el.querySelector(".viewtxt_top, .dccon_title, .dccon_name")).pop() || null;
  }

  function packageId(layer) {
    // The site writes this after rendering its AJAX response; read the active root only.
    try {
      if (window.jQuery) {
        const data = window.jQuery(layer).data("data");
        if (data && data.package_idx) return String(data.package_idx).trim();
      }
    } catch (_) {}
    for (const attr of ["data-package-idx", "data-package-id", "package_idx", "data-id"]) {
      const value = layer.getAttribute(attr);
      if (value && value.trim()) return value.trim();
    }
    const input = layer.querySelector('input[name="package_idx"]');
    return input ? input.value.trim() : "";
  }

  function title(layer) {
    for (const selector of [".viewtxt_top h4", ".info_viewtxt h4", ".font_blue",
      ".dccon_title", ".dccon_name", ".title", "h4", ".name", "h3", "h2", ".subject", ".tit"]) {
      const el = layer.querySelector(selector);
      const text = el && el.textContent.trim();
      if (text && text.length < 100 && text !== "디시콘 정보") return text;
    }
    return (layer.getAttribute("data-title") || layer.getAttribute("title") || "").trim();
  }

  function allowedImage(src) {
    try {
      const url = new URL(src, location.href);
      if (url.protocol !== "https:" ||
          (url.hostname !== "dcinside.com" && !url.hostname.endsWith(".dcinside.com")) ||
          url.pathname !== "/dccon.php" || !url.searchParams.get("no")) return null;
      return url.href;
    } catch (_) { return null; }
  }

  window.__dcconReadDetail = function() {
    const layer = findDetailLayer();
    const result = {error: null, package_id: "", title: "", source_url: location.href, items: []};
    if (!layer) {
      result.error = "상세 레이어가 없음";
      return result;
    }
    result.package_id = packageId(layer);
    result.title = title(layer);
    let lists = Array.from(layer.querySelectorAll(".dccon_list"));
    if (!lists.length) lists = Array.from(layer.querySelectorAll(".list, ul, ol"));
    // Query each DOM image once even when list containers are nested.
    // Equal URLs on different DOM nodes intentionally remain separate items.
    for (const img of layer.querySelectorAll("img")) {
      if (!lists.some(list => list.contains(img)) || !isVisible(img)) continue;
      const url = allowedImage(img.getAttribute("src") || "");
      if (!url) continue;
      result.items.push({order: result.items.length + 1,
        label: (img.getAttribute("alt") || "").trim(), url: url});
    }
    return result;
  };
})();
