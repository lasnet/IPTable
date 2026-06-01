(function () {
  let dirtyRow = null;
  let allowSubmit = false;

  const editableSelector = ".asset-table input:not([type='hidden']):not([type='checkbox']), .asset-table textarea, .asset-table select";

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

  function projectTableRegion() {
    return document.querySelector("[data-project-table-region]");
  }

  function setFormValue(form, name, value) {
    if (form?.elements[name]) {
      form.elements[name].value = value;
    }
  }

  function syncProjectControls(url) {
    const parsedUrl = new URL(url, window.location.origin);
    const params = parsedUrl.searchParams;
    const hideEmpty = params.get("hide_empty") || "true";
    const perPage = params.get("per_page") || "";
    const pingStatus = params.get("ping_status") || "";
    const typeFilter = params.get("type_filter") || "";
    const osFilter = params.get("os_filter") || "";

    const filterForm = document.querySelector(".table-filter-form");
    if (filterForm) {
      setFormValue(filterForm, "hide_empty", hideEmpty);
      setFormValue(filterForm, "page", "1");
      if (perPage) {
        setFormValue(filterForm, "per_page", perPage);
      }
      setFormValue(filterForm, "ping_status", pingStatus);
      setFormValue(filterForm, "type_filter", typeFilter);
      setFormValue(filterForm, "os_filter", osFilter);
    }

    const hideToggle = document.querySelector("[data-hide-toggle]");
    if (hideToggle) {
      const nextParams = new URLSearchParams(params);
      nextParams.set("hide_empty", hideEmpty === "true" ? "false" : "true");
      nextParams.set("page", "1");
      hideToggle.href = `${parsedUrl.pathname}?${nextParams.toString()}`;
    }

    const resetLink = document.querySelector("[data-filter-reset]");
    if (resetLink) {
      const resetParams = new URLSearchParams();
      resetParams.set("hide_empty", hideEmpty);
      resetParams.set("page", "1");
      if (perPage) {
        resetParams.set("per_page", perPage);
      }
      resetLink.href = `${parsedUrl.pathname}?${resetParams.toString()}`;
    }
  }

  function scrollToHash() {
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
  }

  async function loadTablePage(url, pushState = true) {
    const region = projectTableRegion();
    if (!region) {
      window.location.href = url;
      return;
    }

    const response = await window.fetch(url, {
      headers: { "X-Requested-With": "XMLHttpRequest" },
      credentials: "same-origin",
    });
    if (!response.ok) {
      window.location.href = url;
      return;
    }

    region.innerHTML = await response.text();
    dirtyRow = null;
    allowSubmit = false;
    syncProjectControls(url);
    if (pushState) {
      window.history.pushState(null, "", url);
    }
    scrollToHash();
  }

  document.addEventListener("focusin", (event) => {
    const target = event.target;
    if (!target.matches(editableSelector)) {
      return;
    }

    const nextRow = rowFor(target);
    if (hasDirtyRow() && nextRow !== dirtyRow) {
      warnAboutDirtyRow();
    }
  });

  document.addEventListener("input", (event) => {
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
    if (form.classList.contains("page-size-form") || form.classList.contains("table-filter-form")) {
      event.preventDefault();
      if (hasDirtyRow()) {
        warnAboutDirtyRow();
        return;
      }

      const params = new URLSearchParams(new FormData(form));
      loadTablePage(`${form.action}?${params.toString()}`);
      return;
    }

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

  document.addEventListener("change", (event) => {
    const target = event.target;
    if (target.matches(".page-size-form select") || target.matches(".table-filter-form select")) {
      target.form.requestSubmit();
    }
  });

  document.addEventListener("click", (event) => {
    const link = event.target.closest("a");
    if (!link) {
      return;
    }

    if (link.closest("[data-project-table-region]") && link.closest(".pagination-controls")) {
      event.preventDefault();
      if (hasDirtyRow()) {
        warnAboutDirtyRow();
        return;
      }
      loadTablePage(link.href);
      return;
    }

    if (!hasDirtyRow()) {
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

  window.addEventListener("popstate", () => {
    loadTablePage(window.location.href, false);
  });

  window.addEventListener("beforeunload", (event) => {
    if (hasDirtyRow() && !allowSubmit) {
      event.preventDefault();
      event.returnValue = "";
    }
  });

  scrollToHash();
  syncProjectControls(window.location.href);
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
