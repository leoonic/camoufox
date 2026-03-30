(function() {
  if (window.__camoufoxCursor) return;
  window.__camoufoxCursor = true;

  var dot = document.createElement("div");
  dot.style.cssText =
    "position:fixed;width:10px;height:10px;" +
    "background:rgba(255,105,105,0.8);border-radius:50%;" +
    "pointer-events:none;z-index:2147483647;" +
    "transform:translate(-50%,-50%);" +
    "box-shadow:0 0 0 5px rgba(255,105,105,0.5)," +
    "0 0 0 10px rgba(255,105,105,0.3)," +
    "0 0 0 15px rgba(255,105,105,0.1);" +
    "display:none;transition:left 0.05s linear,top 0.05s linear;";

  (document.documentElement || document.body).appendChild(dot);

  window.addEventListener("mousemove", function(e) {
    dot.style.left = e.clientX + "px";
    dot.style.top = e.clientY + "px";
    dot.style.display = "block";
  }, true);
})();
