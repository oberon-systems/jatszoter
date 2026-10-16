"use strict";

// The replay page: asciinema-player over the cast `web` serves, and its markers as jump points.
(() => {
  const screen = document.querySelector("[data-player]");
  if (!screen) {
    return;
  }
  const failed = document.querySelector("[data-failed]");
  const rows = [...document.querySelectorAll("[data-seek]")];

  function mark(row) {
    for (const other of rows) {
      other.classList.toggle("current", other === row);
    }
  }

  async function load() {
    const response = await fetch(screen.dataset.cast, { cache: "no-store" });
    if (!response.ok) {
      throw new Error(`the cast answered ${response.status}`);
    }
    const text = await response.text();
    // A live recording may end inside the event being appended.
    return text.slice(0, text.lastIndexOf("\n") + 1);
  }

  load()
    .then((text) => {
      const player = AsciinemaPlayer.create({ data: text }, screen, {
        fit: "both",
        terminalFontFamily: '"Source Code Pro", ui-monospace, monospace',
      });
      player.addEventListener("marker", ({ index }) => mark(rows[index]));
      for (const row of rows) {
        const jump = () => {
          player.seek(Number(row.dataset.seek));
          mark(row);
        };
        row.addEventListener("click", jump);
        row.addEventListener("keydown", (event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            jump();
          }
        });
      }
    })
    .catch(() => {
      screen.hidden = true;
      failed.hidden = false;
    });
})();
