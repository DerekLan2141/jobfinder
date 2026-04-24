// ── State ──────────────────────────────────────────────────────────────────
let savedOnly      = false;
let searchQuery    = "";
let locationFilters = [];   // array of selected location strings
let industryFilter = "";
let resumeProfile  = JSON.parse(localStorage.getItem("resumeProfile") || "null");
let availableLocations = [];

// ── Helpers ────────────────────────────────────────────────────────────────
function debounce(fn, ms) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

function showStatus(msg, type) {
  const el = document.getElementById("status");
  el.textContent = msg;
  el.className = `status ${type}`;
  setTimeout(() => el.className = "status hidden", 4000);
}

// ── Match scoring ──────────────────────────────────────────────────────────
// Primary: server-side vector score (job.match_score 0-100).
// Fallback: keyword overlap when server hasn't scored yet.
function calcMatch(job, profile) {
  if (job.match_score !== undefined) return job.match_score;
  if (!profile?.skills?.length) return null;
  const skills = profile.skills.map(s => s.toLowerCase());
  const haystack = [job.title, job.description_snippet, job.matched_keywords, job.industry]
    .join(" ").toLowerCase();
  const hits = skills.filter(s => haystack.includes(s));
  return Math.round((hits.length / skills.length) * 100);
}

// ── Fetch & render jobs ────────────────────────────────────────────────────
async function fetchJobs() {
  const p = new URLSearchParams();
  if (savedOnly) p.set("saved", "true");
  if (searchQuery) p.set("search", searchQuery);
  if (locationFilters.length) p.set("location", locationFilters.join(","));
  if (industryFilter) p.set("industry", industryFilter);
  const res = await fetch(`/api/jobs?${p}`);
  return res.json();
}

function renderJobs(jobs) {
  const list = document.getElementById("jobList");
  list.innerHTML = "";

  if (!jobs.length) {
    list.innerHTML = '<p class="empty-msg">No jobs found. Click "Refresh Jobs" to scrape.</p>';
    return;
  }

  // Server already sorts by vector score when a profile is active.
  // Only re-sort client-side when server scores are absent (no profile on server).
  const hasServerScores = jobs.some(j => j.match_score !== undefined);
  if (resumeProfile && !hasServerScores) {
    jobs = [...jobs].sort((a, b) => calcMatch(b, resumeProfile) - calcMatch(a, resumeProfile));
  }

  const tpl = document.getElementById("jobCard");

  jobs.forEach(job => {
    const card = tpl.content.cloneNode(true).querySelector(".card");
    card.dataset.id = job.id;
    if (job.is_saved) card.classList.add("saved");

    card.querySelector(".card-title").textContent = job.title;
    card.querySelector(".card-title").href = job.url;
    card.querySelector(".card-company").textContent = job.company;
    card.querySelector(".card-location").textContent = job.location || "";
    card.querySelector(".card-date").textContent = job.date_posted ? `· ${job.date_posted}` : "";
    card.querySelector(".card-snippet").textContent = job.description_snippet || "";
    card.querySelector(".card-notes").value = job.notes || "";

    // Badge
    const badge = card.querySelector(".badge");
    badge.textContent = job.source;
    badge.classList.add(`badge-${job.source}`);

    // Industry
    const industryEl = card.querySelector(".card-industry");
    if (job.industry && job.industry !== "Other") {
      industryEl.textContent = job.industry;
      industryEl.classList.remove("hidden");
    }

    // Match score
    const matchEl = card.querySelector(".card-match");
    if (resumeProfile) {
      const score = calcMatch(job, resumeProfile);
      matchEl.textContent = `${score}% match`;
      matchEl.className = `card-match ${score >= 70 ? "match-high" : score >= 40 ? "match-mid" : "match-low"}`;
    }

    // Keywords
    const kwEl = card.querySelector(".card-keywords");
    (job.matched_keywords || "").split(",").filter(Boolean).forEach(kw => {
      const t = document.createElement("span");
      t.className = "kw-tag";
      t.textContent = kw.trim();
      kwEl.appendChild(t);
    });

    // Save button
    const saveBtn = card.querySelector(".btn-save");
    saveBtn.innerHTML = job.is_saved ? "&#9829;" : "&#9825;";
    if (job.is_saved) saveBtn.classList.add("saved");
    saveBtn.addEventListener("click", e => { e.stopPropagation(); toggleSave(job.id, card, saveBtn); });

    // Dismiss button
    card.querySelector(".btn-dismiss").addEventListener("click", e => { e.stopPropagation(); dismissJob(job.id, card); });

    // Notes
    const notes = card.querySelector(".card-notes");
    notes.addEventListener("click", e => e.stopPropagation());
    notes.addEventListener("blur", e => saveNotes(job.id, e.target.value));

    // Modal
    card.addEventListener("click", e => {
      if (e.target.closest(".btn-save, .btn-dismiss, .card-notes, .card-title")) return;
      openModal(job);
    });

    list.appendChild(card);
  });
}

async function load() {
  document.getElementById("jobList").innerHTML = '<p class="empty-msg">Loading jobs...</p>';
  const jobs = await fetchJobs();
  renderJobs(jobs);
}

// ── Actions ────────────────────────────────────────────────────────────────
async function toggleSave(id, card, btn) {
  const data = await (await fetch(`/api/jobs/${id}/save`, { method: "POST" })).json();
  btn.innerHTML = data.is_saved ? "&#9829;" : "&#9825;";
  btn.classList.toggle("saved", data.is_saved);
  card.classList.toggle("saved", data.is_saved);
  if (savedOnly && !data.is_saved) card.remove();
}

async function dismissJob(id, card) {
  await fetch(`/api/jobs/${id}/dismiss`, { method: "POST" });
  card.style.opacity = "0";
  card.style.transition = "opacity .3s";
  setTimeout(() => card.remove(), 300);
}

async function saveNotes(id, notes) {
  await fetch(`/api/jobs/${id}/notes`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ notes }),
  });
}

// ── Modal ──────────────────────────────────────────────────────────────────
function openModal(job) {
  document.getElementById("modalTitle").textContent = job.title;
  document.getElementById("modalCompany").textContent = job.company;
  document.getElementById("modalLocation").textContent = job.location || "";
  document.getElementById("modalLink").href = job.url;

  const badge = document.getElementById("modalBadge");
  badge.textContent = job.source;
  badge.className = `badge badge-${job.source}`;

  const indEl = document.getElementById("modalIndustry");
  if (job.industry && job.industry !== "Other") {
    indEl.textContent = job.industry;
    indEl.classList.remove("hidden");
  } else {
    indEl.classList.add("hidden");
  }

  if (job.ai_description) {
    document.getElementById("modalSalaryVal").textContent = job.ai_salary_estimate || "Not available";
    document.getElementById("modalDesc").textContent = job.ai_description;
  } else {
    document.getElementById("modalSalaryVal").textContent = "Analyzing...";
    document.getElementById("modalDesc").innerHTML = '<div class="ai-loading"><div class="spinner"></div>Analyzing with Gemini 2.5...</div>';
    fetchAnalysis(job.id);
  }

  document.getElementById("modal").classList.remove("hidden");
  document.body.style.overflow = "hidden";
}

function closeModal() {
  document.getElementById("modal").classList.add("hidden");
  document.body.style.overflow = "";
}

async function fetchAnalysis(jobId) {
  try {
    const data = await (await fetch(`/api/jobs/${jobId}/analyze`, { method: "POST" })).json();
    if (data.error) {
      document.getElementById("modalSalaryVal").textContent = "Unavailable";
      document.getElementById("modalDesc").innerHTML = `<div class="modal-error">${data.error}</div>`;
    } else {
      document.getElementById("modalSalaryVal").textContent = data.salary_estimate || "Not available";
      document.getElementById("modalDesc").textContent = data.description || "";
    }
  } catch {
    document.getElementById("modalDesc").innerHTML = '<div class="modal-error">Failed to connect to AI service.</div>';
  }
}

document.getElementById("modalClose").addEventListener("click", closeModal);
document.getElementById("modal").addEventListener("click", e => { if (e.target === document.getElementById("modal")) closeModal(); });
document.addEventListener("keydown", e => { if (e.key === "Escape") closeModal(); });

// ── Location multi-select ──────────────────────────────────────────────────
const locBox      = document.getElementById("locBox");
const locInput    = document.getElementById("locInput");
const locTags     = document.getElementById("locTags");
const locDropdown = document.getElementById("locDropdown");
const locPlaceholder = document.getElementById("locPlaceholder");

function renderLocTags() {
  locTags.innerHTML = locationFilters.map(loc =>
    `<span class="loc-tag">${loc}<button data-loc="${escHtml(loc)}">×</button></span>`
  ).join("");
  locTags.querySelectorAll("button").forEach(btn =>
    btn.addEventListener("click", e => { e.stopPropagation(); removeLocation(btn.dataset.loc); })
  );
  locPlaceholder.style.display = locationFilters.length ? "none" : "";
}

function escHtml(s) { return s.replace(/&/g,"&amp;").replace(/"/g,"&quot;"); }

function showLocDropdown(query = "") {
  const q = query.toLowerCase();
  const opts = availableLocations.filter(l => l.toLowerCase().includes(q) && !locationFilters.includes(l));
  if (!opts.length) { locDropdown.classList.add("hidden"); return; }
  locDropdown.innerHTML = opts.slice(0, 12).map(l => `<div class="loc-option" data-loc="${escHtml(l)}">${l}</div>`).join("");
  locDropdown.querySelectorAll(".loc-option").forEach(o =>
    o.addEventListener("mousedown", e => { e.preventDefault(); addLocation(o.dataset.loc); })
  );
  locDropdown.classList.remove("hidden");
}

function addLocation(loc) {
  if (!locationFilters.includes(loc)) {
    locationFilters.push(loc);
    renderLocTags();
    locInput.value = "";
    locDropdown.classList.add("hidden");
    load();
  }
}

function removeLocation(loc) {
  locationFilters = locationFilters.filter(l => l !== loc);
  renderLocTags();
  load();
}

locBox.addEventListener("click", () => locInput.focus());
locInput.addEventListener("focus", () => showLocDropdown(locInput.value));
locInput.addEventListener("input", () => showLocDropdown(locInput.value));
locInput.addEventListener("blur", () => setTimeout(() => locDropdown.classList.add("hidden"), 150));

// ── Filters ────────────────────────────────────────────────────────────────
document.getElementById("savedToggle").addEventListener("click", function () {
  savedOnly = !savedOnly;
  this.classList.toggle("active", savedOnly);
  load();
});

document.getElementById("industrySelect").addEventListener("change", e => {
  industryFilter = e.target.value;
  load();
});

document.getElementById("searchInput").addEventListener("input", debounce(e => {
  searchQuery = e.target.value.trim();
  load();
}, 300));

// ── Resume ─────────────────────────────────────────────────────────────────
function syncResumeUI() {
  const label = document.getElementById("resumeLabel");
  const tag   = document.getElementById("resumeTag");
  const clear = document.getElementById("resumeClear");
  if (resumeProfile) {
    label.textContent = "📄 Change Resume";
    label.classList.add("loaded");
    tag.textContent = `${resumeProfile.skills.length} skills`;
    tag.classList.remove("hidden");
    clear.classList.remove("hidden");
  } else {
    label.textContent = "📄 Upload Resume";
    label.classList.remove("loaded");
    tag.classList.add("hidden");
    clear.classList.add("hidden");
  }
}

document.getElementById("resumeInput").addEventListener("change", async e => {
  const file = e.target.files[0];
  if (!file) return;
  const label = document.getElementById("resumeLabel");
  label.textContent = "Analyzing...";
  label.classList.add("loading");

  const fd = new FormData();
  fd.append("resume", file);
  try {
    const res = await fetch("/api/resume/upload", { method: "POST", body: fd });
    const data = await res.json();
    if (data.error) { showStatus(`Resume error: ${data.error}`, "error"); }
    else {
      resumeProfile = data;
      localStorage.setItem("resumeProfile", JSON.stringify(data));
      showStatus(`Resume analyzed — ${data.skills.length} skills detected. Jobs sorted by match.`, "success");
      load();
    }
  } catch { showStatus("Failed to analyze resume.", "error"); }
  finally {
    label.classList.remove("loading");
    syncResumeUI();
    e.target.value = "";
  }
});

document.getElementById("resumeClear").addEventListener("click", async () => {
  resumeProfile = null;
  localStorage.removeItem("resumeProfile");
  syncResumeUI();
  await fetch("/api/resume", { method: "DELETE" });
  load();
});

// ── Refresh button ─────────────────────────────────────────────────────────
document.getElementById("scrapeBtn").addEventListener("click", async function () {
  this.disabled = true;
  this.textContent = "Scraping...";
  showStatus("Scraping jobs — this may take a minute...", "");
  try {
    await fetch("/api/scrape", { method: "POST" });
    showStatus("Done! Jobs updated.", "success");
    await loadFilters();
    load();
  } catch { showStatus("Scrape failed.", "error"); }
  finally { this.disabled = false; this.textContent = "Refresh Jobs"; }
});

// ── Init ───────────────────────────────────────────────────────────────────
async function loadFilters() {
  try {
    const data = await (await fetch("/api/filters")).json();
    availableLocations = data.locations || [];

    const sel = document.getElementById("industrySelect");
    const cur = sel.value;
    sel.innerHTML = '<option value="">All Industries</option>';
    (data.industries || []).forEach(ind => {
      const o = document.createElement("option");
      o.value = ind; o.textContent = ind;
      if (ind === cur) o.selected = true;
      sel.appendChild(o);
    });
  } catch {}
}

syncResumeUI();
loadFilters();
load();
