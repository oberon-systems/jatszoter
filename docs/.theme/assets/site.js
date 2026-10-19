// A tap on the dimmed page closes the mobile menu.
document.addEventListener("click", (event) => {
  const page = document.querySelector("section.wy-nav-content-wrap.shift");
  if (page && page.contains(event.target)) {
    event.preventDefault();
    document
      .querySelectorAll("[data-toggle='wy-nav-shift'], [data-toggle='rst-versions']")
      .forEach((node) => node.classList.remove("shift"));
  }
});
