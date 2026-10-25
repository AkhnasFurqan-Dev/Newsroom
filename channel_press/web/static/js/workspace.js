/* Editorial workspace behaviour.
 *
 * Plain DOM, no framework. The server is the source of truth: this script only
 * warns an editor that a form has unsaved changes and lets them undo those
 * changes before navigating away. It never submits or saves on its own.
 */
(function () {
  "use strict";

  function fieldsOf(form) {
    return Array.prototype.slice.call(
      form.querySelectorAll("input[name], textarea[name], select[name]")
    );
  }

  function initialValue(field) {
    if (field.type === "checkbox" || field.type === "radio") {
      return field.hasAttribute("data-initial")
        ? field.getAttribute("data-initial") === "1"
        : field.defaultChecked;
    }
    if (field.tagName === "SELECT") {
      return field.hasAttribute("data-initial")
        ? field.getAttribute("data-initial")
        : field.options[field.selectedIndex]
        ? field.options[field.selectedIndex].value
        : "";
    }
    return field.hasAttribute("data-initial")
      ? field.getAttribute("data-initial")
      : field.defaultValue;
  }

  function currentValue(field) {
    if (field.type === "checkbox" || field.type === "radio") {
      return Boolean(field.checked);
    }
    return field.value;
  }

  function isDirty(form) {
    return fieldsOf(form).some(function (field) {
      if (field.name === "csrf_token" || field.name === "expected_revision") {
        return false;
      }
      if (field.type === "radio") {
        // A radio group is dirty only when it has a selection that differs
        // from the rendered selection, or the rendered one was cleared.
        var group = form.querySelectorAll('input[type="radio"][name="' + field.name + '"]');
        var checked = Array.prototype.filter.call(group, function (item) {
          return item.checked;
        })[0];
        var original = Array.prototype.filter.call(group, function (item) {
          return item.defaultChecked;
        })[0];
        return (checked ? checked.value : "") !== (original ? original.value : "");
      }
      return currentValue(field) !== initialValue(field);
    });
  }

  function refresh(form) {
    var dirty = isDirty(form);
    form.classList.toggle("is-dirty", dirty);
    var note = form.querySelector("[data-dirty-note]");
    if (note) {
      note.hidden = !dirty;
    }
    var save = form.querySelector('button[type="submit"]:not([data-revert])');
    if (save) {
      save.classList.toggle("is-armed", dirty);
    }
    document.body.classList.toggle("has-unsaved-changes", dirty);
    return dirty;
  }

  function revert(form) {
    fieldsOf(form).forEach(function (field) {
      if (field.name === "csrf_token" || field.name === "expected_revision") {
        return;
      }
      if (field.type === "checkbox" || field.type === "radio") {
        field.checked = field.hasAttribute("data-initial")
          ? field.getAttribute("data-initial") === "1"
          : field.defaultChecked;
        return;
      }
      if (field.tagName === "SELECT") {
        var wanted = field.hasAttribute("data-initial")
          ? field.getAttribute("data-initial")
          : null;
        if (wanted !== null) {
          field.value = wanted;
        }
        return;
      }
      field.value = field.hasAttribute("data-initial")
        ? field.getAttribute("data-initial")
        : field.defaultValue;
    });
    refresh(form);
  }

  function initForm(form) {
    if (form.dataset.dirtyReady === "1") {
      return;
    }
    form.dataset.dirtyReady = "1";
    form.addEventListener("input", function () {
      refresh(form);
    });
    form.addEventListener("change", function () {
      refresh(form);
    });
    form.addEventListener("reset", function () {
      window.setTimeout(function () {
        refresh(form);
      }, 0);
    });
    form.addEventListener("submit", function () {
      // A submitted form is no longer "unsaved": the server response replaces
      // this document.
      form.dataset.submitting = "1";
    });
    var revertButton = form.querySelector("[data-revert]");
    if (revertButton && revertButton.tagName !== "BUTTON") {
      revertButton = null;
    }
    Array.prototype.forEach.call(form.querySelectorAll("[data-revert]"), function (button) {
      button.addEventListener("click", function (event) {
        event.preventDefault();
        revert(form);
      });
    });
    refresh(form);
  }

  function init() {
    Array.prototype.forEach.call(
      document.querySelectorAll("form[data-dirty-form]"),
      initForm
    );
    window.addEventListener("beforeunload", function (event) {
      var dirty = Array.prototype.some.call(
        document.querySelectorAll("form[data-dirty-form]"),
        function (form) {
          return form.dataset.submitting !== "1" && isDirty(form);
        }
      );
      if (dirty) {
        event.preventDefault();
        event.returnValue = "You have unsaved editorial changes.";
        return event.returnValue;
      }
      return undefined;
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
