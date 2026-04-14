let activeSource = "";
let savedOnly = false;
let searchQuery = "";
let locationFilter = "";
let industryFilter = "";

// Debounce helper
function debounce(fn, ms) {
  let timer;
  return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), ms); };
}

async function fetchJobs() {
  const params = new URLSearchParams();
  if (activeSource) params.set("source", activeSource);
  if (savedOnly) params.set("saved", "true");
  if (searchQuery) params.set("search", searchQuery);
  if (locationFilter) params.set("location", locationFilter);
  if (industryFilter) params.set("industry", industryFilter);

  const res = await fetch(`/api/jobs?${params}`);
  return await res.json();
}

function renderJobs(jobs) {
  const list = document.getElementById("jobList");
  list.innerHTML = "";

  if (!jobs.length) {
    list.innerHTML = '<p class="loading">No jobs found. Click "Refresh Jobs" to scrape.</p>';
    return;
  }

  const template = document.getElementById("jobCard");

  jobs.forEach((job) => {
    const card = template.content.cloneNode(true).querySelector(".job-card");
    card.dataset.id = job.id;
    if (job.is_saved) card.classList.add("saved");

    card.querySelector(".job-title").textContent = job.title;
    card.querySelector(".job-title").href = job.url;
    card.querySelector(".job-company").textContent = job.company;
    card.querySelector(".job-location").textContent = job.location || "";
    card.querySelector(".job-date").textContent = job.date_posted ? `· ${job.date_posted}` : "";
    card.querySelector(".job-snippet").textContent = job.description_snippet || "";
    card.querySelector(".job-notes").value = job.notes || "";

    const industryEl = card.querySelector(".job-industry");
    if (job.industry && job.industry !== "Other") {
      industryEl.textContent = job.industry;
    } else {
      industryEl.style.display = "none";
    }

    const sourceBadge = card.querySelector(".job-source");
    sourceBadge.textContent = job.source;
    sourceBadge.classList.add(`badge-${job.source}`);

    const keywordsEl = card.querySelector(".job-keywords");
    (job.matched_keywords || "").split(",").filter(Boolean).forEach((kw) => {
      const tag = document.createElement("span");
      tag.className = "keyword-tag";
      tag.textContent = kw.trim();
      keywordsEl.appendChild(tag);
    });

    const saveBtn = card.querySelector(".btn-save");
    saveBtn.innerHTML = job.is_saved ? "&#9829;" : "&#9825;";
    if (job.is_saved) saveBtn.classList.add("saved");
    saveBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      toggleSave(job.id, card, saveBtn);
    });

    card.querySelector(".btn-dismiss").addEventListener("click", (e) => {
      e.stopPropagation();
      dismissJob(job.id, card);
    });

    card.querySelector(".job-notes").addEventListener("click", (e) => e.stopPropagation());
    card.querySelector(".job-notes").addEventListener("blur", (e) => {
      saveNotes(job.id, e.target.value);
    });

    // Open modal on card click (but not on interactive elements)
    card.addEventListener("click", (e) => {
      if (e.target.closest(".btn-save, .btn-dismiss, .job-notes, .job-title")) return;
      openModal(job);
    });

    list.appendChild(card);
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

  const industryEl = document.getElementById("modalIndustry");
  if (job.industry && job.industry !== "Other") {
    industryEl.textContent = job.industry;
    industryEl.style.display = "";
  } else {
    industryEl.textContent = "";
    industryEl.style.display = "none";
  }

  // If already analyzed, show immediately
  if (job.ai_description) {
    document.getElementById("modalSalaryValue").textContent = job.ai_salary_estimate || "Not available";
    document.getElementById("modalDescription").textContent = job.ai_description;
  } else {
    document.getElementById("modalSalaryValue").textContent = "Analyzing...";
    document.getElementById("modalDescription").innerHTML = '<div class="ai-loading">Analyzing with Gemini AI...</div>';
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
    const res = await fetch(`/api/jobs/${jobId}/analyze`, { method: "POST" });
    const data = await res.json();

    if (data.error) {
      document.getElementById("modalSalaryValue").textContent = "Unavailable";
      document.getElementById("modalDescription").innerHTML = `<div class="modal-error">AI analysis failed: ${data.error}</div>`;
      return;
    }

    document.getElementById("modalSalaryValue").textContent = data.salary_estimate || "Not available";
    document.getElementById("modalDescription").textContent = data.description || "";
  } catch (err) {
    document.getElementById("modalSalaryValue").textContent = "Unavailable";
    document.getElementById("modalDescription").innerHTML = '<div class="modal-error">Failed to connect to AI service.</div>';
  }
}

document.getElementById("modalClose").addEventListener("click", closeModal);
document.getElementById("modal").addEventListener("click", (e) => {
  if (e.target === document.getElementById("modal")) closeModal();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeModal();
});

// ── Actions ────────────────────────────────────────────────────────────────

async function toggleSave(id, card, btn) {
  const res = await fetch(`/api/jobs/${id}/save`, { method: "POST" });
  const data = await res.json();
  btn.innerHTML = data.is_saved ? "&#9829;" : "&#9825;";
  btn.classList.toggle("saved", data.is_saved);
  card.classList.toggle("saved", data.is_saved);
  if (savedOnly && !data.is_saved) card.remove();
}

async function dismissJob(id, card) {
  await fetch(`/api/jobs/${id}/dismiss`, { method: "POST" });
  card.style.opacity = "0";
  card.style.transition = "opacity 0.3s";
  setTimeout(() => card.remove(), 300);
}

async function saveNotes(id, notes) {
  await fetch(`/api/jobs/${id}/notes`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ notes }),
  });
}

function showStatus(msg, type) {
  const el = document.getElementById("status");
  el.textContent = msg;
  el.className = `status ${type}`;
  setTimeout(() => (el.className = "status hidden"), 4000);
}

async function load() {
  const jobs = await fetchJobs();
  renderJobs(jobs);
}

// ── Filters & search ───────────────────────────────────────────────────────

async function loadFilters() {
  try {
    const res = await fetch("/api/filters");
    const data = await res.json();
    const select = document.getElementById("industrySelect");
    data.industries.forEach((ind) => {
      const opt = document.createElement("option");
      opt.value = ind;
      opt.textContent = ind;
      select.appendChild(opt);
    });
  } catch (_) {}
}

document.querySelectorAll(".filter-btn:not(#savedToggle)").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".filter-btn:not(#savedToggle)").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    activeSource = btn.dataset.source;
    load();
  });
});

document.getElementById("savedToggle").addEventListener("click", function () {
  savedOnly = !savedOnly;
  this.classList.toggle("active", savedOnly);
  load();
});

document.getElementById("searchInput").addEventListener("input", debounce((e) => {
  searchQuery = e.target.value.trim();
  load();
}, 300));

document.getElementById("locationInput").addEventListener("input", debounce((e) => {
  locationFilter = e.target.value.trim();
  load();
}, 400));

document.getElementById("industrySelect").addEventListener("change", (e) => {
  industryFilter = e.target.value;
  load();
});

document.getElementById("scrapeBtn").addEventListener("click", async function () {
  this.disabled = true;
  this.textContent = "Scraping...";
  showStatus("Scraping jobs — this may take a minute...", "");
  try {
    await fetch("/api/scrape", { method: "POST" });
    showStatus("Done! Jobs updated.", "success");
    await loadFilters();
    load();
  } catch {
    showStatus("Scrape failed. Check console.", "error");
  } finally {
    this.disabled = false;
    this.textContent = "Refresh Jobs";
  }
});

loadFilters();
load();
