(function() {
  if (window.__cvis_init) return;
  window.__cvis_init = true;

  var host = document.createElement("div");
  host.style.cssText = "position:fixed;top:0;left:0;width:0;height:0;z-index:2147483647;pointer-events:none;";
  var shadow = host.attachShadow({ mode: "closed" });

  var dot = document.createElement("div");
  dot.style.cssText =
    "position:fixed;width:10px;height:10px;" +
    "background:rgba(255,140,0,0.8);border-radius:50%;" +
    "pointer-events:none;" +
    "transform:translate(-50%,-50%);" +
    "display:none;transition:left 0.04s linear,top 0.04s linear;" +
    "box-shadow:0 0 4px rgba(255,140,0,0.4);";

  shadow.appendChild(dot);
  (document.documentElement || document.body).appendChild(host);

  window.addEventListener("mousemove", function(e) {
    dot.style.left = e.clientX + "px";
    dot.style.top = e.clientY + "px";
    dot.style.display = "block";
  }, true);
})();
