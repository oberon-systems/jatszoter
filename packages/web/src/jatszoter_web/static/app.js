"use strict";

// Dialogs arrive as HTMX fragments: each opens modal, and is dropped once closed.
document.addEventListener("htmx:load", (event) => {
  const root = event.detail.elt;
  const dialogs = root.matches("dialog") ? [root] : root.querySelectorAll("dialog");
  for (const dialog of dialogs) {
    dialog.addEventListener("close", () => dialog.remove());
    if (!dialog.open) {
      dialog.showModal();
    }
  }
});

document.addEventListener("htmx:afterSwap", (event) => {
  if (event.detail.target.id === "flashes") {
    document.querySelector("dialog[open]")?.close();
  }
});

document.addEventListener("click", (event) => {
  const closer = event.target.closest("[data-close]");
  if (closer) {
    closer.closest("dialog")?.close();
  }
  const copier = event.target.closest("[data-copy]");
  if (copier) {
    const source = document.getElementById(copier.dataset.copy);
    navigator.clipboard?.writeText(source.value);
  }
});
