// The Magpie app's reminder pop-ups: one card per reminder, with Done, Snooze and Open.
//
// The app (Python) adds and removes cards with magpieAlerts.add/remove; the buttons reach
// the app through Magpie's own API on this computer (/api/desktop/ui).

const list = document.getElementById("alerts");

function call(action, details = {}) {
  return fetch("/api/desktop/ui", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Magpie": "1" },
    body: JSON.stringify({ window: "alerts", action, ...details }),
  }).then(async (response) => {
    const answer = await response.json().catch(() => ({}));
    if (!response.ok) return answer.detail || "Magpie couldn't do that.";
    return answer.problem || null;
  }).catch(() => "Magpie isn't answering. Try again in a moment.");
}

function el(tag, props = {}, ...children) {
  const node = Object.assign(document.createElement(tag), props);
  for (const child of children) if (child !== null && child !== undefined && child !== "") node.append(child);
  return node;
}

function button(label, action, key, primary = false) {
  const b = el("button", { type: "button", textContent: label, className: primary ? "primary" : "" });
  b.addEventListener("click", async () => {
    const card = b.closest(".alert");
    card.querySelectorAll("button").forEach((x) => { x.disabled = true; });
    const problem = await call("act", { key, text: action });
    if (problem) {
      card.querySelectorAll("button").forEach((x) => { x.disabled = false; });
      card.querySelector(".error").textContent = problem;
    }
  });
  return b;
}

function report() {
  // The window grows and shrinks with the cards, anchored to the corner.
  call("resize", { height: Math.ceil(list.getBoundingClientRect().height) });
}

// An icon from Magpie's own set, for decoration.
function symbol(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "icon");
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `/static/icons/sprite.svg#${name}`);
  svg.append(use);
  return svg;
}

window.magpieAlerts = {
  add({ key, title = "Reminder", body = "", due = "", kind = "reminder", snooze = "1 hour", reminder = true } = {}) {
    window.magpieAlerts.remove(key);
    const close = button("✕", "dismiss", key);
    close.setAttribute("aria-label", "Close");
    close.className = "quiet close";
    const head = el("div", { className: "head" },
      el("span", { textContent: kind === "digest" ? "Your digest" : kind !== "reminder" ? "Magpie" : "Reminder" }),
      kind === "reminder" && due ? el("span", { className: "due", textContent: `due ${due}` }) : null,
      close);
    const problem = el("p", { className: "error" });
    problem.setAttribute("role", "alert");
    const badge = el("span", { className: "badge" }, symbol(kind === "digest" ? "sun" : kind === "reminder" ? "bell" : "sparkles"));
    const card = el("article", { className: `alert ${kind}` },
      badge,
      el("div", { className: "body" },
        head,
        el("h1", { textContent: title, dir: "auto" }),
        body ? el("p", { textContent: body, dir: "auto" }) : null,
        el("div", { className: "row" },
          reminder ? button("Done", "done", key, true) : null,
          reminder ? button(`Snooze ${snooze}`, "snooze", key) : null,
          button("Open", "open", key)),
        problem));
    card.dataset.key = key;
    list.prepend(card);
    report();
  },
  remove(key) {
    for (const card of list.querySelectorAll(".alert")) if (card.dataset.key === String(key)) card.remove();
    report();
  },
};

call("ready"); // the app sends the reminders that are waiting
