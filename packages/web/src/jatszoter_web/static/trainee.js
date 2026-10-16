"use strict";

// The trainee page: the task, the terminal and the checks, read through the relay with the link.
(() => {
  const page = document.querySelector("[data-trainee]");
  if (!page) {
    return;
  }
  const base = `/api/v1/deployments/${page.dataset.deployment}`;
  const token = document.querySelector('meta[name="csrf-token"]').content;
  const terminal = page.querySelector("[data-terminal]");
  const element = (name) => page.querySelector(`[data-${name}]`);
  const request = element("request");
  const control = element("control");
  const checksBody = element("checks");
  const emptyRow = checksBody.firstElementChild.cloneNode(true);
  const POLL = 5000;
  const TONES = {
    PENDING: "neutral",
    STARTING: "info",
    BOOTSTRAPPING: "info",
    READY: "success",
    STOPPING: "warning",
    COMPLETED: "neutral",
    FAILED: "danger",
    CANCELLED: "neutral",
    pending: "info",
    passed: "success",
    failed: "danger",
    error: "warning",
    timeout: "warning",
  };
  const DOWN = {
    502: "The API cannot be reached. Nothing was changed; try again in a moment. (502)",
    504: "The API did not answer in time. Try again in a moment. (504)",
  };
  const HOLDERS = {
    trainee: { operator: "operator", trainee: "you" },
    operator: { operator: "you (operator)", trainee: "trainee" },
    viewer: { operator: "operator", trainee: "trainee" },
  };
  let task = null;
  let checks = [];
  let started = false;
  let over = false;
  let asking = false;
  let failing = false;

  function badge(word) {
    const span = document.createElement("span");
    span.className = `badge badge-${TONES[word] || "neutral"}`;
    span.textContent = word;
    return span;
  }

  function span(text, className) {
    const item = document.createElement("span");
    item.textContent = text;
    if (className) {
      item.className = className;
    }
    return item;
  }

  function utc(timestamp) {
    return new Date(timestamp * 1000).toISOString().slice(0, 19).replace("T", " ");
  }

  function flash(kind, title, text) {
    const slot = document.getElementById("flashes");
    if (kind === null) {
      slot.replaceChildren();
      return;
    }
    const box = document.createElement("div");
    box.className = `flash flash-${kind}`;
    box.setAttribute("role", "alert");
    const icon = span("", "flash-icon");
    icon.setAttribute("aria-hidden", "true");
    const words = document.createElement("div");
    words.className = "flash-text";
    words.append(span(title, "flash-title"), span(text));
    box.append(icon, words);
    slot.replaceChildren(box);
  }

  function failed(status) {
    const text = DOWN[status] || `The API failed to answer. Try again in a moment. (${status})`;
    failing = true;
    flash("error", "Something went wrong", text);
  }

  async function call(method, path, body) {
    const headers = { Accept: "application/json" };
    const init = { method, headers, credentials: "same-origin" };
    if (method === "POST") {
      headers["X-CSRF-Token"] = token;
      if (body !== undefined) {
        headers["Content-Type"] = "application/json";
        init.body = JSON.stringify(body);
      }
    }
    try {
      return await fetch(base + path, init);
    } catch {
      return { ok: false, status: 502 };
    }
  }

  async function detail(response) {
    try {
      const answer = await response.json();
      return typeof answer.detail === "string" ? answer.detail : "the request was refused";
    } catch {
      return "the request was refused";
    }
  }

  function linkEnded() {
    over = true;
    flash(null);
    page.hidden = true;
    document.querySelector("[data-ended]").hidden = false;
  }

  function showTask() {
    element("title").textContent = task.title;
    element("task-text").textContent = task.description;
    element("status").replaceChildren(badge(task.status));
    if (task.session_ends_at === null) {
      element("time-left").textContent = "session starts at READY";
    } else {
      const minutes = Math.max(0, Math.ceil((task.session_ends_at * 1000 - Date.now()) / 60000));
      element("time-left").textContent = `${minutes} min left`;
    }
    element("check-bar").hidden = task.role === "viewer";
    page.hidden = false;
    if (task.status === "READY" && !started) {
      started = true;
      terminal.dataset.role = task.role;
      terminal.dataset.holderOperator = HOLDERS[task.role].operator;
      terminal.dataset.holderTrainee = HOLDERS[task.role].trainee;
      element("preparing").hidden = true;
      element("screen").hidden = false;
      if (task.role !== "operator") {
        control.remove();
      }
      terminal.dispatchEvent(new CustomEvent("terminal:start", { bubbles: true }));
    }
  }

  function showChecks() {
    const newest = checks[0];
    const pending = newest !== undefined && newest.status === "pending";
    request.disabled = asking || pending || task.status !== "READY";
    const last = element("last");
    if (newest === undefined) {
      last.replaceChildren(span("none yet", "secondary"));
      checksBody.replaceChildren(emptyRow.cloneNode(true));
      return;
    }
    last.replaceChildren(
      badge(newest.status),
      span(message(newest)),
      span(utc(newest.created_at).slice(11), "mono-small secondary"),
    );
    checksBody.replaceChildren(
      ...checks.map((check) => {
        const row = document.createElement("tr");
        const cells = [badge(check.status), span(utc(check.created_at)), span(message(check))];
        cells.forEach((cell, index) => {
          const td = document.createElement("td");
          td.append(cell);
          if (index === 1) {
            td.className = "mono-small";
          } else if (index === 2) {
            td.className = "left";
          }
          row.append(td);
        });
        return row;
      }),
    );
  }

  function message(check) {
    return check.message || (check.status === "pending" ? "waiting for the agent" : "-");
  }

  async function refresh() {
    const answer = await call("GET", "/task");
    if (answer.status === 401 || answer.status === 403) {
      linkEnded();
      return;
    }
    if (!answer.ok) {
      failed(answer.status);
      return;
    }
    task = await answer.json();
    showTask();
    const listed = await call("GET", "/checks");
    if (listed.ok) {
      checks = (await listed.json()).reverse();
      showChecks();
      if (failing) {
        failing = false;
        flash(null);
      }
    } else if (listed.status === 401 || listed.status === 403) {
      linkEnded();
    } else {
      failed(listed.status);
    }
  }

  async function poll() {
    await refresh();
    if (!over) {
      setTimeout(poll, POLL);
    }
  }

  async function refused(response) {
    if (response.status === 401) {
      linkEnded();
    } else if (response.status >= 500) {
      failed(response.status);
    } else {
      let text = await detail(response);
      if (text.endsWith("has a pending check")) {
        text = "a check is already pending";
      }
      flash("refusal", "Refused", `${text} (${response.status})`);
    }
  }

  request.addEventListener("click", async () => {
    asking = true;
    request.disabled = true;
    const response = await call("POST", "/checks");
    asking = false;
    if (response.ok) {
      flash(null);
      checks.unshift(await response.json());
      showChecks();
    } else {
      await refused(response);
      showChecks();
    }
  });

  control.addEventListener("click", async () => {
    const response = await call("POST", "/control", { holder: control.dataset.ask });
    if (!response.ok) {
      await refused(response);
    }
  });

  terminal.addEventListener("terminal:closed", (event) => {
    if (event.detail === 4403) {
      linkEnded();
    } else {
      refresh();
    }
  });

  poll();
})();
