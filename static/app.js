// ── State ──────────────────────────────────────────────────────────────────
let profile        = null;
let savedOnly      = false;
let searchQuery    = "";
let locationFilter = "";
let industryFilter = "";

// ── Helpers ────────────────────────────────────────────────────────────────
function debounce(fn, ms) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

function showStatus(msg, type = "") {
  const el = document.getElementById("status");
  el.textContent = msg;
  el.className = `status ${type}`;
  if (type !== "loading") setTimeout(() => el.className = "status hidden", 5000);
}

function hideStatus() {
  document.getElementById("status").className = "status hidden";
}

function escHtml(s) {
  return s.replace(/&/g, "&amp;").replace(/"/g, "&quot;");
}

// ── Profile UI ─────────────────────────────────────────────────────────────
function syncProfileUI() {
  const prompt    = document.getElementById("uploadPrompt");
  const bar       = document.getElementById("profileBar");
  const toolbar   = document.getElementById("toolbar");
  const jobList   = document.getElementById("jobList");
  const label     = document.getElementById("resumeLabel");
  const clearBtn  = document.getElementById("resumeClear");

  if (profile) {
    prompt.classList.add("hidden");
    bar.classList.remove("hidden");
    toolbar.classList.remove("hidden");

    const titles = (profile.job_titles || []).slice(0, 3).join(", ");
    document.getElementById("profileTitles").textContent = titles || "Your profile";
    document.getElementById("profileSkillCount").textContent =
      profile.skills?.length ? `· ${profile.skills.length} skills detected` : "";

    label.textContent = "&#128196; Change Resume";
    clearBtn.classList.remove("hidden");
  } else {
    prompt.classList.remove("hidden");
    bar.classList.add("hidden");
    toolbar.classList.add("hidden");
    jobList.innerHTML = "";

    label.textContent = "&#128196; Upload Resume";
    clearBtn.classList.add("hidden");
  }
}

// ── Jobs ───────────────────────────────────────────────────────────────────
async function fetchJobs() {
  const p = new URLSearchParams();
  if (savedOnly) p.set("saved", "true");
  if (searchQuery) p.set("search", searchQuery);
  if (locationFilter) p.set("location", locationFilter);
  if (industryFilter) p.set("industry", industryFilter);
  const res = await fetch(`/api/jobs?${p}`);
  return res.json();
}

function renderJobs(jobs) {
  const list = document.getElementById("jobList");
  list.innerHTML = "";

  if (!jobs.length) {
    list.innerHTML = '<p class="empty-msg">No jobs found.</p>';
    return;
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
    card.querySelector(".card-salary").textContent = job.salary || "";
    card.querySelector(".card-snippet").textContent = job.description_snippet || "";
    card.querySelector(".card-notes").value = job.notes || "";

    // Industry tag
    const indEl = card.querySelector(".card-industry");
    if (job.industry && job.industry !== "Other") {
      indEl.textContent = job.industry;
      indEl.classList.remove("hidden");
    }

    // Match score
    if (job.match_score !== undefined) {
      const matchEl = card.querySelector(".card-match");
      matchEl.textContent = `${job.match_score}% match`;
      matchEl.className = `card-match ${
        job.match_score >= 60 ? "match-high" :
        job.match_score >= 30 ? "match-mid" : "match-low"
      }`;
    }

    // Save button
    const saveBtn = card.querySelector(".btn-save");
    saveBtn.innerHTML = job.is_saved ? "&#9829;" : "&#9825;";
    if (job.is_saved) saveBtn.classList.add("saved");
    saveBtn.addEventListener("click", e => { e.stopPropagation(); toggleSave(job.id, card, saveBtn); });

    // Dismiss
    card.querySelector(".btn-dismiss").addEventListener("click", e => {
      e.stopPropagation(); dismissJob(job.id, card);
    });

    // Notes
    const notes = card.querySelector(".card-notes");
    notes.addEventListener("click", e => e.stopPropagation());
    notes.addEventListener("blur", e => saveNotes(job.id, e.target.value));

    // Modal on card click
    card.addEventListener("click", e => {
      if (e.target.closest(".btn-save, .btn-dismiss, .card-notes, .card-title")) return;
      openModal(job);
    });

    list.appendChild(card);
  });
}

async function load() {
  if (!profile) return;
  document.getElementById("jobList").innerHTML = '<p class="empty-msg">Loading jobs...</p>';
  const jobs = await fetchJobs();
  renderJobs(jobs);
  await loadFilters();
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
  document.getElementById("modalTitle").textContent    = job.title;
  document.getElementById("modalCompany").textContent  = job.company;
  document.getElementById("modalLocation").textContent = job.location || "";
  document.getElementById("modalSalaryInline").textContent = job.salary || "";
  document.getElementById("modalLink").href = job.url;

  const indEl = document.getElementById("modalIndustry");
  if (job.industry && job.industry !== "Other") {
    indEl.textContent = job.industry;
    indEl.classList.remove("hidden");
  } else {
    indEl.classList.add("hidden");
  }

  // Show description directly — no AI call
  const desc = job.description_snippet || "No description available.";
  document.getElementById("modalDesc").textContent = desc;

  document.getElementById("modal").classList.remove("hidden");
  document.body.style.overflow = "hidden";
}

function closeModal() {
  document.getElementById("modal").classList.add("hidden");
  document.body.style.overflow = "";
}

document.getElementById("modalClose").addEventListener("click", closeModal);
document.getElementById("modal").addEventListener("click", e => {
  if (e.target === document.getElementById("modal")) closeModal();
});
document.addEventListener("keydown", e => { if (e.key === "Escape") closeModal(); });

// ── Filters ────────────────────────────────────────────────────────────────
document.getElementById("locationSelect").addEventListener("change", e => {
  locationFilter = e.target.value;
  load();
});
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

async function loadFilters() {
  try {
    const data = await (await fetch("/api/filters")).json();

    const locSel = document.getElementById("locationSelect");
    const curLoc = locSel.value;
    locSel.innerHTML = '<option value="">All Locations</option>';
    (data.locations || []).forEach(loc => {
      const o = document.createElement("option");
      o.value = loc; o.textContent = loc;
      if (loc === curLoc) o.selected = true;
      locSel.appendChild(o);
    });

    const indSel = document.getElementById("industrySelect");
    const curInd = indSel.value;
    indSel.innerHTML = '<option value="">All Industries</option>';
    (data.industries || []).forEach(ind => {
      const o = document.createElement("option");
      o.value = ind; o.textContent = ind;
      if (ind === curInd) o.selected = true;
      indSel.appendChild(o);
    });
  } catch {}
}

// ── Resume upload ──────────────────────────────────────────────────────────
document.getElementById("resumeInput").addEventListener("change", async e => {
  const file = e.target.files[0];
  if (!file) return;

  const label = document.getElementById("resumeLabel");
  label.textContent = "Analyzing...";
  showStatus("Analyzing resume and finding matching jobs — this may take a moment...", "loading");

  const fd = new FormData();
  fd.append("resume", file);

  try {
    const res  = await fetch("/api/resume/upload", { method: "POST", body: fd });
    const data = await res.json();

    if (data.error) {
      showStatus(`Resume error: ${data.error}`, "error");
    } else {
      profile = data;
      syncProfileUI();
      showStatus(`Resume analyzed — ${data.skills?.length || 0} skills detected. Found matching jobs!`, "success");
      await load();
    }
  } catch {
    showStatus("Failed to analyze resume.", "error");
  } finally {
    label.innerHTML = profile ? "&#128196; Change Resume" : "&#128196; Upload Resume";
    e.target.value = "";
  }
});

// ── Clear resume ───────────────────────────────────────────────────────────
document.getElementById("resumeClear").addEventListener("click", async () => {
  await fetch("/api/resume", { method: "DELETE" });
  profile = null;
  locationFilter = "";
  industryFilter = "";
  searchQuery    = "";
  savedOnly      = false;
  document.getElementById("savedToggle").classList.remove("active");
  document.getElementById("searchInput").value = "";
  document.getElementById("locationSelect").value = "";
  document.getElementById("industrySelect").value = "";
  syncProfileUI();
  hideStatus();
});

// ── Refresh jobs ───────────────────────────────────────────────────────────
document.getElementById("refreshBtn").addEventListener("click", async function () {
  this.disabled = true;
  showStatus("Fetching fresh jobs from Adzuna...", "loading");
  try {
    const res = await fetch("/api/jobs/refresh", { method: "POST" });
    const data = await res.json();
    if (data.error) {
      showStatus(data.error, "error");
    } else {
      showStatus("Jobs updated!", "success");
      await load();
    }
  } catch {
    showStatus("Refresh failed.", "error");
  } finally {
    this.disabled = false;
  }
});

// ── Init ───────────────────────────────────────────────────────────────────
async function init() {
  // Check if a profile already exists on the server (e.g. from a previous session)
  try {
    const res = await fetch("/api/profile");
    const data = await res.json();
    if (data) {
      profile = data;
      syncProfileUI();
      await load();
      return;
    }
  } catch {}
  syncProfileUI();
}

init();
