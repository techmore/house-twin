/*
 * app.js — data fetching, side panel, sparklines, level controls.
 */

import { HouseScene } from "./scene.js";

const REFRESH_MS = 30000;
const $ = (sel) => document.querySelector(sel);

const state = {
  house: null,
  readings: [],
  selected: null,
  history: null,
  hours: 24,
};

const scene = new HouseScene($("#view"));
scene.onSelect = handleSelect;
scene.onEdit = (result) => {
  if (!editMode) return;
  scene.applyPlacement(result.id, result.x, result.z);
  pendingPlacements.set(result.id, {
    x: result.x,
    z: result.z,
    room_id: result.roomId,
  });
  const label = result.room ? result.room : "outside any room";
  toast(`${result.id} → x ${result.x} z ${result.z} (${label})`);
};

// ── data ─────────────────────────────────────────────────────────────────────

async function getJSON(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${url} -> ${response.status}`);
  return response.json();
}

async function loadHouse() {
  state.house = await getJSON("/api/house");
  scene.build(state.house);
  renderLevelToggles();
  buildLegend();
  scene.invalidate();
}

async function loadReadings() {
  try {
    const data = await getJSON("/api/readings?hours=24");
    state.readings = data.sensors;
    scene.updateReadings(state.readings);
    renderSummary(data.summary);
    renderSensorList();
    renderStatusDot();
  } catch (err) {
    console.error("reading fetch failed", err);
  }
}

async function loadHistory(sensorId) {
  const data = await fetchHistory(sensorId, state.hours);
  state.history = data;
  drawChart(data);
}

// ── rendering ────────────────────────────────────────────────────────────────

function fmtAge(ts) {
  if (!ts) return "never";
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 90) return "just now";
  if (s < 5400) return `${Math.round(s / 60)}m ago`;
  if (s < 172800) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

function renderSummary(summary) {
  const set = (sel, value) => { const el = $(sel); if (el) el.textContent = value; };
  const has = summary && summary.temp_min != null;
  set("#sum-indoor", has ? `${summary.temp_avg.toFixed(1)}°` : "--");
  set("#sum-range", has ? `${summary.temp_min.toFixed(1)}° – ${summary.temp_max.toFixed(1)}°` : "--");
  set("#sum-humidity", has ? `${summary.hum_avg.toFixed(0)}%` : "--");
  set("#sum-sensors", summary ? summary.sensors : 0);
}

function renderSensorList() {
  const list = $("#sensor-list");
  if (!list) return;

  if (!state.readings.length) {
    list.innerHTML = `<li class="empty">No sensors reported yet.</li>`;
    return;
  }

  const sorted = [...state.readings].sort((a, b) => {
    const av = a.temperature ?? -999;
    const bv = b.temperature ?? -999;
    return bv - av;
  });

  list.innerHTML = sorted
    .map((s) => {
      const t = s.temperature != null ? `${s.temperature.toFixed(1)}°` : "--";
      const h = s.humidity != null ? `${s.humidity.toFixed(0)}%` : "--";
      const off = s.online === false ? " offline" : "";
      const sel = state.selected === s.id ? " selected" : "";
      const batt =
        s.battery != null && s.battery < 20 ? ` <span class="warn">${s.battery}%</span>` : "";
      return `
        <li class="sensor${sel}${off}" data-id="${s.id}">
          <div class="sensor-main">
            <span class="sensor-name">${s.name}</span>
            <span class="sensor-age">${fmtAge(s.ts)}</span>
          </div>
          <div class="sensor-vals">
            <span class="v-temp">${t}</span>
            <span class="v-hum">${h}</span>${batt}
          </div>
          <canvas class="spark" width="120" height="26" data-id="${s.id}"></canvas>
        </li>`;
    })
    .join("");

  list.querySelectorAll("li.sensor").forEach((li) => {
    li.addEventListener("click", () => {
      const id = li.dataset.id;
      scene.focusSensor(id);
      scene.select(id);
    });
  });

  for (const s of state.readings) drawSparkline(s.id);
}

/**
 * Sparklines in the side list.
 *
 * History is cached per sensor+range because the list redraws on every
 * selection change; without this, hovering through the list would re-request
 * every sensor's series each time.
 */
const historyCache = new Map();

function cacheKey(sensorId, hours) {
  return `${sensorId}@${hours}`;
}

async function fetchHistory(sensorId, hours) {
  const key = cacheKey(sensorId, hours);
  if (historyCache.has(key)) return historyCache.get(key);
  const data = await getJSON(
    `/api/history/${encodeURIComponent(sensorId)}?hours=${hours}`
  );
  historyCache.set(key, data);
  return data;
}

function drawSparkline(sensorId) {
  const target = document.querySelector(`.spark[data-id="${sensorId}"]`);
  if (!target) return;

  const ctx = target.getContext("2d");
  const w = target.width;
  const h = target.height;
  ctx.clearRect(0, 0, w, h);

  fetchHistory(sensorId, state.hours)
    .then((data) => {
      if (data.points.length > 1) drawSeries(ctx, data.points, w, h, true);
    })
    .catch(() => {});
}

function drawSeries(ctx, points, w, h, spark) {
  const temps = points.map((p) => p.temperature).filter((v) => v != null);
  if (temps.length < 2) return;

  let min = Math.min(...temps);
  let max = Math.max(...temps);
  if (max - min < 1) { const mid = (max + min) / 2; min = mid - 0.5; max = mid + 0.5; }
  const pad = (max - min) * 0.15;
  min -= pad; max += pad;

  const t0 = points[0].ts;
  const t1 = points[points.length - 1].ts || t0 + 1;
  const span = Math.max(1, t1 - t0);

  const x = (ts) => ((ts - t0) / span) * (w - 2) + 1;
  const y = (v) => h - 2 - ((v - min) / (max - min)) * (h - 4);

  ctx.beginPath();
  let started = false;
  for (const p of points) {
    if (p.temperature == null) continue;
    const px = x(p.ts);
    const py = y(p.temperature);
    if (!started) { ctx.moveTo(px, py); started = true; } else ctx.lineTo(px, py);
  }
  ctx.strokeStyle = spark ? "rgba(200,214,150,0.75)" : "#c8d696";
  ctx.lineWidth = spark ? 1.2 : 1.8;
  ctx.stroke();

  if (!spark) {
    ctx.lineTo(w - 1, h);
    ctx.lineTo(1, h);
    ctx.closePath();
    ctx.fillStyle = "rgba(200,214,150,0.12)";
    ctx.fill();
  }
}

function drawChart(history) {
  const canvas = $("#detail-chart");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const w = canvas.width;
  const h = canvas.height;
  ctx.clearRect(0, 0, w, h);

  const temps = history.points.map((p) => p.temperature).filter((v) => v != null);
  if (temps.length < 2) {
    ctx.fillStyle = "rgba(200,208,176,0.5)";
    ctx.font = "13px ui-monospace, Menlo, monospace";
    ctx.textAlign = "center";
    ctx.fillText("not enough history yet", w / 2, h / 2);
    return;
  }

  // Gridlines at whole degrees.
  let min = Math.floor(Math.min(...temps));
  let max = Math.ceil(Math.max(...temps));
  if (max === min) max = min + 1;

  ctx.strokeStyle = "rgba(255,255,255,0.07)";
  ctx.fillStyle = "rgba(200,208,176,0.55)";
  ctx.font = "10px ui-monospace, Menlo, monospace";
  ctx.textAlign = "right";
  ctx.lineWidth = 1;

  for (let v = min; v <= max; v++) {
    const y = h - ((v - min) / (max - min)) * (h - 8) - 4;
    ctx.beginPath();
    ctx.moveTo(34, y);
    ctx.lineTo(w - 4, y);
    ctx.stroke();
    ctx.fillText(`${v}°`, 30, y + 3);
  }

  const t0 = history.points[0].ts;
  const t1 = history.points[history.points.length - 1].ts || t0 + 1;
  const span = Math.max(1, t1 - t0);
  const X = (ts) => 34 + ((ts - t0) / span) * (w - 38);
  const Y = (v) => h - ((v - min) / (max - min)) * (h - 8) - 4;

  ctx.beginPath();
  let started = false;
  for (const p of history.points) {
    if (p.temperature == null) continue;
    if (!started) { ctx.moveTo(X(p.ts), Y(p.temperature)); started = true; }
    else ctx.lineTo(X(p.ts), Y(p.temperature));
  }
  ctx.strokeStyle = "#c8d696";
  ctx.lineWidth = 1.8;
  ctx.stroke();

  ctx.lineTo(X(t1), h);
  ctx.lineTo(X(t0), h);
  ctx.closePath();
  ctx.fillStyle = "rgba(200,214,150,0.1)";
  ctx.fill();

  // Time axis labels.
  ctx.fillStyle = "rgba(200,208,176,0.55)";
  ctx.textAlign = "left";
  ctx.fillText(fmtClock(t0), 34, h - 2);
  ctx.textAlign = "right";
  ctx.fillText(fmtClock(t1), w - 4, h - 2);
}

function fmtClock(ts) {
  return new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function renderLevelToggles() {
  const host = $("#level-toggles");
  host.innerHTML = state.house.levels
    .map(
      (lv) => `
      <label class="level-toggle">
        <input type="checkbox" data-level="${lv.index}" checked>
        <span>${lv.name}</span>
        <em>${lv.elevation} ft</em>
      </label>`
    )
    .join("");

  host.querySelectorAll("input").forEach((input) => {
    input.addEventListener("change", () => {
      scene.setLevelVisible(Number(input.dataset.level), input.checked);
    });
  });

  const meta = state.house.meta;
  $("#building-meta").textContent =
    `${meta.footprint_ft[0]}×${meta.footprint_ft[1]} ft · ${meta.levels} levels · ` +
    `${meta.footprint_sqft} sq ft/level · ${meta.conditioned_sqft} sq ft conditioned`;
}

function buildLegend() {
  const stops = [];
  for (let i = 0; i <= 10; i++) {
    const t = i / 10;
    // Mirror rampColor from scene.js.
    const RAMP = [
      [0.0, [0x2c, 0x5f, 0x8f]], [0.25, [0x4a, 0x8f, 0x9c]],
      [0.5, [0x8f, 0xa3, 0x6b]], [0.75, [0xc4, 0x9a, 0x4a]],
      [1.0, [0xb5, 0x4a, 0x2a]],
    ];
    let c = RAMP[RAMP.length - 1][1];
    for (let k = 1; k < RAMP.length; k++) {
      if (t <= RAMP[k][0]) {
        const [ta, ca] = RAMP[k - 1];
        const [tb, cb] = RAMP[k];
        const f = (t - ta) / (tb - ta);
        c = ca.map((v, idx) => Math.round(v + (cb[idx] - v) * f));
        break;
      }
    }
    stops.push(`rgb(${c[0]},${c[1]},${c[2]}) ${t * 100}%`);
  }
  $("#legend-bar").style.background = `linear-gradient(to right, ${stops.join(",")})`;
}

async function renderStatusDot() {
  const dot = $("#status-dot");
  const text = $("#status-text");
  try {
    const s = await getJSON("/api/status");
    if (s.credentials && s.authorised && s.reachable) {
      dot.className = "dot ok";
      text.textContent = `${s.th_sensors ?? 0} TH sensors · ${s.devices ?? 0} devices`;
    } else if (s.credentials && s.authorised) {
      dot.className = "dot warn";
      text.textContent = s.error ? "API error" : "authorised, not reachable";
    } else {
      dot.className = "dot off";
      text.textContent = "awaiting authorisation";
    }
  } catch {
    dot.className = "dot off";
    text.textContent = "status unavailable";
  }
}

function handleSelect(payload) {
  const detail = $("#detail");
  if (payload.type === "sensor" && payload.sensor) {
    state.selected = payload.sensor.id;
    renderSensorList();
    const s = state.readings.find((r) => r.id === payload.sensor.id) || {};
    $("#detail-title").textContent = payload.sensor.name;
    $("#detail-sub").textContent =
      `level ${payload.sensor.level} · ${payload.sensor.room_id} · ` +
      `x ${payload.sensor.x} z ${payload.sensor.z} ft · ${fmtAge(s.ts)}`;
    $("#detail-temp").textContent = s.temperature != null ? `${s.temperature.toFixed(1)}°C` : "--";
    $("#detail-hum").textContent = s.humidity != null ? `${s.humidity.toFixed(0)}%` : "--";
    $("#detail-batt").textContent = s.battery != null ? `${s.battery}%` : "--";
    detail.classList.add("open");
    loadHistory(payload.sensor.id);
  } else {
    state.selected = null;
    detail.classList.remove("open");
    renderSensorList();
  }
}

// ── layout editing ───────────────────────────────────────────────────────────
//
// Sensor positions are guesses until someone measures the real room, so the
// editor lets you drag each sensor where it actually lives and write the result
// to house_layout.json. Room rectangles can be edited in the same file by hand.

let editMode = false;
const pendingPlacements = new Map();

async function saveLayout() {
  if (!pendingPlacements.size) {
    toast("nothing to save");
    return;
  }
  const payload = { ...state.house };
  payload.sensors = payload.sensors.map((s) => {
    const override = pendingPlacements.get(s.id);
    return override ? { ...s, ...override } : s;
  });

  const btn = $("#save-layout");
  btn.disabled = true;
  btn.textContent = "saving…";
  try {
    const response = await fetch("/api/layout", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) {
      toast(`rejected: ${data.error || response.status}`);
      return;
    }
    pendingPlacements.clear();
    toast(`saved to ${data.saved.split("/").pop()}`);
    await loadHouse();
  } catch (err) {
    toast(`save failed: ${err.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = "Save layout";
  }
}

function toast(message, bad = false) {
  const el = $("#toast");
  el.textContent = message;
  el.className = bad ? "show bad" : "show";
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { el.className = ""; }, 3200);
}

function setEditMode(on) {
  editMode = on;
  scene.setEditMode(on);
  $("#edit-toggle").classList.toggle("active", on);
  $("#edit-panel").classList.toggle("open", on);
  $("#view").style.pointerEvents = "auto";
  if (!on && pendingPlacements.size) {
    toast(`${pendingPlacements.size} unsaved change(s) — press Save layout`);
  }
}

// ── boot ─────────────────────────────────────────────────────────────────────

function wireControls() {
  $("#explode").addEventListener("input", (e) => {
    scene.setExplode(Number(e.target.value) / 100);
  });

  $("#range").addEventListener("change", (e) => {
    state.hours = Number(e.target.value);
    historyCache.clear();
    renderSensorList();
    if (state.selected) loadHistory(state.selected);
  });

  $("#detail-close").addEventListener("click", () => {
    scene.select(null);
  });

  $("#reset-view").addEventListener("click", () => {
    scene.frameBuilding();
  });

  $("#edit-toggle").addEventListener("click", () => setEditMode(!editMode));
  $("#save-layout").addEventListener("click", saveLayout);
  $("#discard-layout").addEventListener("click", async () => {
    pendingPlacements.clear();
    await loadHouse();
    toast("discarded unsaved changes");
  });
}

async function main() {
  wireControls();
  await loadHouse();
  await loadReadings();
  await renderStatusDot();
  setInterval(loadReadings, REFRESH_MS);
}

// Exposed for console poking and automated visual checks.
window.houseTwin = { scene, state, reload: loadReadings };

main().catch((err) => {
  console.error(err);
  document.body.insertAdjacentHTML(
    "afterbegin",
    `<div class="fatal">Failed to start: ${err.message}</div>`
  );
});
