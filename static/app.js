/* app.js — 1D Site Response Analysis Frontend */
"use strict";

let eqData = null;           // uploaded earthquake metadata
let eqChart = null;           // Chart.js instance for motion preview
let resultCharts = {};        // interactive result charts
let currentRunId = null;

const soilColors = { sand: "#b8943a", silt: "#7a654a", clay: "#5a7d5a" };

// ── Helpers ──────────────────────────────────────────────

function $(sel) { return document.querySelector(sel); }

function toast(msg, type = "info") {
    const container = $("#toastContainer");
    if (!container) return;
    const icons = {
        success: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>',
        error: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>',
        info: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>',
    };
    const el = document.createElement("div");
    el.className = `toast ${type}`;
    el.innerHTML = `<span class="toast-icon">${icons[type] || icons.info}</span>${msg}`;
    container.appendChild(el);
    setTimeout(() => { el.style.opacity = "0"; el.style.transition = "opacity 0.3s"; setTimeout(() => el.remove(), 300); }, 4000);
}

function addLayer(data) {
    const tbody = $("#soilBody");
    const idx = tbody.rows.length + 1;
    const type = data?.soil_type || "sand";
    const thick = data?.thickness ?? 2;
    const unitWt = data?.unit_weight ?? 18;
    const vs = data?.Vs ?? 180;
    const sptN = data?.spt_n ?? 15;
    const row = tbody.insertRow();
    row.innerHTML = `
        <td class="row-num">${idx}</td>
        <td><select onchange="onSoilChange()">
            <option value="sand"${type === "sand" ? " selected" : ""}>Sand</option>
            <option value="silt"${type === "silt" ? " selected" : ""}>Silt</option>
            <option value="clay"${type === "clay" ? " selected" : ""}>Clay</option>
        </select></td>
        <td><input type="number" value="${thick}" min="0.1" max="15" step="0.5" onchange="onSoilChange()"></td>
        <td><input type="number" value="${unitWt}" min="12" max="24" step="0.5" onchange="onSoilChange()"></td>
        <td><input type="number" value="${vs}" min="50" max="1500" step="10" onchange="onSoilChange()"></td>
        <td><input type="number" value="${sptN}" min="0" max="100" step="1" onchange="onSoilChange()"></td>
        <td><button class="btn btn-danger" onclick="removeRow(this)" title="Remove layer">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M18 6L6 18M6 6l12 12"/></svg>
        </button></td>`;
    onSoilChange();
}

function addMultipleLayers() {
    for (let i = 0; i < 5; i++) addLayer();
}

function clearAllLayers() {
    $("#soilBody").innerHTML = "";
    onSoilChange();
}

// ── Soil CSV Import ──────────────────────────────────────

function uploadSoilCSV(input) {
    if (input.files.length) _doSoilCSVUpload(input.files[0]);
    input.value = "";
}

async function _doSoilCSVUpload(file) {
    const form = new FormData();
    form.append("file", file);

    try {
        const res = await fetch("/api/upload-soil-csv", { method: "POST", body: form });
        const data = await res.json();
        if (data.error) { toast(data.error, "error"); return; }

        // Set borehole name
        if (data.borehole_name) {
            $("#boreholeName").value = data.borehole_name;
        }

        // Clear existing layers and populate from CSV
        $("#soilBody").innerHTML = "";
        for (const l of data.layers) {
            addLayer(l);
        }

        // Flash the CSV drop zone green briefly
        const zone = $("#csvDropZone");
        zone.classList.add("success");
        setTimeout(() => zone.classList.remove("success"), 1500);

        toast(`Imported ${data.count} layers (${data.total_depth}m) from ${data.borehole_name}`, "success");
    } catch (e) {
        toast("CSV upload failed: " + e.message, "error");
    }
}

// Wire up CSV drag-and-drop zone
document.addEventListener("DOMContentLoaded", () => {
    const zone = $("#csvDropZone");
    const dropInput = $("#csvDropInput");

    zone.addEventListener("dragover", e => { e.preventDefault(); zone.classList.add("drag-over"); });
    zone.addEventListener("dragleave", () => zone.classList.remove("drag-over"));
    zone.addEventListener("drop", e => {
        e.preventDefault();
        zone.classList.remove("drag-over");
        if (e.dataTransfer.files.length) {
            _doSoilCSVUpload(e.dataTransfer.files[0]);
        }
    });
    dropInput.addEventListener("change", () => {
        if (dropInput.files.length) _doSoilCSVUpload(dropInput.files[0]);
        dropInput.value = "";
    });
});

// ──────────────────────────────────────────────────────────

function removeRow(btn) {
    const row = btn.closest("tr");
    row.remove();
    renumberRows();
    onSoilChange();
}

function renumberRows() {
    const rows = $("#soilBody").rows;
    for (let i = 0; i < rows.length; i++) {
        rows[i].cells[0].textContent = i + 1;
    }
}

function onSoilChange() {
    updateLayerStats();
    updateSoilVis();
    updateRunButton();
}

function updateLayerStats() {
    const layers = getSoilLayers();
    const totalDepth = layers.reduce((s, l) => s + l.thickness, 0);
    $("#layerCount").textContent = `${layers.length} layer${layers.length !== 1 ? "s" : ""} \u00b7 ${totalDepth.toFixed(1)} m depth`;
}

function updateSoilVis() {
    const layers = getSoilLayers();
    const vis = $("#soilColumnVis");
    if (!layers.length) { vis.innerHTML = ""; return; }
    const total = layers.reduce((s, l) => s + l.thickness, 0);
    vis.innerHTML = layers.map(l => {
        const pct = Math.max((l.thickness / total) * 100, 8);
        return `<div class="soil-layer-bar ${l.soil_type}" style="height:${pct}%">${l.soil_type} &middot; ${l.thickness}m &middot; Vs=${l.Vs}</div>`;
    }).join("");
}

function getSoilLayers() {
    const rows = $("#soilBody").rows;
    const layers = [];
    for (let i = 0; i < rows.length; i++) {
        const cells = rows[i].cells;
        const type = cells[1].querySelector("select").value;
        const thick = parseFloat(cells[2].querySelector("input").value) || 2;
        const unitWt = parseFloat(cells[3].querySelector("input").value) || 18;
        const vs = parseFloat(cells[4].querySelector("input").value) || 180;
        const sptN = parseFloat(cells[5].querySelector("input").value) || 15;
        const density = (unitWt * 1000) / 9.81;
        const layer = {
            soil_type: type,
            thickness: thick,
            unit_weight: unitWt,
            mass_density: Math.round(density),
            Vs: vs,
            spt_n: sptN,
        };
        layers.push(layer);
    }
    return layers;
}

// ── Earthquake Upload ────────────────────────────────────

const fileInput = $("#eqFile");
const uploadZone = $("#uploadZone");

uploadZone.addEventListener("dragover", e => { e.preventDefault(); uploadZone.classList.add("drag-over"); });
uploadZone.addEventListener("dragleave", () => uploadZone.classList.remove("drag-over"));
uploadZone.addEventListener("drop", e => {
    e.preventDefault();
    uploadZone.classList.remove("drag-over");
    if (e.dataTransfer.files.length) {
        fileInput.files = e.dataTransfer.files;
        uploadEarthquake(e.dataTransfer.files[0]);
    }
});
fileInput.addEventListener("change", () => {
    if (fileInput.files.length) uploadEarthquake(fileInput.files[0]);
});

async function uploadEarthquake(file) {
    const form = new FormData();
    form.append("file", file);

    try {
        const res = await fetch("/api/upload-earthquake", { method: "POST", body: form });
        const data = await res.json();
        if (data.error) { toast(data.error, "error"); return; }

        eqData = data;
        showEarthquakePreview(data);
        updateRunButton();

        // Show file loaded badge
        const info = $("#eqFileInfo");
        if (info) { info.style.display = "flex"; $("#eqFileName").textContent = data.filename; }
        $("#uploadZone").classList.add("loaded");

        toast(`Loaded ${data.filename} — PGA ${data.pga_g.toFixed(4)}g, ${data.npts} points`, "success");
    } catch (e) {
        toast("Upload failed: " + e.message, "error");
    }
}

function clearEarthquake() {
    eqData = null;
    $("#eqPreview").style.display = "none";
    const info = $("#eqFileInfo");
    if (info) info.style.display = "none";
    $("#uploadZone").classList.remove("loaded");
    if (eqChart) { eqChart.destroy(); eqChart = null; }
    updateRunButton();
}

function showEarthquakePreview(data) {
    $("#eqPreview").style.display = "block";
    $("#eqStats").innerHTML = `
        <div class="eq-stat"><div class="value">${data.pga_g.toFixed(4)}</div><div class="label">PGA (g)</div></div>
        <div class="eq-stat"><div class="value">${data.duration.toFixed(1)}</div><div class="label">Duration (s)</div></div>
        <div class="eq-stat"><div class="value">${data.dt.toFixed(5)}</div><div class="label">dt (s)</div></div>
        <div class="eq-stat"><div class="value">${data.npts}</div><div class="label">Points</div></div>`;

    if (eqChart) eqChart.destroy();
    const ctx = $("#eqChart").getContext("2d");
    eqChart = new Chart(ctx, {
        type: "line",
        data: {
            labels: data.preview.time,
            datasets: [{
                label: "Acceleration (g)",
                data: data.preview.accel,
                borderColor: "#4f8cff",
                borderWidth: 0.7,
                pointRadius: 0,
                fill: false,
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: false,
            plugins: { legend: { display: false } },
            scales: {
                x: {
                    type: "linear",
                    title: { display: true, text: "Time (s)", color: "#000000" },
                    ticks: { color: "#000000", maxTicksLimit: 8 },
                    grid: { color: "#e0e0e0" },
                },
                y: {
                    title: { display: true, text: "Accel (g)", color: "#000000" },
                    ticks: { color: "#000000" },
                    grid: { color: "#e0e0e0" },
                }
            }
        }
    });
}

// ── Compute Params ───────────────────────────────────────

async function computeParams() {
    const layers = getSoilLayers();
    if (!layers.length) return;

    try {
        const res = await fetch("/api/compute-params", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ layers }),
        });
        const data = await res.json();
        if (data.error) return;

        const box = $("#computedParams");
        box.style.display = "block";
        const grid = $("#paramGrid");
        const layer = data.layers[0];
        grid.innerHTML = Object.entries(layer).map(
            ([k, v]) => `<div><strong>${k}:</strong> ${typeof v === "number" ? v.toFixed(4) : v}</div>`
        ).join("");
    } catch (e) {
        /* silent */
    }
}

// ── Run Analysis ─────────────────────────────────────────

function updateRunButton() {
    const layers = getSoilLayers();
    $("#runBtn").disabled = !(layers.length > 0 && eqData);
}

async function runAnalysis() {
    const layers = getSoilLayers();
    if (!layers.length || !eqData) return;

    // Validate inputs
    const totalDepth = layers.reduce((s, l) => s + l.thickness, 0);
    if (totalDepth < 1) { toast("Total soil depth must be at least 1m", "error"); return; }
    if (totalDepth > 150) { toast("Total soil depth exceeds 150m — may cause convergence issues", "error"); return; }
    for (let i = 0; i < layers.length; i++) {
        const l = layers[i];
        if (l.Vs < 30 || l.Vs > 2000) { toast(`Layer ${i+1}: Vs=${l.Vs} m/s is outside valid range (30–2000)`, "error"); return; }
        if (l.thickness < 0.1) { toast(`Layer ${i+1}: thickness ${l.thickness}m is too thin (min 0.1m)`, "error"); return; }
    }

    const waterTable = parseFloat($("#waterTable").value) || 5;

    // Add water table info to layers
    let cumDepth = 0;
    for (const l of layers) {
        const midDepth = cumDepth + l.thickness / 2;
        l.below_wt = midDepth >= waterTable;
        cumDepth += l.thickness;
    }

    const btn = $("#runBtn");
    const statusBar = $("#statusBar");
    const statusText = $("#statusText");
    const stepList = $("#stepList");

    btn.disabled = true;
    statusBar.className = "status-bar running";
    statusText.textContent = "Submitting analysis...";
    if (stepList) { stepList.style.display = "block"; resetSteps(); }

    try {
        const boreholeName = $("#boreholeName") ? $("#boreholeName").value.trim() : "";
        const res = await fetch("/api/run", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                layers: layers,
                earthquake_file: eqData.filename,
                water_table: waterTable,
                borehole_name: boreholeName,
            }),
        });
        const data = await res.json();
        if (data.error) {
            showError(data.error);
            btn.disabled = false;
            return;
        }

        currentRunId = data.run_id;
        statusText.textContent = "Running OpenSees analysis...";
        toast("Analysis started — this may take a few minutes", "info");
        pollStatus(data.run_id);
    } catch (e) {
        showError("Failed to submit: " + e.message);
        btn.disabled = false;
    }
}

function resetSteps() {
    document.querySelectorAll(".step-item").forEach(el => {
        el.className = "step-item";
    });
}

function updateStep(stepName, state) {
    const el = document.querySelector(`.step-item[data-step="${stepName}"]`);
    if (!el) return;
    el.className = "step-item " + state;
}

async function pollStatus(runId) {
    const statusBar = $("#statusBar");
    const statusText = $("#statusText");

    const poll = async () => {
        try {
            const res = await fetch(`/api/status/${runId}`);
            const data = await res.json();

            // Update step indicators from result steps
            if (data.result && data.result.steps) {
                for (const step of data.result.steps) {
                    updateStep(step.step, step.status === "ok" ? "done" : "error-step");
                }
            }

            if (data.status === "completed") {
                statusText.textContent = "Analysis completed successfully.";
                statusBar.className = "status-bar completed";
                // Mark all steps done
                ["soil", "earthquake", "opensees", "postprocess"].forEach(s => updateStep(s, "done"));
                fetchResults(runId);
                $("#runBtn").disabled = false;
                toast("Analysis completed!", "success");
                return;
            }

            if (data.status === "error") {
                showError(data.progress || "Analysis failed");
                $("#runBtn").disabled = false;
                toast("Analysis failed: " + (data.progress || "Unknown error"), "error");
                return;
            }

            // Show progress text
            const prog = data.progress || "Running...";
            statusText.textContent = prog;

            // Guess current step from progress text
            if (prog.includes("soil") || prog.includes("Soil")) updateStep("soil", "active");
            else if (prog.includes("earthquake") || prog.includes("Earthquake")) { updateStep("soil", "done"); updateStep("earthquake", "active"); }
            else if (prog.includes("OpenSees") || prog.includes("Running")) { updateStep("soil", "done"); updateStep("earthquake", "done"); updateStep("opensees", "active"); }

            setTimeout(poll, 1500);
        } catch (e) {
            setTimeout(poll, 3000);
        }
    };
    poll();
}

function showError(msg) {
    const statusBar = $("#statusBar");
    const statusText = $("#statusText");
    statusBar.className = "status-bar error";
    statusText.textContent = msg;
}

// ── Results ──────────────────────────────────────────────

async function fetchResults(runId) {
    try {
        const res = await fetch(`/api/results/${runId}`);
        const data = await res.json();
        if (data.error) { showError(data.error); return; }

        const results = data.results || data;
        showResults(runId, results);
    } catch (e) {
        showError("Failed to load results: " + e.message);
    }
}

function showResults(runId, r) {
    const section = $("#resultsSection");
    section.classList.add("visible");
    section.scrollIntoView({ behavior: "smooth" });

    // Summary metrics
    const mg = $("#metricsGrid");
    mg.innerHTML = `
        <div class="result-metric highlight"><div class="value">${r.pga_surface_g?.toFixed(4) || "—"}</div><div class="label">Surface PGA (g)</div></div>
        <div class="result-metric"><div class="value">${r.pga_input_g?.toFixed(4) || "—"}</div><div class="label">Input PGA (g)</div></div>
        <div class="result-metric highlight"><div class="value">${r.pga_amplification?.toFixed(2) || "—"}</div><div class="label">PGA Amplification</div></div>
        <div class="result-metric"><div class="value">${r.max_Sa_surface_g?.toFixed(3) || "—"}</div><div class="label">Max Sa Surface (g)</div></div>
        <div class="result-metric"><div class="value">${r.period_max_Sa?.toFixed(3) || "—"}</div><div class="label">Period at Max Sa (s)</div></div>
        <div class="result-metric"><div class="value">${r.total_depth?.toFixed(1) || "—"}</div><div class="label">Total Depth (m)</div></div>`;

    // Figures
    const fg = $("#figuresGrid");
    if (r.figures && r.figures.length) {
        fg.innerHTML = r.figures.map(f => {
            const cap = f.replace(/\.png$/i, "").replace(/_/g, " ").replace(/^Fig\d+\s*/,"");
            return `<div class="figure-card">
                <img src="/api/results/${runId}/figure/${f}" alt="${cap}" onclick="openLightbox(this.src)">
                <div class="fig-caption">${cap}</div>
            </div>`;
        }).join("");
    } else {
        fg.innerHTML = '<div class="empty-state">No figures generated.</div>';
    }

    // Interactive charts
    buildInteractiveCharts(r);

    // Shaking simulation
    initShaking(r);

    // Downloads
    const dr = $("#downloadRow");
    dr.innerHTML = "";
    const csvFiles = [
        "response_spectrum_surface.csv",
        "response_spectrum_base.csv",
        "amplification_factor.csv",
    ];
    for (const f of csvFiles) {
        const a = document.createElement("a");
        a.className = "btn btn-outline btn-sm";
        a.href = `/api/results/${runId}/csv/${f}`;
        a.download = f;
        a.innerHTML = `<svg class="btn-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>${f.replace(".csv", "").replace(/_/g, " ")}`;
        dr.appendChild(a);
    }
}

function buildInteractiveCharts(r) {
    // Destroy old charts
    Object.values(resultCharts).forEach(c => c.destroy());
    resultCharts = {};

    const lineOpts = {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        plugins: { legend: { labels: { color: "#000000" } } },
        scales: {
            x: { ticks: { color: "#444" }, grid: { color: "#e0e0e0" } },
            y: { ticks: { color: "#444" }, grid: { color: "#e0e0e0" } },
        }
    };

    // Accel time history
    if (r.time_history) {
        const th = r.time_history;
        resultCharts.accel = new Chart($("#chartAccel").getContext("2d"), {
            type: "line",
            data: {
                labels: th.time,
                datasets: [
                    { label: "Surface (g)", data: th.acc_surface, borderColor: "#4f8cff", borderWidth: 0.8, pointRadius: 0, fill: false },
                    { label: "Bedrock (g)", data: th.acc_base, borderColor: "#d62728", borderWidth: 0.5, pointRadius: 0, borderDash: [4, 2], fill: false },
                ]
            },
            options: {
                ...lineOpts,
                scales: {
                    ...lineOpts.scales,
                    x: { ...lineOpts.scales.x, type: "linear", title: { display: true, text: "Time (s)", color: "#000000" } },
                    y: { ...lineOpts.scales.y, title: { display: true, text: "Acceleration (g)", color: "#000000" } },
                },
                plugins: { ...lineOpts.plugins, title: { display: true, text: "Acceleration Time History", color: "#000000" } },
            }
        });
    }

    // Response spectra
    if (r.spectra) {
        const sp = r.spectra;
        resultCharts.spectra = new Chart($("#chartSpectrum").getContext("2d"), {
            type: "line",
            data: {
                labels: sp.periods,
                datasets: [
                    { label: "Surface Sa (g)", data: sp.Sa_surface, borderColor: "#4f8cff", borderWidth: 1.4, pointRadius: 0, fill: false },
                    { label: "Input Sa (g)", data: sp.Sa_base, borderColor: "#d62728", borderWidth: 1.2, borderDash: [5, 3], pointRadius: 0, fill: false },
                ]
            },
            options: {
                ...lineOpts,
                scales: {
                    ...lineOpts.scales,
                    x: { ...lineOpts.scales.x, type: "logarithmic", title: { display: true, text: "Period (s)", color: "#000000" }, min: 0.01, max: 10 },
                    y: { ...lineOpts.scales.y, title: { display: true, text: "Sa (g)", color: "#000000" } },
                },
                plugins: { ...lineOpts.plugins, title: { display: true, text: "Response Spectra (5% Damping)", color: "#000000" } },
            }
        });

        // Amplification
        resultCharts.amp = new Chart($("#chartAmplification").getContext("2d"), {
            type: "line",
            data: {
                labels: sp.periods,
                datasets: [{
                    label: "Amplification Factor",
                    data: sp.amplification,
                    borderColor: "#2ca02c",
                    borderWidth: 1.3,
                    pointRadius: 0,
                    fill: { target: "origin", above: "rgba(44,160,44,0.1)" },
                }]
            },
            options: {
                ...lineOpts,
                scales: {
                    ...lineOpts.scales,
                    x: { ...lineOpts.scales.x, type: "logarithmic", title: { display: true, text: "Period (s)", color: "#000000" }, min: 0.01, max: 10 },
                    y: { ...lineOpts.scales.y, title: { display: true, text: "Amp. Factor", color: "#000000" } },
                },
                plugins: { ...lineOpts.plugins, title: { display: true, text: "Spectral Amplification Factor", color: "#000000" } },
            }
        });
    }
}

// ── Tabs ─────────────────────────────────────────────────

function switchTab(el) {
    document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
    el.classList.add("active");
    document.querySelectorAll(".tab-content").forEach(c => c.classList.remove("active"));
    const target = el.getAttribute("data-tab");
    $(`#tab-${target}`).classList.add("active");

    if (target === "charts") {
        setTimeout(() => { Object.values(resultCharts).forEach(c => c.resize()); }, 50);
    }
    if (target === "shaking" && shakingAnim.data) {
        drawShakingFrame(shakingAnim.frameIdx);
    }
}

// ── Lightbox ─────────────────────────────────────────────

function openLightbox(src) {
    $("#lightboxImg").src = src;
    $("#lightbox").classList.add("active");
}

// ── Init ─────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", () => {
    // No default layers — user will either import CSV or add manually
    updateRunButton();
});

// ── Shaking Simulation ──────────────────────────────────

const shakingAnim = {
    data: null,
    playing: false,
    frameIdx: 0,
    speed: 1,
    rafId: null,
    lastTs: 0,
    maxDisp: 1,
};

const SOIL_COLORS_VIS = {
    sand: { fill: "#c9a94e", stroke: "#a88a30", light: "#dbc06a" },
    silt: { fill: "#8a7558", stroke: "#6e5c42", light: "#a09070" },
    clay: { fill: "#6a9a6a", stroke: "#4e7e4e", light: "#82b082" },
};
const BEDROCK_CLR = "#555";

function initShaking(r) {
    const wrapper = document.querySelector(".shaking-wrapper");
    const empty = $("#shakingEmpty");
    if (!r.animation || !r.animation.nodes || !r.animation.nodes.length) {
        if (wrapper) wrapper.style.display = "none";
        if (empty) empty.style.display = "block";
        return;
    }
    if (wrapper) wrapper.style.display = "flex";
    if (empty) empty.style.display = "none";

    const anim = r.animation;
    shakingAnim.data = anim;
    shakingAnim.frameIdx = 0;
    shakingAnim.playing = false;

    let maxD = 0;
    for (const nd of anim.nodes) {
        for (const d of nd.disp) maxD = Math.max(maxD, Math.abs(d));
    }
    shakingAnim.maxDisp = maxD || 0.01;

    const slider = $("#shakingSlider");
    if (slider) { slider.max = anim.times.length - 1; slider.value = 0; }

    const tTotal = anim.times[anim.times.length - 1];
    if ($("#shakingTimeTotal")) $("#shakingTimeTotal").textContent = tTotal.toFixed(2);
    if ($("#shakingTimeVal")) $("#shakingTimeVal").textContent = "0.00";

    drawTimelineMini(anim);
    drawShakingFrame(0);
}

function drawTimelineMini(anim) {
    const container = $("#timelineAccelMini");
    if (!container) return;
    const W = container.clientWidth || 800;
    const H = 40;
    container.innerHTML = `<canvas width="${W}" height="${H}" style="width:100%;height:100%"></canvas>`;
    const canvas = container.querySelector("canvas");
    const ctx = canvas.getContext("2d");

    const surfNode = anim.nodes[anim.nodes.length - 1];
    if (!surfNode) return;
    const acc = surfNode.accel;
    const n = acc.length;
    let maxA = 0;
    for (const a of acc) maxA = Math.max(maxA, Math.abs(a));
    if (maxA === 0) maxA = 1;

    ctx.fillStyle = "#f8f8f8";
    ctx.fillRect(0, 0, W, H);
    ctx.beginPath();
    ctx.strokeStyle = "rgba(79,140,255,0.6)";
    ctx.lineWidth = 0.8;
    for (let i = 0; i < n; i++) {
        const x = (i / (n - 1)) * W;
        const y = H / 2 - (acc[i] / maxA) * (H / 2 - 2);
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    }
    ctx.stroke();
    ctx.strokeStyle = "rgba(0,0,0,0.1)";
    ctx.lineWidth = 0.5;
    ctx.beginPath(); ctx.moveTo(0, H / 2); ctx.lineTo(W, H / 2); ctx.stroke();
}

function drawShakingFrame(idx) {
    const anim = shakingAnim.data;
    if (!anim) return;
    idx = Math.max(0, Math.min(idx, anim.times.length - 1));
    shakingAnim.frameIdx = idx;

    const canvas = $("#shakingCanvas");
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    const dpr = window.devicePixelRatio || 1;
    const dispW = canvas.clientWidth || 900;
    const dispH = Math.max(420, dispW * 0.56);
    canvas.width = dispW * dpr;
    canvas.height = dispH * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const cW = dispW, cH = dispH;

    ctx.clearRect(0, 0, cW, cH);

    const PAD_L = 55, PAD_T = 30, PAD_B = 40;
    const COL_W = 160;
    const COL_L = PAD_L;
    const COL_R = COL_L + COL_W;
    const DRAW_H = cH - PAD_T - PAD_B;
    const totalDepth = anim.total_depth;
    const yScale = DRAW_H / totalDepth;
    const DISP_SCALE = 55 / shakingAnim.maxDisp;

    const t = anim.times[idx];
    const nodes = anim.nodes;
    const frameDisps = nodes.map(nd => nd.disp[idx] || 0);
    const frameAccels = nodes.map(nd => nd.accel[idx] || 0);
    const nodeDepths = nodes.map(nd => nd.depth);

    function interp(depth) {
        if (depth <= nodeDepths[0]) return frameDisps[0];
        if (depth >= nodeDepths[nodeDepths.length - 1]) return frameDisps[nodeDepths.length - 1];
        for (let i = 1; i < nodeDepths.length; i++) {
            if (depth <= nodeDepths[i]) {
                const f = (depth - nodeDepths[i-1]) / (nodeDepths[i] - nodeDepths[i-1]);
                return frameDisps[i-1] + f * (frameDisps[i] - frameDisps[i-1]);
            }
        }
        return 0;
    }

    // BG
    ctx.fillStyle = "#fafafa";
    ctx.fillRect(0, 0, cW, cH);

    // ── Soil layers ──
    for (const layer of anim.layers) {
        const yTop = PAD_T + layer.top * yScale;
        const yBot = PAD_T + layer.bot * yScale;
        const dTop = interp(layer.top) * DISP_SCALE;
        const dBot = interp(layer.bot) * DISP_SCALE;
        const sc = SOIL_COLORS_VIS[layer.type] || SOIL_COLORS_VIS.sand;

        ctx.beginPath();
        ctx.moveTo(COL_L + dTop, yTop);
        ctx.lineTo(COL_R + dTop, yTop);
        ctx.lineTo(COL_R + dBot, yBot);
        ctx.lineTo(COL_L + dBot, yBot);
        ctx.closePath();
        ctx.fillStyle = sc.fill; ctx.fill();
        ctx.strokeStyle = sc.stroke; ctx.lineWidth = 0.6; ctx.stroke();

        // Texture dots
        const layH = yBot - yTop;
        const nD = Math.max(1, Math.floor(layH / 7));
        ctx.fillStyle = sc.light; ctx.globalAlpha = 0.3;
        for (let r = 0; r < nD; r++) {
            const fy = (r + 0.3) / nD;
            const py = yTop + fy * layH;
            const ld = interp(layer.top + fy * (layer.bot - layer.top)) * DISP_SCALE;
            for (let dx = 12; dx < COL_W; dx += 16 + Math.sin(r * 37) * 5) {
                const px = COL_L + dx + ld + Math.sin(r * 7 + dx) * 2.5;
                ctx.beginPath(); ctx.arc(px, py + Math.cos(dx * 3) * 1.5, layer.type === "clay" ? 1 : 1.4, 0, Math.PI * 2); ctx.fill();
            }
        }
        ctx.globalAlpha = 1;

        // Label
        if (layH > 14) {
            ctx.fillStyle = "#333"; ctx.font = "10px Inter,sans-serif"; ctx.textAlign = "left";
            ctx.fillText(`${layer.type} ${layer.thick}m`, COL_R + 8 + (dTop + dBot) / 2, (yTop + yBot) / 2 + 3);
        }
    }

    // Bedrock
    const brY = PAD_T + totalDepth * yScale;
    const brD = interp(totalDepth) * DISP_SCALE;
    ctx.fillStyle = BEDROCK_CLR;
    ctx.fillRect(COL_L + brD - 4, brY, COL_W + 8, PAD_B);
    ctx.strokeStyle = "#777"; ctx.lineWidth = 0.4;
    for (let hx = -20; hx < COL_W + 30; hx += 8) {
        ctx.beginPath(); ctx.moveTo(COL_L + brD + hx, brY); ctx.lineTo(COL_L + brD + hx - 10, brY + PAD_B); ctx.stroke();
    }

    // Water table
    const wt = parseFloat($("#waterTable")?.value) || 5;
    if (wt < totalDepth) {
        const wY = PAD_T + wt * yScale;
        const wd = interp(wt) * DISP_SCALE;
        ctx.fillStyle = "rgba(79,140,255,0.15)";
        ctx.fillRect(COL_L + wd, wY, COL_W, brY - wY);
        ctx.strokeStyle = "#4f8cff"; ctx.lineWidth = 1; ctx.setLineDash([5, 3]);
        ctx.beginPath(); ctx.moveTo(COL_L + wd - 6, wY); ctx.lineTo(COL_R + wd + 6, wY); ctx.stroke();
        ctx.setLineDash([]); ctx.fillStyle = "#4f8cff"; ctx.font = "bold 9px Inter,sans-serif"; ctx.textAlign = "right";
        ctx.fillText("WT", COL_L + wd - 8, wY + 3);
    }

    // Depth axis
    ctx.strokeStyle = "#999"; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(COL_L - 8, PAD_T); ctx.lineTo(COL_L - 8, brY); ctx.stroke();
    ctx.fillStyle = "#444"; ctx.font = "9px Inter,sans-serif"; ctx.textAlign = "right";
    const dStep = totalDepth > 50 ? 10 : totalDepth > 20 ? 5 : 2;
    for (let d = 0; d <= totalDepth; d += dStep) {
        const yp = PAD_T + d * yScale;
        ctx.fillText(d + "m", COL_L - 12, yp + 3);
        ctx.beginPath(); ctx.moveTo(COL_L - 10, yp); ctx.lineTo(COL_L - 6, yp); ctx.stroke();
    }
    ctx.font = "bold 10px Inter,sans-serif"; ctx.textAlign = "center";
    ctx.save(); ctx.translate(11, PAD_T + DRAW_H / 2); ctx.rotate(-Math.PI / 2);
    ctx.fillText("Depth (m)", 0, 0); ctx.restore();

    // ── Accel profile (right side) ──
    const PROF_L = COL_R + 100;
    const PROF_W = cW - PROF_L - 15;
    if (PROF_W > 60) {
        const maxA = Math.max(0.01, ...nodes.map(nd => Math.max(...nd.accel.map(Math.abs))));
        const aS = (PROF_W / 2) / maxA;
        const cx = PROF_L + PROF_W / 2;

        ctx.strokeStyle = "#ccc"; ctx.lineWidth = 0.5;
        ctx.beginPath(); ctx.moveTo(cx, PAD_T); ctx.lineTo(cx, brY); ctx.stroke();

        // Profile line
        ctx.beginPath();
        ctx.strokeStyle = "rgba(79,140,255,0.7)"; ctx.lineWidth = 1.5;
        for (let ni = 0; ni < nodes.length; ni++) {
            const x = cx + frameAccels[ni] * aS;
            const y = PAD_T + nodes[ni].depth * yScale;
            if (ni === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
        }
        ctx.stroke();

        // Dots + bars
        for (let ni = 0; ni < nodes.length; ni++) {
            const aVal = frameAccels[ni];
            const x = cx + aVal * aS;
            const y = PAD_T + nodes[ni].depth * yScale;
            const intensity = Math.abs(aVal) / maxA;
            const clr = intensity > 0.7 ? "#e76f51" : intensity > 0.3 ? "#e9c46a" : "#4f8cff";
            ctx.fillStyle = clr; ctx.globalAlpha = 0.5;
            ctx.fillRect(cx, y - 2, aVal * aS, 4);
            ctx.globalAlpha = 1;
            ctx.beginPath(); ctx.arc(x, y, 3.5, 0, Math.PI * 2); ctx.fillStyle = clr; ctx.fill();
            ctx.strokeStyle = "#fff"; ctx.lineWidth = 0.8; ctx.stroke();
        }

        ctx.fillStyle = "#444"; ctx.font = "bold 9px Inter,sans-serif"; ctx.textAlign = "center";
        ctx.fillText("Accel (g)", cx, PAD_T - 12);
        ctx.font = "8px Inter,sans-serif";
        ctx.fillText(`-${maxA.toFixed(2)}`, PROF_L + 4, PAD_T - 3);
        ctx.fillText(`+${maxA.toFixed(2)}`, PROF_L + PROF_W - 4, PAD_T - 3);
    }

    // ── Time overlay ──
    ctx.fillStyle = "#000000"; ctx.font = "bold 13px Inter,sans-serif"; ctx.textAlign = "left";
    ctx.fillText(`t = ${t.toFixed(3)} s`, COL_L, PAD_T - 12);

    const surfA = frameAccels[frameAccels.length - 1] || 0;
    ctx.fillStyle = Math.abs(surfA) > 0.05 ? "#e76f51" : "#4f8cff";
    ctx.font = "bold 11px Inter,sans-serif";
    ctx.fillText(`Surface: ${surfA.toFixed(4)} g`, COL_L, cH - 8);

    // Update UI
    if ($("#shakingTimeVal")) $("#shakingTimeVal").textContent = t.toFixed(2);
    if ($("#shakingSlider")) $("#shakingSlider").value = idx;
}

function toggleShakingPlay() {
    if (!shakingAnim.data) return;
    shakingAnim.playing = !shakingAnim.playing;
    const label = $("#shakingPlayLabel");
    const icon = $("#shakingPlayIcon");
    if (shakingAnim.playing) {
        label.textContent = "Pause";
        icon.innerHTML = '<rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/>';
        shakingAnim.lastTs = performance.now();
        shakingAnim.rafId = requestAnimationFrame(shakingLoop);
    } else {
        label.textContent = "Play";
        icon.innerHTML = '<polygon points="5 3 19 12 5 21 5 3"/>';
        if (shakingAnim.rafId) cancelAnimationFrame(shakingAnim.rafId);
    }
}

function shakingLoop(ts) {
    if (!shakingAnim.playing || !shakingAnim.data) return;
    const dtReal = (ts - shakingAnim.lastTs) / 1000;
    shakingAnim.lastTs = ts;
    const dtAnim = shakingAnim.data.dt_anim;
    const framesToAdvance = Math.max(1, Math.round((dtReal * shakingAnim.speed) / dtAnim));
    let next = shakingAnim.frameIdx + framesToAdvance;
    if (next >= shakingAnim.data.times.length) next = 0;
    drawShakingFrame(next);
    shakingAnim.rafId = requestAnimationFrame(shakingLoop);
}

function resetShaking() {
    shakingAnim.playing = false;
    if (shakingAnim.rafId) cancelAnimationFrame(shakingAnim.rafId);
    const label = $("#shakingPlayLabel");
    const icon = $("#shakingPlayIcon");
    if (label) label.textContent = "Play";
    if (icon) icon.innerHTML = '<polygon points="5 3 19 12 5 21 5 3"/>';
    drawShakingFrame(0);
}

function scrubShaking(val) {
    const idx = parseInt(val, 10);
    if (shakingAnim.playing) {
        shakingAnim.playing = false;
        if (shakingAnim.rafId) cancelAnimationFrame(shakingAnim.rafId);
        const label = $("#shakingPlayLabel");
        const icon = $("#shakingPlayIcon");
        if (label) label.textContent = "Play";
        if (icon) icon.innerHTML = '<polygon points="5 3 19 12 5 21 5 3"/>';
    }
    drawShakingFrame(idx);
}

function updateShakingSpeed() {
    shakingAnim.speed = parseFloat($("#shakingSpeed").value) || 1;
}
