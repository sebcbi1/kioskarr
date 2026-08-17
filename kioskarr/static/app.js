// Kioskarr SPA — Alpine.js data/methods, talking directly to the JSON API.
// Hash-based routing (no server-side routes beyond "/"): #/publications,
// #/publications/new, #/publications/:id/edit, #/review, #/grabs, #/settings.

function parseHash() {
  const parts = window.location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  if (parts.length === 0) return { name: "publications" };
  if (parts[0] === "publications") {
    if (parts[1] === "new") return { name: "publication-form", id: null };
    if (parts[1] && parts[2] === "edit") return { name: "publication-form", id: Number(parts[1]) };
    return { name: "publications" };
  }
  if (parts[0] === "review") return { name: "review" };
  if (parts[0] === "grabs") return { name: "grabs" };
  if (parts[0] === "settings") return { name: "settings" };
  return { name: "publications" };
}

async function apiFetch(url, options = {}) {
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    let message = res.statusText;
    try {
      const body = await res.json();
      message = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body);
    } catch (_) {
      // response wasn't JSON — keep statusText
    }
    throw new Error(message);
  }
  if (res.status === 204) return null;
  return res.json();
}

function app() {
  return {
    route: parseHash(),
    toast: { message: "", type: "success" },
    _toastTimer: null,

    publications: [],
    reviewItems: [],
    grabs: [],
    grabStatusFilter: "",
    settings: null,

    form: {},

    init() {
      window.addEventListener("hashchange", () => {
        this.route = parseHash();
        this.onRouteEnter();
      });
      this.onRouteEnter();
    },

    onRouteEnter() {
      if (this.route.name === "publications") return this.loadPublications();
      if (this.route.name === "publication-form") return this.enterPublicationForm(this.route.id);
      if (this.route.name === "review") return this.loadReviewData();
      if (this.route.name === "grabs") return this.loadGrabs();
      if (this.route.name === "settings") return this.loadSettings();
    },

    navigate(hash) {
      window.location.hash = hash;
    },

    showToast(message, type = "success") {
      this.toast = { message, type };
      clearTimeout(this._toastTimer);
      this._toastTimer = setTimeout(() => {
        this.toast = { message: "", type: "success" };
      }, 4000);
    },

    // --- Publications ---
    async loadPublications() {
      try {
        this.publications = await apiFetch("/publications");
      } catch (e) {
        this.showToast(`Failed to load publications: ${e.message}`, "error");
      }
    },

    async toggleMonitored(pub) {
      const previous = pub.monitored;
      pub.monitored = !previous;
      try {
        await apiFetch(`/publications/${pub.id}`, {
          method: "PATCH",
          body: JSON.stringify({ monitored: pub.monitored }),
        });
      } catch (e) {
        pub.monitored = previous;
        this.showToast(`Failed to update: ${e.message}`, "error");
      }
    },

    async searchNow(pub) {
      try {
        const result = await apiFetch(`/publications/${pub.id}/search-now`, { method: "POST" });
        this.showToast(`${pub.title}: grabbed ${result.grabbed} release(s)`, "success");
      } catch (e) {
        this.showToast(`Search failed: ${e.message}`, "error");
      }
    },

    async deletePublication(pub) {
      if (!confirm(`Delete "${pub.title}"? This cannot be undone.`)) return;
      try {
        await apiFetch(`/publications/${pub.id}`, { method: "DELETE" });
        this.publications = this.publications.filter((p) => p.id !== pub.id);
        this.showToast("Publication deleted", "success");
      } catch (e) {
        this.showToast(`Delete failed: ${e.message}`, "error");
      }
    },

    // --- Publication form (create + edit) ---
    async enterPublicationForm(id) {
      if (id) {
        try {
          const pub = await apiFetch(`/publications/${id}`);
          this.form = { ...pub, aliases: [...pub.aliases] };
        } catch (e) {
          this.showToast(`Failed to load publication: ${e.message}`, "error");
          this.navigate("#/publications");
        }
      } else {
        this.form = {
          id: null,
          title: "",
          type: "magazine",
          aliases: [],
          format_preference: "any",
          min_seeders: 1,
          target_dir: "",
          grab_last_n: 1,
          monitored: true,
          baseline_identifier: null,
        };
      }
    },

    addAliasRow() {
      this.form.aliases.push("");
    },

    removeAliasRow(index) {
      this.form.aliases.splice(index, 1);
    },

    async savePublication() {
      const aliases = this.form.aliases.map((a) => a.trim()).filter(Boolean);
      const common = {
        title: this.form.title,
        aliases,
        format_preference: this.form.format_preference,
        min_seeders: Number(this.form.min_seeders),
        target_dir: this.form.target_dir,
        grab_last_n: Number(this.form.grab_last_n),
        monitored: this.form.monitored,
      };
      try {
        if (this.form.id) {
          await apiFetch(`/publications/${this.form.id}`, { method: "PATCH", body: JSON.stringify(common) });
          this.showToast("Publication saved", "success");
        } else {
          await apiFetch("/publications", { method: "POST", body: JSON.stringify({ ...common, type: this.form.type }) });
          this.showToast("Publication added", "success");
        }
        this.navigate("#/publications");
      } catch (e) {
        this.showToast(`Save failed: ${e.message}`, "error");
      }
    },

    async resetBaseline() {
      if (!confirm("Reset the cold-start baseline? The next search will re-evaluate from scratch.")) return;
      try {
        const updated = await apiFetch(`/publications/${this.form.id}`, {
          method: "PATCH",
          body: JSON.stringify({ baseline_identifier: null }),
        });
        this.form.baseline_identifier = updated.baseline_identifier;
        this.showToast("Baseline reset", "success");
      } catch (e) {
        this.showToast(`Reset failed: ${e.message}`, "error");
      }
    },

    // --- Review queue ---
    async loadReviewData() {
      try {
        const [items, pubs] = await Promise.all([apiFetch("/review"), apiFetch("/publications")]);
        this.publications = pubs;
        // Options for each item's <select> are rendered via x-for after this
        // assignment; if _publicationId were pre-filled here, x-model would try
        // to select a value before its matching <option> exists in the DOM and
        // silently fail (browser falls back to the first option). Leave it
        // blank now and fill it in on the next tick, once the <option>s exist.
        this.reviewItems = items.map((item) => ({ ...item, _publicationId: "", _identifier: "", _filePath: "" }));
        await this.$nextTick();
        this.reviewItems.forEach((item) => {
          if (item.candidate_publication_id) item._publicationId = item.candidate_publication_id;
        });
      } catch (e) {
        this.showToast(`Failed to load review queue: ${e.message}`, "error");
      }
    },

    async resolveReviewItem(item) {
      const payload = {
        publication_id: Number(item._publicationId),
        identifier: item._identifier,
        file_path: item._filePath || null,
      };
      try {
        await apiFetch(`/review/${item.id}/resolve`, { method: "POST", body: JSON.stringify(payload) });
        this.reviewItems = this.reviewItems.filter((i) => i.id !== item.id);
        this.showToast("Review item resolved", "success");
      } catch (e) {
        this.showToast(`Resolve failed: ${e.message}`, "error");
      }
    },

    // --- Grabs ---
    async loadGrabs() {
      try {
        const query = this.grabStatusFilter ? `?status=${this.grabStatusFilter}` : "";
        this.grabs = await apiFetch(`/grabs${query}`);
      } catch (e) {
        this.showToast(`Failed to load grabs: ${e.message}`, "error");
      }
    },

    // --- Settings ---
    async loadSettings() {
      try {
        this.settings = await apiFetch("/settings");
      } catch (e) {
        this.showToast(`Failed to load settings: ${e.message}`, "error");
      }
    },

    async saveSettings() {
      try {
        this.settings = await apiFetch("/settings", { method: "PATCH", body: JSON.stringify(this.settings) });
        this.showToast("Settings saved", "success");
      } catch (e) {
        this.showToast(`Save failed: ${e.message}`, "error");
      }
    },
  };
}
