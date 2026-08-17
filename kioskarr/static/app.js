// Monitored toggle: PATCH in place, no full page reload.
document.addEventListener("change", async (e) => {
  const el = e.target;
  if (!el.matches("[data-toggle-monitored]")) return;
  const id = el.dataset.toggleMonitored;
  try {
    const res = await fetch(`/publications/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ monitored: el.checked }),
    });
    if (!res.ok) throw new Error(await res.text());
  } catch (err) {
    el.checked = !el.checked; // revert on failure
    alert("Failed to update: " + err.message);
  }
});

// Delete confirmation for anything marked data-confirm.
document.addEventListener("submit", (e) => {
  const form = e.target;
  if (form.matches("[data-confirm]") && !confirm(form.dataset.confirm)) {
    e.preventDefault();
  }
});

// Repeatable alias inputs on the publication form.
function addAliasRow(value) {
  const container = document.getElementById("alias-rows");
  if (!container) return;
  const row = document.createElement("div");
  row.className = "alias-row";
  row.innerHTML =
    '<input type="text" name="aliases" value="' +
    (value ? value.replace(/"/g, "&quot;") : "") +
    '" placeholder="Alternate name uploaders use" />' +
    '<button type="button" class="btn btn-sm" onclick="this.parentElement.remove()">Remove</button>';
  container.appendChild(row);
}

document.addEventListener("click", (e) => {
  if (e.target.matches("[data-add-alias]")) {
    e.preventDefault();
    addAliasRow("");
  }
});
