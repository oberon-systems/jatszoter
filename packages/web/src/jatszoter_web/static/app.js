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

// Password fields carry the server's rule, so a weak or mismatched one never leaves the browser.
const CLASSES = [/\p{Ll}/u, /\p{Lu}/u, /\p{Nd}/u, /[^\p{L}\p{N}]/u];

function passwordProblem(input) {
  if (input.dataset.password !== undefined) {
    const mixed = CLASSES.filter((pattern) => pattern.test(input.value)).length;
    return [...input.value].length < input.minLength || mixed < 3 ? input.dataset.password : "";
  }
  const password = input.form.elements[input.dataset.repeat];
  return input.value !== password.value ? input.dataset.differ : "";
}

function showProblem(input, message) {
  const field = input.closest(".field");
  input.classList.toggle("invalid", Boolean(message));
  field.querySelector(".field-error")?.remove();
  const hint = field.querySelector(".field-hint");
  if (hint) {
    hint.hidden = Boolean(message);
  }
  if (message) {
    const error = document.createElement("span");
    error.className = "field-error";
    error.textContent = message;
    field.append(error);
  }
}

function passwordInputs(form) {
  return form.querySelectorAll("input[data-password], input[data-repeat]");
}

document.addEventListener(
  "click",
  (event) => {
    const form = event.target.closest("button[type=submit]")?.form;
    for (const input of form ? passwordInputs(form) : []) {
      input.setCustomValidity(passwordProblem(input));
    }
  },
  true,
);

document.addEventListener(
  "invalid",
  (event) => {
    const input = event.target;
    if (input.matches("input[data-password], input[data-repeat]")) {
      event.preventDefault();
      showProblem(input, passwordProblem(input) || input.validationMessage);
    }
  },
  true,
);

document.addEventListener("input", (event) => {
  const form = event.target.form;
  for (const input of form ? passwordInputs(form) : []) {
    input.setCustomValidity("");
    if (input.classList.contains("invalid")) {
      showProblem(input, passwordProblem(input));
    }
  }
});
