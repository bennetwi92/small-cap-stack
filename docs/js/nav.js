// Shared top application bar (#288), macOS-menu-bar style: wordmark left, every
// page listed horizontally, active page in the session accent. One source of
// truth for the page list; each page mounts <header id="appbar" class="appbar">
// and imports this module.

import "./session.js"; // the wordmark dot + active link follow the session accent

export const PAGES = [
  { label: "Dashboard", file: "index.html" },
  { label: "Review",    file: "review.html" },
  { label: "Results",   file: "results.html" },
  { label: "Portfolio", file: "portfolio.html" },
];

function currentFile() {
  const name = location.pathname.split("/").pop();
  return name && name.length ? name : "index.html";
}

export function renderNav(mountId = "appbar") {
  const mount = document.getElementById(mountId);
  if (!mount) return;
  const here = currentFile();
  const links = PAGES.map((p) => {
    const on = p.file === here ? ' class="on"' : "";
    return `<a${on} href="${p.file}">${p.label}</a>`;
  }).join("");
  mount.innerHTML =
    `<a class="appbar-logo" href="index.html">` +
    `<span class="w1">SMALL-CAP</span><span class="dot"></span><span class="w2">STACK</span>` +
    `</a><nav class="appbar-menu">${links}</nav>`;
}

renderNav();
