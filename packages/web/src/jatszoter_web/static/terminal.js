"use strict";

// The console terminal of the deployment detail, the trainee page and the console log page.
(() => {
  const THEME = {
    background: "#0d1117",
    foreground: "#e6edf3",
    cursor: "#e6edf3",
    cursorAccent: "#0d1117",
  };
  const PAUSES = [1000, 2000, 4000, 8000, 16000, 30000];
  // Close codes the page reconnects after; any other is final.
  const RETRIED = new Set([1006, 1011, 4429]);
  const encoder = new TextEncoder();

  function open(screen, readOnly) {
    const term = new Terminal({
      cursorBlink: !readOnly,
      disableStdin: true,
      fontFamily: '"Source Code Pro", ui-monospace, monospace',
      fontSize: 13,
      lineHeight: 1.2,
      scrollback: readOnly ? 100000 : 5000,
      theme: THEME,
    });
    term.open(screen);
    return term;
  }

  // Resize to fill the screen element, from the cell size xterm measured itself.
  function fit(term, screen) {
    const measure = term.element.querySelector(".xterm-char-measure-element");
    const row = term.element.querySelector(".xterm-rows > div");
    const width = measure ? measure.getBoundingClientRect().width / measure.textContent.length : 0;
    const height = row ? row.getBoundingClientRect().height : 0;
    if (!width || !height) {
      return;
    }
    const cols = Math.max(2, Math.floor(screen.clientWidth / width));
    const rows = Math.max(1, Math.floor(screen.clientHeight / height));
    if (cols !== term.cols || rows !== term.rows) {
      term.resize(cols, rows);
    }
  }

  function attach(box) {
    const role = box.dataset.role;
    const screen = box.querySelector("[data-screen]");
    const holderText = box.querySelector("[data-holder]");
    const connection = box.querySelector("[data-connection]");
    const button = box.querySelector("[data-control]");
    const notice = box.querySelector("[data-notice]");
    const term = open(screen, false);
    let socket = null;
    let position = null;
    let holder = null;
    let failures = 0;

    function show(state) {
      connection.className = `connection ${state}`;
      connection.textContent = state;
    }

    function sendSize() {
      if (socket && socket.readyState === WebSocket.OPEN && holder === role) {
        socket.send(JSON.stringify({ cols: term.cols, rows: term.rows }));
      }
    }

    function control(next) {
      holder = next;
      holderText.textContent = next === "operator" ? box.dataset.holderOperator : box.dataset.holderTrainee;
      term.options.disableStdin = holder !== role;
      if (notice) {
        notice.hidden = holder === role;
      }
      if (button) {
        const ask = holder === "operator" ? "trainee" : "operator";
        button.textContent = ask === "operator" ? "Take control" : "Give back";
        button.dataset.ask = ask;
        button.setAttribute("hx-vals", JSON.stringify({ holder: ask }));
        button.hidden = false;
      }
      sendSize();
    }

    function ended(code) {
      holder = null;
      holderText.textContent = "nobody";
      term.options.disableStdin = true;
      if (button) {
        button.hidden = true;
      }
      if (notice) {
        notice.hidden = true;
      }
      show("closed");
      box.dispatchEvent(new CustomEvent("terminal:closed", { bubbles: true, detail: code }));
    }

    function receive(event) {
      if (typeof event.data === "string") {
        const frame = JSON.parse(event.data);
        if ("offset" in frame) {
          // An offset below what is shown means the API sent its tail again: draw it afresh.
          if (position !== null && frame.offset < position) {
            term.reset();
          }
          position = frame.offset;
        }
        if ("control" in frame) {
          control(frame.control);
        }
        return;
      }
      const bytes = new Uint8Array(event.data);
      term.write(bytes);
      position += bytes.length;
    }

    function connect() {
      const scheme = location.protocol === "https:" ? "wss:" : "ws:";
      const query = position === null ? "" : `?offset=${position}`;
      socket = new WebSocket(`${scheme}//${location.host}${box.dataset.socket}${query}`);
      socket.binaryType = "arraybuffer";
      socket.addEventListener("open", () => {
        failures = 0;
        show("connected");
      });
      socket.addEventListener("message", receive);
      socket.addEventListener("close", (event) => {
        socket = null;
        if (!RETRIED.has(event.code)) {
          ended(event.code);
          return;
        }
        show("reconnecting");
        setTimeout(connect, PAUSES[Math.min(failures, PAUSES.length - 1)]);
        failures += 1;
      });
    }

    function send(bytes) {
      if (socket && socket.readyState === WebSocket.OPEN && holder === role) {
        socket.send(bytes);
      }
    }

    term.onData((data) => send(encoder.encode(data)));
    term.onBinary((data) => send(Uint8Array.from(data, (char) => char.charCodeAt(0))));
    term.onResize(sendSize);
    window.addEventListener("resize", () => fit(term, screen));
    document.fonts.ready.then(() => {
      fit(term, screen);
      connect();
    });
  }

  async function replay(box) {
    const screen = box.querySelector("[data-screen]");
    const term = open(screen, true);
    await document.fonts.ready;
    fit(term, screen);
    window.addEventListener("resize", () => fit(term, screen));
    const response = await fetch(box.dataset.log, { credentials: "same-origin" });
    if (response.headers.get("content-type") !== "application/octet-stream") {
      // A refusal lands on the page that shows it.
      location.assign(response.url);
      return;
    }
    term.write(new Uint8Array(await response.arrayBuffer()));
  }

  document.addEventListener("terminal:start", (event) => attach(event.target));
  for (const box of document.querySelectorAll("[data-terminal]")) {
    if (box.dataset.log) {
      replay(box);
    } else if (box.dataset.socket && !("deferred" in box.dataset)) {
      attach(box);
    }
  }
})();
