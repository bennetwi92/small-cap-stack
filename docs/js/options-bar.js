// Shared options bar (#288) — ported from the macro-beans v2 cockpit.
//
// An expandable form that starts as a single compact row of controls. Pages
// pass a list of `primary` fields (always visible) and optional `extra` fields
// (revealed by a ··· toggle that only appears when extras exist). Each page
// owns its own field set; this component only renders and wires them.
//
// Field types: date | text | search | seg (segmented enum) | select | btn |
// readout (page-updated text) | note (static dim text).
//
// Usage:
//   createOptionsBar('optbar', {
//     primary: [{ type:'seg', id:'rs-session', label:'SESSION', value:'all', options:[…] }],
//     onChange: (id, value, fields) => { … },
//   });
// Returns { el, fields } where `fields` maps id -> the control element.

function buildField(f, emit) {
  const isSeg = f.type === "seg";
  const isWrapless = f.type === "btn" || f.type === "readout" || f.type === "note";
  const wrap = document.createElement(isSeg || isWrapless ? "div" : "label");
  wrap.className = "opt-field";

  if (f.label && f.type !== "btn") {
    const label = document.createElement("span");
    label.className = "opt-label";
    label.textContent = f.label;
    wrap.appendChild(label);
  }

  // Segmented control: a row of buttons, exactly one active.
  if (isSeg) {
    const seg = document.createElement("div");
    seg.className = "opt-seg";
    if (f.id) seg.id = f.id;
    let current = f.value;
    for (const o of f.options || []) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "opt-seg-btn" + (o.value === f.value ? " on" : "");
      b.dataset.value = o.value;
      b.textContent = o.label;
      b.addEventListener("click", () => {
        if (current === o.value) return;
        current = o.value;
        seg.querySelectorAll(".opt-seg-btn").forEach((x) =>
          x.classList.toggle("on", x.dataset.value === current)
        );
        emit(f.id, current);
      });
      seg.appendChild(b);
    }
    seg.getValue = () => current;
    wrap.appendChild(seg);
    return { wrap, input: seg };
  }

  if (f.type === "select") {
    const sel = document.createElement("select");
    sel.className = "opt-input";
    if (f.id) sel.id = f.id;
    for (const o of f.options || []) {
      const opt = document.createElement("option");
      opt.value = o.value;
      opt.textContent = o.label;
      sel.appendChild(opt);
    }
    if (f.value != null) sel.value = f.value;
    sel.addEventListener("change", () => emit(f.id, sel.value));
    wrap.appendChild(sel);
    return { wrap, input: sel };
  }

  if (f.type === "btn") {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "opt-btn";
    if (f.id) btn.id = f.id;
    btn.textContent = f.label || "";
    if (f.title) btn.title = f.title;
    btn.addEventListener("click", () => emit(f.id, null));
    wrap.appendChild(btn);
    return { wrap, input: btn };
  }

  if (f.type === "readout" || f.type === "note") {
    const span = document.createElement("span");
    span.className = f.type === "readout" ? "opt-readout" : "opt-note";
    if (f.id) span.id = f.id;
    if (f.value != null) span.textContent = f.value;
    wrap.appendChild(span);
    return { wrap, input: span };
  }

  // Inputs: date / search / text. ('search' is a text input the page wires a
  // datalist onto for type-ahead.)
  const input = document.createElement("input");
  input.type = f.type === "date" ? "date" : "text";
  input.className = "opt-input" + (f.type === "search" ? " opt-search" : "");
  if (f.type === "search") input.setAttribute("autocomplete", "off");
  if (f.id) input.id = f.id;
  if (f.value != null) input.value = f.value;
  if (f.placeholder) input.placeholder = f.placeholder;
  input.addEventListener("change", () => emit(f.id, input.value));
  wrap.appendChild(input);
  return { wrap, input };
}

export function createOptionsBar(mount, { primary = [], extra = [], onChange } = {}) {
  const el = typeof mount === "string" ? document.getElementById(mount) : mount;
  if (!el) return null;

  el.classList.add("optbar");
  el.innerHTML = "";
  const fields = {};
  const emit = (id, value) => onChange?.(id, value, fields);

  const addField = (f, row) => {
    const { wrap, input } = buildField(f, emit);
    if (f.id) fields[f.id] = input;
    row.appendChild(wrap);
  };

  const primaryRow = document.createElement("div");
  primaryRow.className = "optbar-row";
  primary.forEach((f) => addField(f, primaryRow));
  el.appendChild(primaryRow);

  // Expand mechanism — only surfaced when a page supplies extra controls.
  if (extra.length) {
    const extraRow = document.createElement("div");
    extraRow.className = "optbar-row optbar-extra";
    extraRow.hidden = true;
    extra.forEach((f) => addField(f, extraRow));

    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "opt-expand";
    btn.setAttribute("aria-expanded", "false");
    btn.title = "More options";
    btn.textContent = "···";
    btn.addEventListener("click", () => {
      const open = extraRow.hidden;
      extraRow.hidden = !open;
      btn.setAttribute("aria-expanded", String(open));
      btn.classList.toggle("on", open);
    });

    primaryRow.appendChild(btn);
    el.appendChild(extraRow);
  }

  return { el, fields };
}
