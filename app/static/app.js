(function () {
  let dirtyRow = null;
  let allowSubmit = false;

  const editableSelector = ".host-drawer [data-drawer-editable]";
  const i18n = document.body.dataset;

  function formatMessage(template, values) {
    return Object.entries(values).reduce(
      (message, [key, value]) => message.replaceAll(`{${key}}`, value),
      template
    );
  }

  function rowFor(element) {
    const drawer = element.closest?.("[data-host-drawer]");
    if (drawer?.dataset.rowId) {
      return document.getElementById(drawer.dataset.rowId);
    }
    return element.closest?.("tr");
  }

  function rowAddress(row) {
    return row?.dataset.rowAddress || i18n.i18nSelectedRow || "selected row";
  }

  function firstEditable(row) {
    const drawer = document.querySelector("[data-host-drawer]");
    if (!drawer || drawer.dataset.rowId !== row?.id) {
      return null;
    }
    return drawer.querySelector("[data-drawer-editable]:not([disabled])");
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
    const template = i18n.i18nUnsavedRow || "There are unsaved changes in row {row}. Press Save for this row first.";
    window.alert(formatMessage(template, { row: rowAddress(dirtyRow) }));
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

  function clearDirtyState() {
    dirtyRow?.classList.remove("row-dirty", "row-saving");
    dirtyRow = null;
    allowSubmit = false;
    document.querySelector("[data-host-drawer]")?.classList.remove("drawer-dirty");
  }

  function setDrawerEditMode(drawer, enabled) {
    drawer.classList.toggle("editing", enabled);
    drawer.querySelectorAll("[data-drawer-editable]").forEach((field) => {
      field.disabled = !enabled;
    });
    const editButton = drawer.querySelector("[data-host-drawer-edit]");
    const saveButton = drawer.querySelector("[data-host-drawer-save]");
    if (editButton) {
      editButton.hidden = enabled;
    }
    if (saveButton) {
      saveButton.hidden = !enabled;
    }
  }

  function closeHostDrawer(force = false) {
    const drawer = document.querySelector("[data-host-drawer]");
    if (!drawer) {
      return;
    }
    if (!force && hasDirtyRow()) {
      warnAboutDirtyRow();
      return;
    }
    drawer.classList.remove("open");
    drawer.setAttribute("aria-hidden", "true");
    setDrawerEditMode(drawer, false);
    clearDirtyState();
    document.querySelector(".asset-table tr.row-selected")?.classList.remove("row-selected");
  }

  function tableCellValue(row, fieldName) {
    const cell = row.querySelector(`[data-detail-field="${fieldName}"]`);
    return cell?.dataset.detailValue || "";
  }

  function drawerValue(value) {
    const cleanValue = String(value || "").trim();
    return cleanValue || i18n.i18nEmptyValue || "empty";
  }

  function setDrawerField(drawer, name, value) {
    const target = drawer.querySelector(`[data-drawer-field="${name}"]`);
    if (target) {
      target.value = value || "";
      target.placeholder = drawerValue("");
      target.classList.toggle("empty-value", !String(value || "").trim());
    }
  }

  function syncDrawerHiddenFields(drawer, row) {
    const values = {
      hide_empty: row.dataset.hideEmpty || "true",
      page: row.dataset.page || "1",
      per_page: row.dataset.perPage || "",
      ping_status: row.dataset.pingStatusFilter || "",
      type_filter: row.dataset.typeFilter || "",
      os_filter: row.dataset.osFilter || "",
    };
    drawer.querySelectorAll("[data-drawer-hidden]").forEach((field) => {
      field.value = values[field.dataset.drawerHidden] || "";
    });
  }

  function openHostDrawer(row) {
    const drawer = document.querySelector("[data-host-drawer]");
    if (!drawer || !row) {
      return;
    }

    const address = row.dataset.rowAddress || "";
    drawer.dataset.rowId = row.id;
    setDrawerEditMode(drawer, false);
    clearDirtyState();
    drawer.querySelector("[data-drawer-ip-title]").textContent = address;
    drawer.querySelector("[data-host-drawer-form]").action = row.dataset.updateUrl || "";
    const clearForm = drawer.querySelector("[data-host-drawer-clear-form]");
    clearForm.action = row.dataset.clearUrl || "";
    if (clearForm.dataset.confirmTemplate) {
      clearForm.dataset.confirm = formatMessage(clearForm.dataset.confirmTemplate, { address });
    }
    const historyLink = drawer.querySelector("[data-host-drawer-history]");
    historyLink.href = row.dataset.historyUrl || "#";
    syncDrawerHiddenFields(drawer, row);
    setDrawerField(drawer, "address", address);
    setDrawerField(drawer, "hostname", tableCellValue(row, "hostname"));
    setDrawerField(drawer, "os", tableCellValue(row, "os"));
    setDrawerField(drawer, "asset_type", tableCellValue(row, "asset_type"));
    setDrawerField(drawer, "comment", tableCellValue(row, "comment"));

    const statusTarget = drawer.querySelector("[data-drawer-status]");
    const status = row.querySelector(".status-pill");
    statusTarget.innerHTML = "";
    if (status) {
      statusTarget.append(status.cloneNode(true));
    }

    const customTarget = drawer.querySelector("[data-drawer-custom]");
    customTarget.innerHTML = "";
    const customCells = Array.from(row.querySelectorAll("[data-custom-label]"));
    customCells.forEach((cell) => {
      const label = document.createElement("label");
      const labelText = document.createElement("span");
      const input = document.createElement("input");
      const fieldType = cell.dataset.customType || "text";
      const cleanValue = cell.dataset.customValue || "";
      labelText.textContent = cell.dataset.customLabel || "";
      input.name = cell.dataset.customName || "";
      input.type = ["number", "date"].includes(fieldType) ? fieldType : "text";
      input.maxLength = 1000;
      input.value = cleanValue;
      input.placeholder = drawerValue("");
      input.disabled = true;
      input.dataset.drawerEditable = "";
      input.classList.toggle("empty-value", !String(cleanValue).trim());
      label.append(labelText, input);
      customTarget.append(label);
    });

    document.querySelector(".asset-table tr.row-selected")?.classList.remove("row-selected");
    row.classList.add("row-selected");
    drawer.classList.add("open");
    drawer.setAttribute("aria-hidden", "false");
  }

  function editRow(row) {
    if (!row) {
      return;
    }
    const drawer = document.querySelector("[data-host-drawer]");
    if (!drawer || drawer.dataset.rowId !== row.id) {
      openHostDrawer(row);
    }
    setDrawerEditMode(document.querySelector("[data-host-drawer]"), true);
    const target = firstEditable(row);
    if (target) {
      window.setTimeout(() => target.focus(), 0);
    }
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
    closeHostDrawer(true);
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
    target.closest("[data-host-drawer]")?.classList.add("drawer-dirty");
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

    const formRow = rowFor(form);

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
    if (event.target.closest("[data-host-drawer-close]")) {
      closeHostDrawer();
      return;
    }

    const drawerEdit = event.target.closest("[data-host-drawer-edit]");
    if (drawerEdit) {
      const drawer = drawerEdit.closest("[data-host-drawer]");
      editRow(document.getElementById(drawer?.dataset.rowId || ""));
      return;
    }

    const link = event.target.closest("a");
    if (!link) {
      const row = event.target.closest(".asset-table tbody tr[data-row-address]");
      const interactive = event.target.closest("button, input, textarea, select, label, summary, details, .node-menu");
      if (row && !interactive) {
        if (hasDirtyRow()) {
          if (row !== dirtyRow) {
            warnAboutDirtyRow();
            return;
          }
          focusDirtyRow();
          return;
        }
        openHostDrawer(row);
      }
      return;
    }

    if (link.closest("[data-host-drawer-history]") && hasDirtyRow()) {
      const leaveTemplate = i18n.i18nUnsavedLeave || "There are unsaved changes in row {row}. Leave without saving?";
      const allowLeave = window.confirm(formatMessage(leaveTemplate, { row: rowAddress(dirtyRow) }));
      if (!allowLeave) {
        event.preventDefault();
        focusDirtyRow();
      }
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

    const leaveTemplate = i18n.i18nUnsavedLeave || "There are unsaved changes in row {row}. Leave without saving?";
    const allowLeave = window.confirm(formatMessage(leaveTemplate, { row: rowAddress(dirtyRow) }));
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
  if (!document.querySelector(".node-menu")) {
    return;
  }

  function allMenus() {
    return Array.from(document.querySelectorAll(".node-menu"));
  }

  function panelFor(menu) {
    return menu.querySelector(".node-menu-panel");
  }

  function closeMenu(menu) {
    const panel = panelFor(menu);
    menu.open = false;
    if (panel) {
      panel.classList.remove("floating");
      panel.style.removeProperty("--node-menu-top");
      panel.style.removeProperty("--node-menu-left");
      panel.style.removeProperty("visibility");
    }
  }

  function closeOtherMenus(activeMenu) {
    allMenus().forEach((menu) => {
      if (menu !== activeMenu) {
        closeMenu(menu);
      }
    });
  }

  function positionMenu(menu) {
    const button = menu.querySelector(".node-menu-button");
    const panel = panelFor(menu);
    if (!button || !panel) {
      return;
    }

    panel.classList.add("floating");
    panel.style.visibility = "hidden";
    panel.style.setProperty("--node-menu-top", "0px");
    panel.style.setProperty("--node-menu-left", "0px");

    const buttonRect = button.getBoundingClientRect();
    const panelRect = panel.getBoundingClientRect();
    const sidebarRect = menu.closest(".sidebar")?.getBoundingClientRect();
    const viewportPadding = 12;
    const sidebarPadding = 8;

    const minLeft = sidebarRect ? sidebarRect.left + sidebarPadding : viewportPadding;
    const maxLeft = sidebarRect
      ? sidebarRect.right - panelRect.width - sidebarPadding
      : window.innerWidth - panelRect.width - viewportPadding;

    let left = buttonRect.right - panelRect.width;
    if (maxLeft >= minLeft) {
      left = Math.min(Math.max(left, minLeft), maxLeft);
    } else {
      left = Math.min(Math.max(left, viewportPadding), window.innerWidth - panelRect.width - viewportPadding);
    }

    let top = buttonRect.bottom + 6;
    if (top + panelRect.height > window.innerHeight - viewportPadding) {
      top = buttonRect.top - panelRect.height - 6;
    }
    top = Math.max(viewportPadding, top);

    panel.style.setProperty("--node-menu-top", `${top}px`);
    panel.style.setProperty("--node-menu-left", `${left}px`);
    panel.style.removeProperty("visibility");
  }

  document.addEventListener("toggle", (event) => {
    const menu = event.target;
    if (!menu.matches(".node-menu")) {
      return;
    }
    if (menu.open) {
      closeOtherMenus(menu);
      positionMenu(menu);
    } else {
      closeMenu(menu);
    }
  }, true);

  document.addEventListener("click", (event) => {
    if (event.target.closest(".node-menu")) {
      return;
    }
    allMenus().forEach(closeMenu);
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      allMenus().forEach(closeMenu);
    }
  });

  window.addEventListener("resize", () => allMenus().forEach(closeMenu));
  document.querySelector(".tree")?.addEventListener("scroll", () => allMenus().forEach(closeMenu));
})();

(function () {
  const dialogs = Array.from(document.querySelectorAll(".modal-dialog"));
  if (!dialogs.length) {
    return;
  }
  const canUseNativeDialog = typeof HTMLDialogElement !== "undefined";

  function openDialog(dialog) {
    if (!canUseNativeDialog || !(dialog instanceof HTMLDialogElement)) {
      return;
    }
    if (!dialog.open) {
      dialog.showModal();
    }
    window.setTimeout(() => {
      const focusTarget = dialog.querySelector("input:not([type='hidden']), select, textarea, button");
      focusTarget?.focus();
    }, 0);
  }

  function closeDialog(dialog) {
    if (dialog?.open) {
      dialog.close();
    }
  }

  document.addEventListener("click", (event) => {
    const trigger = event.target.closest("[data-modal-target]");
    if (!trigger) {
      return;
    }

    event.preventDefault();
    trigger.closest(".node-menu")?.removeAttribute("open");
    const dialog = document.getElementById(trigger.dataset.modalTarget);
    if (canUseNativeDialog && dialog instanceof HTMLDialogElement) {
      openDialog(dialog);
    }
  });

  document.addEventListener("click", (event) => {
    const closeButton = event.target.closest("[data-modal-close]");
    if (!closeButton) {
      return;
    }
    closeDialog(closeButton.closest(".modal-dialog"));
  });

  dialogs.forEach((dialog) => {
    dialog.addEventListener("click", (event) => {
      if (event.target !== dialog) {
        return;
      }
      const rect = dialog.getBoundingClientRect();
      const clickedInside =
        event.clientX >= rect.left &&
        event.clientX <= rect.right &&
        event.clientY >= rect.top &&
        event.clientY <= rect.bottom;
      if (!clickedInside) {
        closeDialog(dialog);
      }
    });

    if (dialog.dataset.openOnLoad !== undefined && canUseNativeDialog && dialog instanceof HTMLDialogElement) {
      openDialog(dialog);
    }
  });

  document.addEventListener("submit", (event) => {
    const form = event.target;
    if (!form.classList.contains("project-create-form")) {
      return;
    }

    const fileInput = form.querySelector("input[type='file'][name='csv_file']");
    const hasFile = fileInput?.files?.length > 0;
    form.action = hasFile ? form.dataset.importAction : form.dataset.manualAction;
  }, true);
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
