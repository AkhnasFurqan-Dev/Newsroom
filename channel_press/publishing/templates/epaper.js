(function () {
  "use strict";
  var viewer = document.getElementById("viewer");
  if (!viewer) { return; }
  var pages = Array.prototype.slice.call(document.querySelectorAll(".viewer__page"));
  var select = document.getElementById("page-select");
  var prev = document.getElementById("prev");
  var next = document.getElementById("next");
  var zoom = document.getElementById("zoom");
  var index = 0;

  function show(nextIndex) {
    if (nextIndex < 0 || nextIndex >= pages.length) { return; }
    pages[index].hidden = true;
    pages[index].classList.remove("is-active");
    index = nextIndex;
    pages[index].hidden = false;
    pages[index].classList.add("is-active");
    if (select) { select.value = pages[index].getAttribute("data-page"); }
    var caption = pages[index].querySelector("img");
    if (caption) { caption.loading = "eager"; }
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  if (prev) { prev.addEventListener("click", function () { show(index - 1); }); }
  if (next) { next.addEventListener("click", function () { show(index + 1); }); }
  if (select) {
    select.addEventListener("change", function () { show(parseInt(select.value, 10) - 1); });
  }
  if (zoom) {
    zoom.addEventListener("click", function () {
      viewer.classList.toggle("is-actual");
      zoom.textContent = viewer.classList.contains("is-actual") ? "Fit width" : "Actual size";
    });
  }
  document.addEventListener("keydown", function (event) {
    if (event.target && /INPUT|SELECT|TEXTAREA/.test(event.target.tagName)) { return; }
    if (event.key === "ArrowLeft") { show(index - 1); }
    if (event.key === "ArrowRight") { show(index + 1); }
  });
})();
