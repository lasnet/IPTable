(function () {
  const table = document.querySelector(".asset-table");
  if (!table) {
    return;
  }

  let dirtyRow = null;
  let allowSubmit = false;

  const editableSelector = "input:not([type='hidden']), textarea, select";

  function rowFor(element) {
    return element.closest("tr");
  }

  function rowAddress(row) {
    return row?.dataset.rowAddress || "выбранной строки";
  }

  function firstEditable(row) {
    return row?.querySelector(editableSelector);
  }

  function hasDirtyRow() {
    return dirtyRow && dirtyRow.classList.contains("row-dirty");
  }

  function focusDirtyRow() {
    const target = firstEditable(dirtyRow);
    if (target) {
      window.setTimeout(() => target.focus(), 0);
    }
  }

  function warnAboutDirtyRow() {
    window.alert(`Есть несохраненные изменения в строке ${rowAddress(dirtyRow)}. Сначала нажмите Save для этой строки.`);
    focusDirtyRow();
  }

  table.addEventListener("focusin", (event) => {
    const target = event.target;
    if (!target.matches(editableSelector)) {
      return;
    }

    const nextRow = rowFor(target);
    if (hasDirtyRow() && nextRow !== dirtyRow) {
      warnAboutDirtyRow();
    }
  });

  table.addEventListener("input", (event) => {
    const target = event.target;
    if (!target.matches(editableSelector)) {
      return;
    }

    const row = rowFor(target);
    if (!row) {
      return;
    }

    if (hasDirtyRow() && row !== dirtyRow) {
      warnAboutDirtyRow();
      return;
    }

    dirtyRow = row;
    row.classList.add("row-dirty");
  });

  document.addEventListener("submit", (event) => {
    const form = event.target;
    const formRow = document.querySelector(`tr [form="${form.id}"]`)?.closest("tr") || form.closest("tr");

    if (hasDirtyRow() && form.classList.contains("asset-row-form") && formRow === dirtyRow) {
      allowSubmit = true;
      dirtyRow.classList.add("row-saving");
      return;
    }

    if (hasDirtyRow()) {
      event.preventDefault();
      warnAboutDirtyRow();
      return;
    }

    const confirmMessage = form.dataset.confirm;
    if (confirmMessage && !window.confirm(confirmMessage)) {
      event.preventDefault();
    }
  }, true);

  document.addEventListener("click", (event) => {
    const link = event.target.closest("a");
    if (!link || !hasDirtyRow()) {
      return;
    }

    const allowLeave = window.confirm(
      `Есть несохраненные изменения в строке ${rowAddress(dirtyRow)}. Перейти без сохранения?`
    );
    if (!allowLeave) {
      event.preventDefault();
      focusDirtyRow();
    }
  }, true);

  window.addEventListener("beforeunload", (event) => {
    if (hasDirtyRow() && !allowSubmit) {
      event.preventDefault();
      event.returnValue = "";
    }
  });
})();

(function () {
  const forms = document.querySelectorAll(".export-form");
  forms.forEach((form) => {
    const toggle = form.querySelector("[data-password-toggle]");
    const input = form.querySelector("[data-password-input]");
    if (!toggle || !input) {
      return;
    }

    function syncPasswordInput() {
      input.disabled = !toggle.checked;
      input.required = toggle.checked;
      if (!toggle.checked) {
        input.value = "";
      }
    }

    toggle.addEventListener("change", syncPasswordInput);
    syncPasswordInput();
  });
})();

(function () {
  if (!window.location.hash) {
    return;
  }

  let targetId = "";
  try {
    targetId = decodeURIComponent(window.location.hash.slice(1));
  } catch (error) {
    return;
  }

  const target = document.getElementById(targetId);
  if (!target || !target.closest(".asset-table-wrap")) {
    return;
  }

  window.setTimeout(() => {
    target.scrollIntoView({ block: "center", inline: "nearest" });
  }, 50);
})();
