const HEADER_FIELDS = [
  "Company",
  "CUSTOMER PO #",
  "VENDOR PO #",
  "PO DATE",
];

const LINE_FIELDS = ["FIG#", "Qty", "Unit Price"];

const FIELDS = [...HEADER_FIELDS, ...LINE_FIELDS];

const DATE_FIELDS = new Set([
  "PO DATE",
]);

const SHORT_FIELDS = new Set([
  ...DATE_FIELDS,
  "Qty",
  "Unit Price",
]);

const drop = document.getElementById("drop");
const fileInput = document.getElementById("file");
const browse = document.getElementById("browse");
const filebar = document.getElementById("filebar");
const filename = document.getElementById("filename");
const filesize = document.getElementById("filesize");
const clearFileButton = document.getElementById("clear-file");
const readRowBox = document.getElementById("read-row");
const readButton = document.getElementById("read");
const reader = document.getElementById("reader");
const status = document.getElementById("status");
const review = document.getElementById("review");
const pdf = document.getElementById("pdf");
const summary = document.getElementById("summary");
const count = document.getElementById("count");
const lines = document.getElementById("lines");
const order = document.getElementById("order");
const form = document.getElementById("ledger-form");
const addLine = document.getElementById("add-line");
const sheetBox = document.getElementById("to-sheet");
const zohoBox = document.getElementById("to-zoho");
const destHint = document.getElementById("dest-hint");
const submitButton = document.getElementById("submit");
const submitSpin = document.getElementById("submit-spin");
const submitLabel = document.getElementById("submit-label");
const result = document.getElementById("result");
const syncWhen = document.getElementById("sync-when");

let file = null;
let objectUrl = null;
let pdfShown = null;
const shell = document.querySelector(".shell");
let phase = "idle";
let pristine = "";
let customerLoad = Promise.resolve();
let customers = [];
let figureLoad = Promise.resolve({ figures: [], matches: {} });
let figureCatalog = [];
let itemLoad = Promise.resolve({ connected: false, items: [] });
let matchLoad = Promise.resolve({ matches: {} });
let zohoItems = [];
let optionLoad = Promise.resolve({
  connected: false,
  paymentTerms: [],
  deliveryMethods: [],
  matches: {},
});
const HELD_FIELDS = ["Payment Terms", "Delivery Method", "Other", "Customer Notes", "Description"];
let catalog = null;
let resolution = null;

const refreshLists = document.getElementById("refresh-lists");
const syncDialog = document.getElementById("sync-dialog");

function keepPageOpen(event) {
  event.preventDefault();
  event.returnValue = "";
}

refreshLists.addEventListener("click", async () => {
  refreshLists.disabled = true;
  syncDialog.hidden = false;
  window.addEventListener("beforeunload", keepPageOpen);
  try {
    const response = await fetch("/api/catalog/refresh", { method: "POST" });
    catalog = await readJson(response);
    customers = catalog.customers || [];
    zohoItems = catalog.items || [];
    figureCatalog = catalog.figures || [];
    showSyncTime(catalog.saved);
    showError("");
  } catch (error) {
    showError(error.message);
  }
  window.removeEventListener("beforeunload", keepPageOpen);
  syncDialog.hidden = true;
  refreshLists.disabled = false;
});
loadCatalog().catch((error) => showError(error.message));

browse.addEventListener("click", (event) => {
  event.stopPropagation();
  fileInput.click();
});

drop.addEventListener("click", (event) => {
  if (event.target.closest("button")) return;
  fileInput.click();
});

drop.addEventListener("dragenter", () => drop.classList.add("is-over"));
drop.addEventListener("dragleave", () => drop.classList.remove("is-over"));
drop.addEventListener("drop", (event) => {
  event.preventDefault();
  event.stopPropagation();
  drop.classList.remove("is-over");
  const dropped = event.dataTransfer.files && event.dataTransfer.files[0];
  if (dropped) useFile(dropped);
});

window.addEventListener("dragover", (event) => event.preventDefault());
window.addEventListener("drop", (event) => {
  event.preventDefault();
  const dropped = event.dataTransfer.files && event.dataTransfer.files[0];
  if (dropped) useFile(dropped);
});

fileInput.addEventListener("change", () => {
  const chosen = fileInput.files && fileInput.files[0];
  if (chosen) useFile(chosen);
});

clearFileButton.addEventListener("click", clearFile);
readButton.addEventListener("click", () => readPdf());
addLine.addEventListener("click", () => {
  const row = appendRow(blankRow(), lineCount());
  row.querySelector("input").focus();
  renumber();
  result.hidden = true;
});

sheetBox.addEventListener("change", syncSubmit);
zohoBox.addEventListener("change", syncSubmit);
form.addEventListener("submit", onSubmit);
form.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && event.target.matches("input:not(.picker-search)")) event.preventDefault();
});

document.addEventListener("click", (event) => {
  const open = document.querySelector(".picker.is-open");
  if (open && !open.contains(event.target)) closePicker(open);
});

form.addEventListener("input", (event) => {
  const input = event.target;
  if (!input.matches("input, textarea")) return;
  input.classList.remove("is-invalid");
  if (input.tagName === "TEXTAREA") fitField(input);
  const entry = input.closest(".entry");
  if (entry && entry !== order) paint(entry);
  result.hidden = true;
  showError("");
});

lines.addEventListener("click", (event) => {
  const button = event.target.closest(".remove");
  if (!button) return;
  button.closest(".entry").remove();
  renumber();
  result.hidden = true;
});

function blankRow() {
  return Object.fromEntries(FIELDS.map((field) => [field, ""]));
}

function useFile(next) {
  const name = next.name || "purchase-order.pdf";
  const isPdf = next.type === "application/pdf" || name.toLowerCase().endsWith(".pdf");
  if (!isPdf) {
    showError("Only a PDF can be read.");
    return;
  }
  if ((phase === "review" || phase === "sending") && isDirty()) {
    if (!window.confirm("Replace this purchase order? The lines you are editing will be cleared.")) {
      fileInput.value = "";
      return;
    }
  }
  if (objectUrl) URL.revokeObjectURL(objectUrl);
  file = next;
  objectUrl = URL.createObjectURL(next);
  pdfShown = null;
  pdf.removeAttribute("src");
  pdf.title = name;
  filename.textContent = name;
  filesize.textContent = formatSize(next.size);
  lines.replaceChildren();
  order.replaceChildren();
  result.hidden = true;
  showError("");
  setPhase("ready");
}

function forgetFile() {
  if (objectUrl) URL.revokeObjectURL(objectUrl);
  objectUrl = null;
  file = null;
  fileInput.value = "";
  pdfShown = null;
  pdf.removeAttribute("src");
  pdf.title = "Purchase order PDF";
  lines.replaceChildren();
  order.replaceChildren();
  summary.textContent = "";
}

function clearFile() {
  if ((phase === "review" || phase === "sending") && isDirty()) {
    if (!window.confirm("Remove this PDF and the lines you are editing?")) return;
  }
  forgetFile();
  result.hidden = true;
  showError("");
  setPhase("idle");
}

async function readPdf() {
  if (!file || phase === "reading") return;
  showError("");
  result.hidden = true;
  setPhase("reading");
  const body = new FormData();
  body.append("pdf", file);
  try {
    const response = await fetch("/api/extract", { method: "POST", body });
    const data = await readJson(response);
    const rows = Array.isArray(data.rows) && data.rows.length ? data.rows : [blankRow()];
    await loadCatalog();
    resolution = await resolveRows(rows);
    renderLines(rows);
    summary.textContent = !data.rows || !data.rows.length
      ? "Nothing was found in the PDF. Fill in the order and the line below, or add more."
      : "";
    await Promise.all([customerLoad, figureLoad, itemLoad, optionLoad, matchLoad]);
    setPhase("review");
    pristine = JSON.stringify(readAll());
    const first = order.querySelector(".picker-value, textarea");
    if (first) first.focus();
  } catch (error) {
    showError(error.message);
    if (lineCount()) setPhase("review");
    else setPhase(file ? "ready" : "idle");
  }
}

function renderLines(rows) {
  renderOrder(rows);
  lines.replaceChildren();
  figureLoad = requestFigures(rows);
  itemLoad = requestItems();
  matchLoad = requestItemMatches(rows);
  rows.forEach((row, index) => appendRow(row, index));
  renumber();
}

async function loadCatalog() {
  if (catalog && catalog.ready) return catalog;
  const response = await fetch("/api/catalog");
  catalog = await readJson(response);
  customers = catalog.customers || [];
  zohoItems = catalog.items || [];
  figureCatalog = catalog.figures || [];
  showSyncTime(catalog.saved);
  return catalog;
}

function showSyncTime(saved) {
  const stamp = Number(saved);
  const date = new Date(stamp * 1000);
  if (!stamp || Number.isNaN(date.getTime())) {
    syncWhen.textContent = "";
    return;
  }
  const formatted = new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZone: "America/New_York",
    timeZoneName: "short",
  }).format(date);
  syncWhen.textContent = `Last sync ${formatted}`;
}

async function resolveRows(rows) {
  const response = await fetch("/api/resolve", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ rows }),
    signal: AbortSignal.timeout(90000),
  });
  return readJson(response);
}

async function requestItems() {
  if (catalog && catalog.ready) {
    zohoItems = catalog.items || [];
    return { connected: Boolean(catalog.connected), items: zohoItems };
  }
  try {
    const response = await fetch("/api/items", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
      signal: AbortSignal.timeout(180000),
    });
    const data = await readJson(response);
    zohoItems = Array.isArray(data.items) ? data.items : [];
    return { connected: Boolean(data.connected), items: zohoItems };
  } catch (error) {
    zohoItems = [];
    return {
      connected: false,
      items: [],
      error: error.message || "Zoho Books could not load items.",
    };
  }
}

async function requestItemMatches(rows) {
  if (resolution) return { matches: resolution.items || {} };
  const figures = rows
    .map((row) => (row["FIG#"] == null ? "" : String(row["FIG#"]).trim()))
    .filter(Boolean);
  if (!figures.length) return { matches: {} };
  try {
    const response = await fetch("/api/item-matches", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ figures }),
      signal: AbortSignal.timeout(150000),
    });
    const data = await readJson(response);
    return { matches: data.matches || {} };
  } catch (error) {
    return { matches: {}, error: error.message || "Zoho Books could not match items." };
  }
}

async function requestFigures(rows) {
  if (catalog && catalog.ready && resolution) {
    figureCatalog = catalog.figures || [];
    return { figures: figureCatalog, matches: resolution.figures || {} };
  }
  const sources = rows
    .map((row) => (row["FIG#"] == null ? "" : String(row["FIG#"]).trim()))
    .filter(Boolean);
  try {
    const response = await fetch("/api/figures", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ figures: sources }),
      signal: AbortSignal.timeout(60000),
    });
    const data = await readJson(response);
    figureCatalog = Array.isArray(data.figures) ? data.figures : [];
    return { figures: figureCatalog, matches: data.matches || {} };
  } catch (error) {
    figureCatalog = [];
    return { figures: [], matches: {}, error: error.message || "The inventory figures could not be loaded." };
  }
}

function firstFilled(rows, field) {
  const found = rows
    .map((row) => (row[field] == null ? "" : String(row[field]).trim()))
    .find((value) => value);
  return found || "";
}

function requestOrderOptions(paymentTerms, deliveryMethod) {
  if (catalog && catalog.ready && resolution) {
    return Promise.resolve({
      connected: Boolean(catalog.connected),
      paymentTerms: catalog.paymentTerms || [],
      deliveryMethods: catalog.deliveryMethods || [],
      matches: {
        paymentTerms: resolution.paymentTerms,
        deliveryMethod: resolution.deliveryMethod,
      },
    });
  }
  return fetch("/api/order-options", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ paymentTerms, deliveryMethod }),
    signal: AbortSignal.timeout(60000),
  }).then(async (response) => {
    const data = await readJson(response);
    return {
      connected: Boolean(data.connected),
      paymentTerms: Array.isArray(data.paymentTerms) ? data.paymentTerms : [],
      deliveryMethods: Array.isArray(data.deliveryMethods) ? data.deliveryMethods : [],
      matches: data.matches || {},
      paymentTermsError: data.paymentTermsError || "",
      deliveryMethodsError: data.deliveryMethodsError || "",
    };
  }).catch((error) => ({
    connected: false,
    paymentTerms: [],
    deliveryMethods: [],
    matches: {},
    error: error.message || "Zoho Books could not load payment terms.",
  }));
}

function sharedHeader(rows) {
  const header = blankRow();
  HEADER_FIELDS.forEach((field) => {
    const found = rows
      .map((row) => (row[field] == null ? "" : String(row[field]).trim()))
      .find((value) => value);
    header[field] = found || "";
  });
  return header;
}

function renderOrder(rows) {
  order.replaceChildren();
  const head = document.createElement("div");
  head.className = "entry-head";
  const title = document.createElement("h3");
  title.className = "line-no";
  title.textContent = "Order";
  head.append(title);

  const fields = document.createElement("div");
  fields.className = "fields";
  const shared = sharedHeader(rows);
  const paymentTerms = firstFilled(rows, "Payment Terms");
  const deliveryMethod = firstFilled(rows, "Delivery Method");
  optionLoad = requestOrderOptions(paymentTerms, deliveryMethod);
  HEADER_FIELDS.forEach((field) => {
    fields.append(field === "Company" ? companyControl(shared.Company) : fieldControl(field, shared));
  });
  fields.append(zohoOptionControl(paymentTerms, {
    field: "Payment Terms",
    label: "Payment Terms",
    listKey: "paymentTerms",
    matchKey: "paymentTerms",
    errorKey: "paymentTermsError",
    pending: "Checking payment terms",
    choose: "Choose a payment term",
    search: "Search payment terms",
    empty: "No payment term with that name.",
    none: "No payment terms were found in Zoho Books.",
    unmatched: "Choose a Zoho Books payment term.",
  }));
  fields.append(zohoOptionControl(deliveryMethod, {
    field: "Delivery Method",
    label: "Delivery method",
    listKey: "deliveryMethods",
    matchKey: "deliveryMethod",
    errorKey: "deliveryMethodsError",
    pending: "Checking delivery methods",
    choose: "Choose a delivery method",
    search: "Search delivery methods",
    empty: "No delivery method with that name.",
    none: "No delivery methods were found in Zoho Books.",
    unmatched: "Choose a Zoho Books delivery method.",
  }));
  fields.append(otherField(firstFilled(rows, "Other")));
  fields.append(customerNotesField());
  order.append(head, fields);
  order.querySelectorAll("textarea").forEach(fitField);
}

function appendRow(row, index) {
  const entry = document.createElement("article");
  entry.className = "entry is-new";
  entry.style.animationDelay = `${Math.min(index, 12) * 35}ms`;

  const head = document.createElement("div");
  head.className = "entry-head";
  const number = document.createElement("h3");
  number.className = "line-no";
  number.textContent = `Line ${index + 1}`;
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "remove";
  remove.textContent = "Remove";
  head.append(number, remove);

  const fields = document.createElement("div");
  fields.className = "fields";
  const foundFigure = row["FIG#"] == null ? "" : String(row["FIG#"]);
  fields.append(zohoFigureControl(foundFigure), sheetFigureControl(foundFigure));

  const figures = document.createElement("div");
  figures.className = "figures";
  figures.append(fieldControl("Qty", row), fieldControl("Unit Price", row));
  const amount = document.createElement("div");
  amount.className = "field amount";
  const amountLabel = document.createElement("span");
  amountLabel.textContent = "Sales Amount";
  const output = document.createElement("output");
  output.textContent = "—";
  amount.append(amountLabel, output);
  figures.append(amount);

  entry.append(head, fields, figures);
  lines.append(entry);
  entry.querySelectorAll("textarea").forEach(fitField);
  paint(entry);
  return entry;
}

function zohoFigureControl(extracted) {
  const wrap = document.createElement("div");
  wrap.className = "field";
  const label = document.createElement("span");
  label.textContent = "Fig# for Zoho Books";
  const note = document.createElement("small");
  note.className = "field-note";
  note.hidden = true;
  const value = document.createElement("input");
  value.type = "hidden";
  value.dataset.zohoItem = "";
  value.value = "";

  const picker = document.createElement("div");
  picker.className = "picker";
  const toggle = document.createElement("button");
  toggle.type = "button";
  toggle.className = "picker-value is-empty";
  toggle.setAttribute("aria-haspopup", "listbox");
  toggle.setAttribute("aria-expanded", "false");
  const toggleText = document.createElement("span");
  toggleText.textContent = "Checking items";
  toggle.append(toggleText, chevron());
  const menu = document.createElement("div");
  menu.className = "picker-menu";
  menu.hidden = true;
  const search = document.createElement("input");
  search.type = "search";
  search.className = "picker-search";
  search.placeholder = "Search items";
  search.autocomplete = "off";
  search.spellcheck = false;
  search.setAttribute("aria-label", "Search items");
  const list = document.createElement("div");
  list.className = "picker-list";
  list.setAttribute("role", "listbox");
  menu.append(search, list);
  picker.append(toggle, menu);
  wrap.append(label, note, value, picker);

  let pickedByUser = false;
  const setChosen = (name, id) => {
    value.value = name || "";
    value.dataset.itemId = id || "";
    toggleText.textContent = name || "Choose an item";
    toggle.classList.toggle("is-empty", !name);
    toggle.classList.remove("is-invalid");
    if (!extracted || (name && name === extracted)) note.hidden = true;
    else {
      note.hidden = false;
      note.textContent = id
        ? `From the PDF: ${extracted}`
        : `From the PDF: ${extracted}. Choose a Zoho Books item.`;
    }
  };

  const renderChoices = (query) => {
    list.replaceChildren();
    const needle = query.trim().toLowerCase();
    let rows = zohoItems.filter((item) => {
      if (!needle) return true;
      return item.name.toLowerCase().includes(needle)
        || (item.sku || "").toLowerCase().includes(needle);
    });
    if (!needle && value.dataset.itemId) {
      const chosen = zohoItems.find((item) => item.id === value.dataset.itemId);
      if (chosen) rows = [chosen, ...rows.filter((item) => item.id !== chosen.id)];
    }
    rows = rows.slice(0, needle ? 40 : 12);
    rows.forEach((item) => {
      const hint = item.status !== "active"
        ? "Inactive"
        : (item.sku && item.sku !== item.name ? item.sku : "");
      list.append(itemChoice(item.name, item.id, hint));
    });
    if (!list.children.length) {
      const empty = document.createElement("p");
      empty.className = "picker-empty";
      empty.textContent = needle ? "No item with that name." : "No items loaded.";
      list.append(empty);
    }
  };

  function itemChoice(name, id, hint) {
    const button = document.createElement("button");
    button.type = "button";
    const current = value.dataset.itemId === id;
    button.className = current ? "is-current" : "";
    button.setAttribute("role", "option");
    button.setAttribute("aria-selected", current ? "true" : "false");
    const title = document.createElement("strong");
    title.textContent = name;
    button.append(title);
    if (hint) {
      const extra = document.createElement("small");
      extra.textContent = hint;
      button.append(extra);
    }
    button.addEventListener("click", () => {
      pickedByUser = true;
      setChosen(name, id);
      closePicker(picker);
      result.hidden = true;
    });
    return button;
  }

  toggle.addEventListener("click", () => {
    if (picker.classList.contains("is-open")) {
      closePicker(picker);
      return;
    }
    closeOtherPickers(picker);
    picker.classList.add("is-open");
    menu.hidden = false;
    toggle.setAttribute("aria-expanded", "true");
    search.value = "";
    renderChoices("");
    search.focus();
  });
  search.addEventListener("input", () => renderChoices(search.value));
  search.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      closePicker(picker);
      toggle.focus();
    }
  });

  itemLoad.then((data) => {
    if (!wrap.isConnected) return;
    if (!data.connected && !data.error) {
      const plain = document.createElement("label");
      plain.className = "field";
      const plainLabel = document.createElement("span");
      plainLabel.textContent = "Fig# for Zoho Books";
      const input = document.createElement("input");
      input.value = extracted;
      input.readOnly = true;
      input.tabIndex = -1;
      input.placeholder = "Empty";
      plain.append(plainLabel, input);
      wrap.replaceWith(plain);
      return;
    }
    value.dataset.ready = data.connected ? "1" : "";
    if (!pickedByUser && !value.dataset.itemId) {
      toggleText.textContent = "Matching the figure";
    }
    if (!menu.hidden) renderChoices(search.value);
    if (data.error) {
      note.hidden = false;
      note.textContent = data.error;
    }
  });
  matchLoad.then((data) => {
    if (!wrap.isConnected || pickedByUser) return;
    const match = data.matches && data.matches[extracted.trim()];
    if (match && match.id) setChosen(match.name, match.id);
    else if (!pickedByUser && !value.dataset.itemId) setChosen("", "");
    if (data.error && note.hidden) {
      note.hidden = false;
      note.textContent = data.error;
    }
  });
  return wrap;
}

function sheetFigureControl(extracted) {
  const wrap = document.createElement("div");
  wrap.className = "field";
  const label = document.createElement("span");
  label.textContent = "Fig# for Spreadsheet";
  const note = document.createElement("small");
  note.className = "field-note";
  note.hidden = true;
  const value = document.createElement("input");
  value.type = "hidden";
  value.dataset.field = "FIG#";
  value.value = "";

  const picker = document.createElement("div");
  picker.className = "picker";
  const toggle = document.createElement("button");
  toggle.type = "button";
  toggle.className = "picker-value is-empty";
  toggle.setAttribute("aria-haspopup", "listbox");
  toggle.setAttribute("aria-expanded", "false");
  const toggleText = document.createElement("span");
  toggleText.textContent = "Checking figures";
  toggle.append(toggleText, chevron());
  const menu = document.createElement("div");
  menu.className = "picker-menu";
  menu.hidden = true;
  const search = document.createElement("input");
  search.type = "search";
  search.className = "picker-search";
  search.placeholder = "Search figures or type a new one";
  search.autocomplete = "off";
  search.spellcheck = false;
  search.setAttribute("aria-label", "Search figures");
  const list = document.createElement("div");
  list.className = "picker-list";
  list.setAttribute("role", "listbox");
  menu.append(search, list);
  picker.append(toggle, menu);
  wrap.append(label, note, value, picker);

  const setChosen = (name) => {
    value.value = name || "";
    toggleText.textContent = name || "Choose a figure";
    toggle.classList.toggle("is-empty", !name);
    toggle.classList.remove("is-invalid");
    if (extracted && name && name !== extracted) {
      note.hidden = false;
      note.textContent = `From the PDF: ${extracted}`;
    } else if (!name && extracted) {
      note.hidden = false;
      note.textContent = `From the PDF: ${extracted}`;
    } else {
      note.hidden = true;
    }
  };

  const renderChoices = (query) => {
    list.replaceChildren();
    const needle = query.trim().toLowerCase();
    let rows = figureCatalog.filter((figure) => !needle || figure.toLowerCase().includes(needle));
    if (!needle && value.value) {
      const chosen = figureCatalog.find((figure) => figure === value.value);
      if (chosen) rows = [chosen, ...rows.filter((figure) => figure !== chosen)];
    }
    rows = rows.slice(0, needle ? 40 : 12);
    const typed = query.trim();
    const exact = figureCatalog.some((figure) => figure.toLowerCase() === typed.toLowerCase());
    if (typed && !exact) list.append(figureChoice(typed, "New figure"));
    rows.forEach((figure) => list.append(figureChoice(figure, "")));
    if (!list.children.length) {
      const empty = document.createElement("p");
      empty.className = "picker-empty";
      empty.textContent = "No figures loaded.";
      list.append(empty);
    }
  };

  function figureChoice(name, hint) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = value.value === name ? "is-current" : "";
    button.setAttribute("role", "option");
    button.setAttribute("aria-selected", value.value === name ? "true" : "false");
    const title = document.createElement("strong");
    title.textContent = name;
    button.append(title);
    if (hint) {
      const extra = document.createElement("small");
      extra.textContent = hint;
      button.append(extra);
    }
    button.addEventListener("click", () => {
      setChosen(name);
      closePicker(picker);
      result.hidden = true;
    });
    return button;
  }

  toggle.addEventListener("click", () => {
    if (picker.classList.contains("is-open")) {
      closePicker(picker);
      return;
    }
    closeOtherPickers(picker);
    picker.classList.add("is-open");
    menu.hidden = false;
    toggle.setAttribute("aria-expanded", "true");
    search.value = "";
    renderChoices("");
    search.focus();
  });
  search.addEventListener("input", () => renderChoices(search.value));
  search.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      closePicker(picker);
      toggle.focus();
    }
  });

  figureLoad.then((data) => {
    if (!wrap.isConnected) return;
    const match = data.matches && data.matches[extracted.trim()];
    if (match) setChosen(match);
    else setChosen("");
    if (data.error) {
      note.hidden = false;
      note.textContent = data.error;
    }
  });
  return wrap;
}

function closeOtherPickers(picker) {
  document.querySelectorAll(".picker.is-open").forEach((open) => {
    if (open !== picker) closePicker(open);
  });
}

function zohoOptionControl(extracted, spec) {
  const wrap = document.createElement("div");
  wrap.className = "field";
  const label = document.createElement("span");
  label.textContent = spec.label;
  const note = document.createElement("small");
  note.className = "field-note";
  note.hidden = true;
  const value = document.createElement("input");
  value.type = "hidden";
  value.dataset.field = spec.field;
  value.value = "";

  const picker = document.createElement("div");
  picker.className = "picker";
  const toggle = document.createElement("button");
  toggle.type = "button";
  toggle.className = "picker-value is-empty";
  toggle.setAttribute("aria-haspopup", "listbox");
  toggle.setAttribute("aria-expanded", "false");
  const toggleText = document.createElement("span");
  toggleText.textContent = spec.pending;
  toggle.append(toggleText, chevron());
  const menu = document.createElement("div");
  menu.className = "picker-menu";
  menu.hidden = true;
  const search = document.createElement("input");
  search.type = "search";
  search.className = "picker-search";
  search.placeholder = spec.search;
  search.autocomplete = "off";
  search.spellcheck = false;
  search.setAttribute("aria-label", spec.search);
  const list = document.createElement("div");
  list.className = "picker-list";
  list.setAttribute("role", "listbox");
  menu.append(search, list);
  picker.append(toggle, menu);
  wrap.append(label, note, value, picker);

  let options = [];

  const setChosen = (name, id, days) => {
    value.value = name || "";
    value.dataset.optionId = id || "";
    if (days === undefined || days === null || days === "") delete value.dataset.optionDays;
    else value.dataset.optionDays = String(days);
    toggleText.textContent = name || spec.choose;
    toggle.classList.toggle("is-empty", !name);
    toggle.classList.remove("is-invalid");
    if (!extracted || (name && name === extracted)) {
      note.hidden = true;
    } else {
      note.hidden = false;
      note.textContent = id ? `From the PDF: ${extracted}` : `From the PDF: ${extracted}. ${spec.unmatched}`;
    }
  };

  const renderChoices = (query) => {
    list.replaceChildren();
    const needle = query.trim().toLowerCase();
    let rows = options.filter((option) => !needle || option.name.toLowerCase().includes(needle));
    if (!needle && value.dataset.optionId) {
      const chosen = options.find((option) => option.id === value.dataset.optionId);
      if (chosen) rows = [chosen, ...rows.filter((option) => option.id !== chosen.id)];
    }
    rows = rows.slice(0, needle ? 40 : 12);
    rows.forEach((option) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = value.dataset.optionId === option.id ? "is-current" : "";
      button.setAttribute("role", "option");
      button.setAttribute("aria-selected", value.dataset.optionId === option.id ? "true" : "false");
      const title = document.createElement("strong");
      title.textContent = option.name;
      button.append(title);
      button.addEventListener("click", () => {
        setChosen(option.name, option.id, Object.hasOwn(option, "days") ? option.days : undefined);
        closePicker(picker);
        result.hidden = true;
      });
      list.append(button);
    });
    if (!list.children.length) {
      const empty = document.createElement("p");
      empty.className = "picker-empty";
      empty.textContent = needle ? spec.empty : "Nothing is loaded.";
      list.append(empty);
    }
  };

  toggle.addEventListener("click", () => {
    if (picker.classList.contains("is-open")) {
      closePicker(picker);
      return;
    }
    closeOtherPickers(picker);
    picker.classList.add("is-open");
    menu.hidden = false;
    toggle.setAttribute("aria-expanded", "true");
    search.value = "";
    renderChoices("");
    search.focus();
  });
  search.addEventListener("input", () => renderChoices(search.value));
  search.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      closePicker(picker);
      toggle.focus();
    }
  });

  optionLoad.then((data) => {
    if (!wrap.isConnected) return;
    options = Array.isArray(data[spec.listKey]) ? data[spec.listKey] : [];
    const match = data.matches && data.matches[spec.matchKey];
    const problem = data[spec.errorKey] || (!data.connected && data.error) || "";
    if (match && match.id && match.name) {
      const chosen = options.find((option) => option.id === match.id);
      setChosen(match.name, match.id, chosen && Object.hasOwn(chosen, "days") ? chosen.days : undefined);
    } else setChosen("", "");
    if (problem) {
      note.hidden = false;
      note.textContent = problem;
    } else if (!data.connected) {
      note.hidden = false;
      note.textContent = "Zoho Books is not connected.";
    } else if (!options.length) {
      note.hidden = false;
      note.textContent = spec.none;
    }
    renderChoices("");
  });
  return wrap;
}

function otherField(extracted) {
  const wrap = document.createElement("label");
  wrap.className = "field";
  const label = document.createElement("span");
  label.textContent = "Other";
  const note = document.createElement("small");
  note.className = "field-note";
  note.textContent = "(e.g. for customer shipment account number)";
  const input = document.createElement("input");
  input.dataset.field = "Other";
  input.value = extracted || "";
  input.placeholder = "Empty";
  input.autocomplete = "off";
  input.spellcheck = false;
  wrap.append(label, note, input);
  return wrap;
}

function customerNotesField() {
  const wrap = document.createElement("label");
  wrap.className = "field";
  const label = document.createElement("span");
  label.textContent = "Customer Notes";
  const input = document.createElement("textarea");
  input.dataset.field = "Customer Notes";
  input.value = "";
  input.rows = 2;
  input.placeholder = "Empty";
  input.autocomplete = "off";
  input.spellcheck = true;
  wrap.append(label, input);
  return wrap;
}

function companyControl(extracted) {
  const wrap = document.createElement("div");
  wrap.className = "field";
  const label = document.createElement("span");
  label.textContent = "Company";
  const note = document.createElement("small");
  note.className = "field-note";
  note.hidden = true;
  const value = document.createElement("input");
  value.type = "hidden";
  value.dataset.field = "Company";
  value.value = "";

  const picker = document.createElement("div");
  picker.className = "picker";
  const toggle = document.createElement("button");
  toggle.type = "button";
  toggle.className = "picker-value is-empty";
  toggle.setAttribute("aria-haspopup", "listbox");
  toggle.setAttribute("aria-expanded", "false");
  const toggleText = document.createElement("span");
  toggleText.textContent = "Checking customers";
  toggle.append(toggleText, chevron());
  const menu = document.createElement("div");
  menu.className = "picker-menu";
  menu.hidden = true;
  const search = document.createElement("input");
  search.type = "search";
  search.className = "picker-search";
  search.placeholder = "Search customers";
  search.autocomplete = "off";
  search.spellcheck = false;
  search.setAttribute("aria-label", "Search customers");
  const list = document.createElement("div");
  list.className = "picker-list";
  list.setAttribute("role", "listbox");
  menu.append(search, list);
  picker.append(toggle, menu);
  wrap.append(label, note, value, picker);

  const setChosen = (name, id) => {
    value.value = name || "";
    value.dataset.contactId = id || "";
    toggleText.textContent = name || "Choose a customer";
    toggle.classList.toggle("is-empty", !name);
    toggle.classList.remove("is-invalid");
    if (!extracted || (name && name === extracted)) {
      note.hidden = true;
    } else {
      note.hidden = false;
      note.textContent = id
        ? `From the PDF: ${extracted}`
        : `From the PDF: ${extracted}. Choose a Zoho Books customer.`;
    }
  };

  const renderChoices = (query) => {
    list.replaceChildren();
    const needle = query.trim().toLowerCase();
    let rows = customers.filter((customer) => {
      if (!needle) return true;
      return customer.name.toLowerCase().includes(needle)
        || (customer.company || "").toLowerCase().includes(needle);
    });
    if (!needle && value.dataset.contactId) {
      const chosen = customers.find((customer) => customer.id === value.dataset.contactId);
      if (chosen) rows = [chosen, ...rows.filter((customer) => customer.id !== chosen.id)];
    }
    rows = rows.slice(0, needle ? 40 : 12);
    rows.forEach((customer) => {
      const hint = customer.status !== "active"
        ? "Inactive"
        : (customer.company && customer.company !== customer.name ? customer.company : "");
      list.append(customerChoice(customer.name, customer.id, hint));
    });
    if (!list.children.length) {
      const empty = document.createElement("p");
      empty.className = "picker-empty";
      empty.textContent = needle ? "No customer with that name." : "No customers loaded.";
      list.append(empty);
    }
  };

  function customerChoice(name, id, hint) {
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.name = name;
    button.dataset.id = id;
    const current = value.value === name && (value.dataset.contactId || "") === id;
    button.className = current ? "is-current" : "";
    button.setAttribute("role", "option");
    button.setAttribute("aria-selected", current ? "true" : "false");
    const title = document.createElement("strong");
    title.textContent = name;
    button.append(title);
    if (hint) {
      const extra = document.createElement("small");
      extra.textContent = hint;
      button.append(extra);
    }
    button.addEventListener("click", () => {
      setChosen(name, id);
      closePicker(picker);
      result.hidden = true;
    });
    return button;
  }

  toggle.addEventListener("click", () => {
    if (picker.classList.contains("is-open")) {
      closePicker(picker);
      return;
    }
    picker.classList.add("is-open");
    menu.hidden = false;
    toggle.setAttribute("aria-expanded", "true");
    search.value = "";
    renderChoices("");
    search.focus();
    closeOtherPickers(picker);
  });
  search.addEventListener("input", () => renderChoices(search.value));
  search.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      closePicker(picker);
      toggle.focus();
    }
  });

  customerLoad = loadCustomers(extracted, wrap, setChosen, note, renderChoices);
  return wrap;
}

async function loadCustomers(extracted, wrap, setChosen, note, renderChoices) {
  if (catalog && catalog.ready) {
    await Promise.resolve();
    if (!wrap.isConnected) return;
    if (!catalog.connected) {
      const plain = fieldControl("Company", { Company: extracted });
      wrap.replaceWith(plain);
      plain.querySelectorAll("textarea").forEach(fitField);
      return;
    }
    customers = catalog.customers || [];
    const match = resolution && resolution.company;
    if (match && match.id && match.name) setChosen(match.name, match.id);
    else if (!customers.length) {
      setChosen("", "");
      note.hidden = false;
      note.textContent = "No customers were found in Zoho Books.";
    } else setChosen("", "");
    renderChoices("");
    return;
  }
  try {
    const response = await fetch(`/api/customers?company=${encodeURIComponent(extracted)}`, {
      signal: AbortSignal.timeout(180000),
    });
    const data = await readJson(response);
    if (!wrap.isConnected) return;
    if (!data.connected) {
      const plain = fieldControl("Company", { Company: extracted });
      wrap.replaceWith(plain);
      plain.querySelectorAll("textarea").forEach(fitField);
      return;
    }
    customers = Array.isArray(data.customers) ? data.customers : [];
    if (data.match && data.match.id && data.match.name) setChosen(data.match.name, data.match.id);
    else if (!customers.length) {
      setChosen("", "");
      note.hidden = false;
      note.textContent = "No customers were found in Zoho Books.";
    } else setChosen("", "");
    renderChoices("");
  } catch (error) {
    if (!wrap.isConnected) return;
    setChosen("", "");
    note.hidden = false;
    note.textContent = "The customer list could not be loaded.";
  }
}

function closePicker(picker) {
  picker.classList.remove("is-open");
  const menu = picker.querySelector(".picker-menu");
  const toggle = picker.querySelector(".picker-value");
  if (menu) menu.hidden = true;
  if (toggle) toggle.setAttribute("aria-expanded", "false");
}

function chevron() {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("width", "14");
  svg.setAttribute("height", "14");
  svg.setAttribute("viewBox", "0 0 14 14");
  svg.setAttribute("aria-hidden", "true");
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("d", "M3 5.2L7 9l4-3.8");
  path.setAttribute("fill", "none");
  path.setAttribute("stroke", "currentColor");
  path.setAttribute("stroke-width", "1.4");
  path.setAttribute("stroke-linecap", "round");
  path.setAttribute("stroke-linejoin", "round");
  svg.append(path);
  return svg;
}

function fieldControl(field, row) {
  if (DATE_FIELDS.has(field)) return dateControl(row[field]);
  const wrap = document.createElement("label");
  wrap.className = "field";
  const label = document.createElement("span");
  label.textContent = field;
  wrap.append(label);
  if (field === "VENDOR PO #") {
    const note = document.createElement("small");
    note.className = "field-note";
    note.textContent = "Only for spreadsheet, column C.";
    wrap.append(note);
  }
  const long = !SHORT_FIELDS.has(field);
  const input = document.createElement(long ? "textarea" : "input");
  input.dataset.field = field;
  const raw = row[field] == null ? "" : String(row[field]);
  input.value = raw;
  input.placeholder = "Empty";
  input.autocomplete = "off";
  input.spellcheck = false;
  if (field === "Qty" || field === "Unit Price") input.inputMode = "decimal";
  if (long) input.rows = 1;
  wrap.append(input);
  return wrap;
}

function fitField(field) {
  field.style.height = "auto";
  field.style.height = `${field.scrollHeight}px`;
}

function paint(entry) {
  const values = readRow(entry);
  const output = entry.querySelector("output");
  const money = salesAmount(values.Qty, values["Unit Price"]);
  output.textContent = money == null ? "—" : money;
}

function dateControl(raw) {
  const wrap = document.createElement("label");
  wrap.className = "field";
  const label = document.createElement("span");
  label.textContent = "PO DATE";
  const box = document.createElement("div");
  box.className = "date-field";
  const input = document.createElement("input");
  input.dataset.field = "PO DATE";
  input.value = String(raw || "").trim();
  input.placeholder = "MM-DD-YYYY";
  input.autocomplete = "off";
  input.spellcheck = false;
  input.inputMode = "numeric";
  const picker = document.createElement("input");
  picker.type = "date";
  picker.className = "date-picker";
  picker.tabIndex = -1;
  picker.value = toInputDate(input.value);
  picker.setAttribute("aria-hidden", "true");
  const button = document.createElement("button");
  button.type = "button";
  button.className = "date-open";
  button.setAttribute("aria-label", "Choose a date");
  button.append(calendarIcon());
  const openPicker = () => {
    const iso = toInputDate(input.value);
    if (iso) picker.value = iso;
    if (typeof picker.showPicker === "function") {
      try {
        picker.showPicker();
        return;
      } catch (error) {
        picker.focus();
      }
    }
    picker.click();
  };
  button.addEventListener("click", (event) => {
    event.preventDefault();
    openPicker();
  });
  picker.addEventListener("change", () => {
    input.value = fromInputDate(picker.value);
    input.classList.remove("is-invalid");
  });
  box.append(input, picker, button);
  wrap.append(label, box);
  return wrap;
}

function calendarIcon() {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("width", "16");
  svg.setAttribute("height", "16");
  svg.setAttribute("viewBox", "0 0 16 16");
  svg.setAttribute("fill", "none");
  svg.setAttribute("aria-hidden", "true");
  const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
  rect.setAttribute("x", "2");
  rect.setAttribute("y", "3");
  rect.setAttribute("width", "12");
  rect.setAttribute("height", "11");
  rect.setAttribute("rx", "1.5");
  rect.setAttribute("stroke", "currentColor");
  rect.setAttribute("stroke-width", "1.4");
  const top = document.createElementNS("http://www.w3.org/2000/svg", "path");
  top.setAttribute("d", "M2 6.5h12M5 2v2.5M11 2v2.5");
  top.setAttribute("stroke", "currentColor");
  top.setAttribute("stroke-width", "1.4");
  top.setAttribute("stroke-linecap", "round");
  svg.append(rect, top);
  return svg;
}

function toInputDate(value) {
  const text = String(value || "").trim();
  const monthFirst = /^(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])-(\d{4})$/.exec(text);
  if (monthFirst) return `${monthFirst[3]}-${monthFirst[1]}-${monthFirst[2]}`;
  if (/^\d{4}-\d{2}-\d{2}$/.test(text)) return text;
  return "";
}

function fromInputDate(value) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value || "").trim());
  if (!match) return "";
  return `${match[2]}-${match[3]}-${match[1]}`;
}

function readInputs(root, values) {
  root.querySelectorAll("input, textarea").forEach((input) => {
    if (!input.dataset.field) return;
    values[input.dataset.field] = input.value.trim();
  });
}

function readRow(entry) {
  const values = blankRow();
  readInputs(entry, values);
  return values;
}

function readAll() {
  const header = blankRow();
  readInputs(order, header);
  return [...lines.querySelectorAll(".entry")].map((entry) => {
    const values = { ...header };
    readInputs(entry, values);
    return values;
  });
}

function lineCount() {
  return lines.querySelectorAll(".entry").length;
}

function renumber() {
  [...lines.querySelectorAll(".entry")].forEach((entry, index) => {
    entry.querySelector(".line-no").textContent = `Line ${index + 1}`;
    entry.querySelectorAll("input, textarea").forEach((input) => {
      input.setAttribute("aria-label", `${input.dataset.field}, line ${index + 1}`);
    });
    entry.querySelector(".remove").setAttribute("aria-label", `Remove line ${index + 1}`);
  });
  const total = lineCount();
  count.textContent = total === 1 ? "1 line" : `${total} lines`;
}

function isDirty() {
  return JSON.stringify(readAll()) !== pristine;
}

function isBlank(row) {
  return FIELDS.every((field) => !row[field]);
}

function numeric(value) {
  const cleaned = String(value == null ? "" : value).replace(/[$,\s]/g, "");
  if (!/^-?\d+(\.\d+)?$/.test(cleaned)) return null;
  return Number(cleaned);
}

function salesAmount(qty, price) {
  const left = numeric(qty);
  const right = numeric(price);
  if (left == null || right == null) return null;
  const amount = Math.round(left * right * 100) / 100;
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(amount);
}

function isNumber(value) {
  return numeric(value) != null;
}

function validate(rows) {
  const company = order.querySelector('[data-field="Company"]');
  if (company && company.type === "hidden" && !company.dataset.contactId) {
    const button = order.querySelector(".picker-value");
    if (button) {
      button.classList.add("is-invalid");
      button.focus();
    }
    return "Choose a customer from Zoho Books.";
  }
  const pendingItem = [...lines.querySelectorAll("[data-zoho-item]")].find((input) => (
    input.dataset.ready === "1" && !input.dataset.itemId
  ));
  if (pendingItem) {
    const button = pendingItem.closest(".field").querySelector(".picker-value");
    if (button) {
      button.classList.add("is-invalid");
      button.focus();
    }
    return "Choose a Zoho Books item for each line.";
  }
  for (let index = 0; index < rows.length; index += 1) {
    const row = rows[index];
    if (isBlank(row)) continue;
    if (row["PO DATE"] && !/^(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])-\d{4}$/.test(row["PO DATE"])) {
      markInvalid(index, "PO DATE");
      return "PO DATE needs to be MM-DD-YYYY.";
    }
    for (const field of ["Qty", "Unit Price"]) {
      if (row[field] && !isNumber(row[field])) {
        markInvalid(index, field);
        return `Line ${index + 1}: ${field} needs to be a plain number.`;
      }
    }
  }
  return "";
}

function zohoReady(rows) {
  if (!zohoBox.checked) return "";
  if (!file) return "The purchase order PDF is missing, so it cannot be attached.";
  const company = order.querySelector('[data-field="Company"]');
  if (!company || !company.dataset.contactId) return "Choose a Zoho Books customer.";
  const terms = order.querySelector('[data-field="Payment Terms"]');
  if (!terms || !terms.dataset.optionId || terms.dataset.optionDays == null || terms.dataset.optionDays === "") {
    return "Choose a Zoho Books payment term.";
  }
  const delivery = order.querySelector('[data-field="Delivery Method"]');
  if (!delivery || !delivery.dataset.optionId) return "Choose a Zoho Books delivery method.";
  const entries = [...lines.querySelectorAll(".entry")];
  for (let index = 0; index < rows.length; index += 1) {
    if (isBlank(rows[index])) continue;
    const item = entries[index] && entries[index].querySelector("[data-zoho-item]");
    if (!item || !item.dataset.itemId) return `Line ${index + 1}: choose a Zoho Books item.`;
  }
  return "";
}

function zohoOrder(rows) {
  const terms = order.querySelector('[data-field="Payment Terms"]');
  const delivery = order.querySelector('[data-field="Delivery Method"]');
  const other = order.querySelector('[data-field="Other"]');
  const notes = order.querySelector('[data-field="Customer Notes"]');
  const entries = [...lines.querySelectorAll(".entry")];
  const header = rows.find((row) => !isBlank(row)) || rows[0] || {};
  return {
    customerId: order.querySelector('[data-field="Company"]').dataset.contactId,
    reference: header["CUSTOMER PO #"] || "",
    date: header["PO DATE"] || "",
    paymentTermsLabel: terms.value,
    paymentTermsDays: terms.dataset.optionDays,
    deliveryMethod: delivery.value,
    deliveryMethodId: delivery.dataset.optionId,
    other: other ? other.value.trim() : "",
    customerNotes: notes ? notes.value.trim() : "",
    lines: rows.flatMap((row, index) => {
      if (isBlank(row)) return [];
      const item = entries[index].querySelector("[data-zoho-item]");
      return [{ itemId: item.dataset.itemId, quantity: row.Qty, rate: row["Unit Price"] }];
    }),
  };
}

function markInvalid(index, field) {
  const root = HEADER_FIELDS.includes(field) ? order : lines.querySelectorAll(".entry")[index];
  const input = root && root.querySelector(`[data-field="${CSS.escape(field)}"]`);
  if (!input) return;
  input.classList.add("is-invalid");
  input.focus();
}

async function onSubmit(event) {
  event.preventDefault();
  if (phase === "sending") return;
  if (!sheetBox.checked && !zohoBox.checked) {
    syncSubmit();
    return;
  }
  const rows = readAll();
  if (!rows.some((row) => !isBlank(row))) {
    showError("Add at least one line before submitting.");
    return;
  }
  const problem = validate(rows) || zohoReady(rows);
  if (problem) {
    showError(problem);
    return;
  }
  showError("");
  result.hidden = true;
  setPhase("sending");
  const sheetRows = rows.map((row) => {
    const copy = { ...row };
    HELD_FIELDS.forEach((field) => delete copy[field]);
    return copy;
  });
  const formData = new FormData();
  formData.append("spreadsheet", sheetBox.checked ? "true" : "false");
  formData.append("zoho", zohoBox.checked ? "true" : "false");
  formData.append("rows", JSON.stringify(sheetRows));
  if (zohoBox.checked) {
    formData.append("order", JSON.stringify(zohoOrder(rows)));
    formData.append("pdf", file, file.name || "purchase-order.pdf");
  }
  try {
    const response = await fetch("/api/submit", { method: "POST", body: formData });
    const data = await readJson(response);
    forgetFile();
    showError("");
    setPhase("idle");
    showResult(data);
  } catch (error) {
    showError(error.message);
    setPhase("review");
  }
}

function showResult(data) {
  result.replaceChildren();
  const sheet = data.spreadsheet;
  const zoho = data.zoho;
  if (sheet) {
    const title = document.createElement("strong");
    if (sheet.written > 0) {
      title.textContent = sheet.written === 1
        ? "1 line added to the spreadsheet."
        : `${sheet.written} lines added to the spreadsheet.`;
    } else {
      title.textContent = "Nothing was added to the spreadsheet.";
    }
    result.append(title);
    if (sheet.message) {
      const note = document.createElement("p");
      note.textContent = sheet.message;
      result.append(note);
    }
    if (sheet.written > 0) {
      const link = document.createElement("a");
      link.href = sheet.url;
      link.target = "_blank";
      link.rel = "noopener";
      link.textContent = "Open the spreadsheet";
      result.append(link);
    }
    const seen = new Set();
    for (const item of sheet.skipped || []) {
      if (seen.has(item.po)) continue;
      seen.add(item.po);
      const note = document.createElement("p");
      note.textContent = `PO ${item.po} is already on the spreadsheet, so those lines were left out.`;
      result.append(note);
    }
  }
  if (zoho && zoho.message) {
    const note = document.createElement("p");
    note.textContent = zoho.message;
    result.append(note);
    if (zoho.url) {
      const link = document.createElement("a");
      link.href = zoho.url;
      link.target = "_blank";
      link.rel = "noopener";
      link.textContent = "Open the sales order";
      result.append(link);
    }
  }
  const sheetFailed = Boolean(sheet && sheet.message);
  const saved = Boolean((sheet && sheet.written > 0) || (zoho && zoho.created && zoho.attachment !== false));
  result.classList.toggle("is-ok", saved && !sheetFailed);
  result.classList.toggle("is-warn", !saved || sheetFailed);
  result.hidden = false;
}

function setPhase(next) {
  phase = next;
  const reviewing = next === "review" || next === "sending";
  document.body.classList.toggle("is-review", reviewing);
  document.body.classList.toggle("is-filed", Boolean(file));
  drop.hidden = Boolean(file);
  filebar.hidden = !file;
  readRowBox.hidden = !file || next === "reading" || reviewing;
  reader.hidden = next !== "reading";
  review.hidden = !reviewing;
  if (reviewing) showPdfWhenWide();
  readButton.disabled = next === "reading";
  submitLabel.textContent = next === "sending" ? "Submitting" : "Submit";
  submitSpin.hidden = next !== "sending";
  syncSubmit();
}

// The browser's PDF viewer picks its zoom once, from the frame's size when the
// document loads. Loading it before the page has widened leaves the pages small,
// so the PDF goes into the frame only after the shell's width transition ends.
function showPdfWhenWide() {
  const url = objectUrl;
  if (!url || pdfShown === url) return;
  let done = false;
  const load = () => {
    if (done) return;
    done = true;
    shell.removeEventListener("transitionend", onEnd);
    clearTimeout(fallback);
    if (objectUrl !== url || review.hidden) return;
    pdfShown = url;
    pdf.src = `${url}#view=FitH`;
  };
  const onEnd = (event) => {
    if (event.target === shell && event.propertyName === "width") load();
  };
  shell.addEventListener("transitionend", onEnd);
  // No transition fires when the shell is already wide or motion is reduced.
  const fallback = setTimeout(load, 600);
}

function syncSubmit() {
  const chosen = sheetBox.checked || zohoBox.checked;
  submitButton.disabled = phase === "sending" || !chosen;
  destHint.hidden = chosen || review.hidden;
}

function showError(message) {
  status.textContent = message || "";
}

function logUsage(data) {
  const calls = data && data.usage;
  if (!Array.isArray(calls)) return;
  calls.forEach((call) => {
    if (call && call.line) console.log(call.line);
  });
}

async function readJson(response) {
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || "Something went wrong.");
  logUsage(data);
  return data;
}

function formatSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

setPhase("idle");
