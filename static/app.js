let activeSource = "";
let savedOnly = false;

async function fetchJobs() {
  const params = new URLSearchParams();
  if (activeSource) params.set("source", activeSource);
  if (savedOnly) params.set("saved", "true");

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
    saveBtn.addEventListener("click", () => toggleSave(job.id, card, saveBtn));

    card.querySelector(".btn-dismiss").addEventListener("click", () => dismissJob(job.id, card));

    card.querySelector(".job-notes").addEventListener("blur", (e) => {
      saveNotes(job.id, e.target.value);
    });

    list.appendChild(card);
  });
}

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

document.getElementById("scrapeBtn").addEventListener("click", async function () {
  this.disabled = true;
  this.textContent = "Scraping...";
  showStatus("Scraping jobs — this may take a minute...", "");
  try {
    await fetch("/api/scrape", { method: "POST" });
    showStatus("Done! Jobs updated.", "success");
    load();
  } catch {
    showStatus("Scrape failed. Check console.", "error");
  } finally {
    this.disabled = false;
    this.textContent = "Refresh Jobs";
  }
});

load();
