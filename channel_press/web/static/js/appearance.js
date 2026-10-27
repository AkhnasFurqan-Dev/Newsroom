/* Appearance screen: keep HEX text and the browser colour picker in step, and
   let an editor see a role sample with the chosen installed family/weight. */
(function () {
  "use strict";

  function normaliseHex(value) {
    var raw = String(value || "").trim();
    if (/^#[0-9a-fA-F]{6}$/.test(raw)) return raw.toLowerCase();
    if (/^#[0-9a-fA-F]{3}$/.test(raw)) {
      return ("#" + raw[1] + raw[1] + raw[2] + raw[2] + raw[3] + raw[3]).toLowerCase();
    }
    return "";
  }

  document.querySelectorAll("[data-color-picker]").forEach(function (picker) {
    var text = document.querySelector('[data-color-text="' + picker.dataset.colorPicker + '"]');
    if (!text) return;
    picker.addEventListener("input", function () {
      text.value = picker.value;
      text.dispatchEvent(new Event("change", { bubbles: true }));
    });
    text.addEventListener("change", function () {
      var hex = normaliseHex(text.value);
      if (hex) picker.value = hex;
    });
  });

  document.querySelectorAll("[data-font-role]").forEach(function (select) {
    var role = select.dataset.fontRole;
    var weight = document.querySelector('[data-font-weight="' + role + '"]');
    var style = document.querySelector('[data-font-style="' + role + '"]');
    var sample = document.querySelector('[data-role-sample="' + role + '"]');
    if (!sample) return;
    function familyFromValue(value) {
      if (value.indexOf("system:") === 0) return value.slice(7);
      var option = select.options[select.selectedIndex];
      if (!option) return "";
      return option.textContent.split("—")[0].trim();
    }
    function update() {
      var family = familyFromValue(select.value);
      if (family) sample.style.fontFamily = '"' + family.replace(/"/g, "") + '", serif';
      if (weight) sample.style.fontWeight = weight.value;
      if (style) sample.style.fontStyle = style.value;
    }
    select.addEventListener("change", update);
    if (weight) weight.addEventListener("change", update);
    if (style) style.addEventListener("change", update);
  });
})();
