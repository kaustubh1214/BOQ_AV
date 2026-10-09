// Applies the saved/OS theme before React renders (avoids a light→dark flash). Kept external for a strict CSP.
try {
  var t = localStorage.getItem("boq-theme");
  if (t === "dark" || (!t && window.matchMedia("(prefers-color-scheme: dark)").matches)) document.documentElement.classList.add("dark");
} catch (e) {}
