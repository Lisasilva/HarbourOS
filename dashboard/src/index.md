---
title: Live ship traffic
---

<link rel="stylesheet" href="npm:maplibre-gl@5/dist/maplibre-gl.css">

```js
import * as maplibreModule from "npm:maplibre-gl@5";
// MapLibre 5, whose single bundle carries its own worker; version 6 loads the
// worker as a separate file, which Framework does not copy into the build.
const maplibregl = maplibreModule.default ?? maplibreModule;
```

```js
const calls = FileAttachment("data/port_calls.csv").csv({typed: true});
const shipRows = FileAttachment("data/ships.csv").csv({typed: true});
const trackTable = FileAttachment("data/tracks.parquet").parquet();
const freshness = FileAttachment("data/freshness.json").json();
const reliability = FileAttachment("data/reliability.json").json();
```

```js
// Ship groups, in a fixed order, with the colour each one keeps everywhere on
// the page. Livelier steps of Maria's palette hues (navy, red, tan, slate,
// coffee), checked with the dataviz palette validator for colour-blind
// separation; the originals are too muted to tell apart as small map marks.
const GROUPS = [
  {key: "Cargo", color: "#2F5F98", categories: ["cargo"]},
  {key: "Tanker", color: "#B0413E", categories: ["tanker"]},
  {key: "Passenger & ferries", color: "#C98F2E", categories: ["passenger"]},
  {key: "Fishing", color: "#1F93AE", categories: ["fishing"]},
  {key: "Tugs & service", color: "#9A5A2C", categories: ["towing / tug", "pilot, rescue, service"]},
  {key: "Other", color: "#8E8A84", categories: []}
];
const groupColor = Object.fromEntries(GROUPS.map((g) => [g.key, g.color]));
const groupOf = (category) => GROUPS.find((g) => g.categories.includes(category))?.key ?? "Other";

// pandas writes booleans as True/False, which d3's type guessing leaves as text.
const isTrue = (v) => v === true || v === "True" || v === "true";

const DAY = 864e5;
const latest = freshness.latest_reading ? new Date(freshness.latest_reading) : new Date();
const weekAgo = new Date(+latest - 7 * DAY);
// A ship seen within this long of the newest reading is "on the map now".
const LIVE_WINDOW_MS = 2 * 36e5;

const ships = shipRows.map((d) => ({
  ...d,
  group: groupOf(d.ship_category),
  in_port_now: isTrue(d.in_port_now),
  moving: d.speed_over_ground >= 1
}));
const shipByMmsi = new Map(ships.map((d) => [d.mmsi, d]));

const allCalls = calls.map((d) => ({
  ...d,
  group: groupOf(d.ship_category),
  in_port_now: isTrue(d.in_port_now)
}));
const callsByMmsi = d3.group(allCalls, (d) => d.mmsi);
const weekCalls = allCalls.filter((d) => d.visit_type === "port_call" && d.berth_start >= weekAgo);

// One entry per port with a call in the last 7 days, for the port markers.
const ports = d3
  .rollups(
    weekCalls.filter((d) => d.port_locode),
    (v) => ({
      locode: v[0].port_locode,
      name: v[0].port_name,
      lat: v[0].port_latitude,
      lon: v[0].port_longitude,
      calls: v.length
    }),
    (d) => d.port_locode
  )
  .map(([, p]) => p);
const portByLocode = new Map(ports.map((p) => [p.locode, p]));
```

```js
// Routes: the last 3 days, sorted by ship and time. Index each ship's slice
// of the columns so a route or a replay position is a quick lookup.
const tracks = (() => {
  const mmsi = trackTable.getChild("mmsi").toArray();
  const n = mmsi.length;
  const index = new Map();
  let start = 0;
  for (let i = 1; i <= n; i++) {
    if (i === n || mmsi[i] !== mmsi[start]) {
      index.set(Number(mmsi[start]), [start, i]);
      start = i;
    }
  }
  return {
    index,
    t: trackTable.getChild("t").toArray(),
    lat: trackTable.getChild("lat").toArray(),
    lon: trackTable.getChild("lon").toArray(),
    sog: trackTable.getChild("sog").toArray(),
    cog: trackTable.getChild("cog").toArray(),
    afterGap: Array.from(trackTable.getChild("after_gap"), Boolean)
  };
})();

const trackEnd = tracks.t.length ? d3.max(tracks.t) : Math.floor(+latest / 1000);
const REPLAY_HOURS = 24;
const GAP_SECONDS = 30 * 60;

// Where every ship was at time T (seconds): its last recorded point at or
// before T. Points where a ship lay still were dropped from the file, so a
// ship keeps its last point until the next one, unless that next point comes
// after a gap in its sightings, or T is past its last sighting.
function positionsAt(T) {
  const out = [];
  for (const [mmsi, [s, e]] of tracks.index) {
    if (tracks.t[s] > T) continue;
    let lo = s, hi = e - 1;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (tracks.t[mid] <= T) lo = mid;
      else hi = mid - 1;
    }
    const age = T - tracks.t[lo];
    if (age > GAP_SECONDS && (lo === e - 1 || tracks.afterGap[lo + 1])) continue;
    const ship = shipByMmsi.get(mmsi);
    out.push({
      mmsi,
      name: ship?.vessel_name ?? "",
      group: ship?.group ?? "Other",
      lat: tracks.lat[lo],
      lon: tracks.lon[lo],
      cog: tracks.cog[lo],
      sog: age > GAP_SECONDS ? 0 : tracks.sog[lo],
      moving: age <= GAP_SECONDS && tracks.sog[lo] >= 1
    });
  }
  return out;
}

function routeOf(mmsi) {
  const range = tracks.index.get(mmsi);
  if (!range) return [];
  const [s, e] = range;
  return d3.range(s, e).map((i) => ({t: tracks.t[i], lat: tracks.lat[i], lon: tracks.lon[i], afterGap: tracks.afterGap[i]}));
}

const liveShips = ships
  .filter((d) => +latest - d.last_seen <= LIVE_WINDOW_MS)
  .map((d) => ({
    mmsi: d.mmsi,
    name: d.vessel_name ?? "",
    group: d.group,
    lat: d.latitude,
    lon: d.longitude,
    cog: d.course_over_ground ?? 0,
    sog: d.speed_over_ground,
    moving: d.moving
  }));
```

```js
// Formatting helpers.
const ago = (ms) => {
  const h = ms / 36e5;
  return h < 1 ? `${Math.max(1, Math.round(h * 60))} min ago` : h < 48 ? `${Math.round(h)} h ago` : `${Math.round(h / 24)} days ago`;
};
// Every time on the page is Norway's time (CET in winter, CEST in summer),
// wherever the visitor is, since that's where the ships are.
const TZ = "Europe/Oslo";
const when = (d) => d.toLocaleString("en-GB", {weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone: TZ});
const weekday = (d) => d.toLocaleDateString("en-GB", {weekday: "short", timeZone: TZ});
const hourFormat = new Intl.DateTimeFormat("en-GB", {hour: "numeric", hourCycle: "h23", timeZone: TZ});
const norwayHour = (d) => +hourFormat.format(d);
const stay = (minutes) => (minutes < 90 ? `${Math.round(minutes)} min` : minutes < 48 * 60 ? `${d3.format(".1f")(minutes / 60)} h` : `${d3.format(".1f")(minutes / 1440)} days`);
const confidenceBand = (c) => (c >= 0.8 ? "high" : c >= 0.5 ? "medium" : "low");
const compass = (deg) => ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][Math.round((((deg % 360) + 360) % 360) / 45) % 8];
const shipName = (d) => d?.vessel_name || d?.name || `Unnamed ship (MMSI ${d?.mmsi})`;
const haversineKm = (a, b) => {
  const r = Math.PI / 180;
  const x = Math.sin(((b.lat - a.lat) * r) / 2) ** 2 + Math.cos(a.lat * r) * Math.cos(b.lat * r) * Math.sin(((b.lon - a.lon) * r) / 2) ** 2;
  return 12742 * Math.asin(Math.sqrt(x));
};
```

```js
// What is selected on the map: a ship, a port, or nothing.
const selection = Mutable(null);
const select = (value) => {
  selection.value = value;
};
```

```js
// A new run publishes about every 5.5 hours, so data older than 7 hours means
// a run is late or failing.
const STALE_MS = 7 * 36e5;
const minuteClock = Generators.observe((notify) => {
  notify(Date.now());
  const id = setInterval(() => notify(Date.now()), 60_000);
  return () => clearInterval(id);
});
```

<section class="hero">
  <h1>HarbourOS</h1>
  <p>Live ship traffic and port calls along the Norwegian coast, from the positions ships broadcast every few seconds.</p>
  ${freshness.latest_reading
    ? html`<span class="fresh ${minuteClock - latest > STALE_MS ? "stale" : ""}"><span class="dot"></span>Data last updated ${when(latest)} Norway time (${ago(minuteClock - latest)})${minuteClock - latest > STALE_MS ? " · updates may be paused" : ""}</span>`
    : html`<span class="fresh stale"><span class="dot"></span>No data yet</span>`}
  ${sea}
</section>

```js
// The header's sea: three rows of waves rolling past at different speeds, a
// small sailing ship rocking between them, and two gulls. Each wave is drawn
// twice as wide as the header and slid left by half its width on a loop, so
// it never visibly restarts. Visitors who ask for reduced motion get a still
// picture (see style.css).
const sea = (() => {
  const wave = (base, amp, count, phase) => {
    const points = d3.range(0, 2401, 20).map((x) => {
      const a = (2 * Math.PI * count * x) / 1200 + phase;
      return `${x} ${(base + amp * Math.sin(a) + amp * 0.35 * Math.sin(2 * a + phase)).toFixed(1)}`;
    });
    return `M${points.join(" L")} V90 H0 Z`;
  };
  const row = (name, d, fill) => {
    const el = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    el.setAttribute("class", `wave ${name}`);
    el.setAttribute("viewBox", "0 0 2400 90");
    el.setAttribute("preserveAspectRatio", "none");
    el.innerHTML = `<path d="${d}" style="fill: ${fill}"/>`;
    return el;
  };
  return html`<div class="sea" aria-hidden="true">
    <svg class="gulls" viewBox="0 0 60 24">
      <path d="M2 10 Q7 4 12 10 Q17 4 22 10"/>
      <path d="M34 18 Q38 13 42 18 Q46 13 50 18"/>
    </svg>
    ${row("back", wave(34, 7, 2, 0), "#3f5a80")}
    ${row("mid", wave(46, 6, 3, 1.3), "#617891")}
    <div class="ship-drift"><div class="ship-rock">
      <svg class="ship" viewBox="0 0 140 120">
        <path d="M70 12 V90" stroke="#3a2a20" stroke-width="2.6" stroke-linecap="round"/>
        <path d="M70 12 L86 16.5 L70 21 Z" fill="#8a2c31"/>
        <path d="M73 20 C 99 38, 108 62, 101 84 L73 84 Z" fill="#fbf6ee"/>
        <path d="M73 20 C 90 36, 97 55, 95 72" fill="none" stroke="#e3d6c1" stroke-width="1.2"/>
        <path d="M67 26 C 52 44, 40 64, 28 84 L67 84 Z" fill="#d5b893"/>
        <path d="M8 88 L134 84 Q126 102 110 108 L34 108 Q18 102 8 88 Z" fill="#6f4d38"/>
        <path d="M8 88 L134 84 L132 89 L10 93 Z" fill="#632024"/>
        <circle cx="52" cy="97" r="2.4" fill="#d5b893"/>
        <circle cx="70" cy="96.5" r="2.4" fill="#d5b893"/>
        <circle cx="88" cy="96" r="2.4" fill="#d5b893"/>
      </svg>
    </div></div>
    ${row("front", wave(60, 5, 2, 2.4), "#f8f3ea")}
  </div>`;
})();
```

```js
const groupCounts = d3.rollup(liveShips, (v) => v.length, (d) => d.group);
const groupInput = Inputs.checkbox(
  GROUPS.map((g) => g.key),
  {
    value: GROUPS.map((g) => g.key),
    format: (key) => html`<span class="swatch" style="background:${groupColor[key]}"></span>${key} <small>${d3.format(",")(groupCounts.get(key) ?? 0)}</small>`
  }
);
const groupPick = Generators.input(groupInput);
```

<div class="chips">${groupInput}</div>

```js
const activeGroups = new Set(groupPick);
const liveShown = liveShips.filter((d) => activeGroups.has(d.group));
const weekShown = weekCalls.filter((d) => activeGroups.has(d.group));
const inPortShown = ships.filter((d) => d.in_port_now && activeGroups.has(d.group) && +latest - d.last_seen <= LIVE_WINDOW_MS);
```

<div class="kpis">
  <div class="card kpi">
    <div class="label">Ships on the map now</div>
    <div class="value">${d3.format(",")(liveShown.length)}</div>
    <div class="kpi-note">seen in the last 2 hours</div>
  </div>
  <div class="card kpi">
    <div class="label">Under way</div>
    <div class="value">${d3.format(",")(liveShown.filter((d) => d.moving).length)}</div>
    <div class="kpi-note">moving at 1 knot or more</div>
  </div>
  <div class="card kpi">
    <div class="label">In port now</div>
    <div class="value">${d3.format(",")(inPortShown.length)}</div>
    <div class="kpi-note">arrived and not yet seen leaving</div>
  </div>
  <div class="card kpi">
    <div class="label">Port calls this week</div>
    <div class="value">${d3.format(",")(weekShown.length)}</div>
    <div class="kpi-note">at ${d3.format(",")(new Set(weekShown.map((d) => d.port_locode)).size)} ports</div>
  </div>
</div>

```js
const WATER = "#c9d7e4";
const LAND = "#f5eee2";

// The base map is OpenFreeMap's free vector tiles (no account or key),
// recoloured into the palette. If they can't be reached within a few seconds,
// the ships are still drawn on plain water.
async function baseStyle() {
  try {
    const response = await fetch("https://tiles.openfreemap.org/styles/positron", {signal: AbortSignal.timeout(8000)});
    if (!response.ok) throw new Error(response.statusText);
    const style = await response.json();
    for (const layer of style.layers) {
      const id = layer.id;
      const paint = (layer.paint ??= {});
      if (layer.type === "background") paint["background-color"] = LAND;
      else if (layer.type === "fill" && /water|ocean|sea/.test(id)) paint["fill-color"] = WATER;
      else if (layer.type === "line" && /water|river/.test(id)) paint["line-color"] = "#b9c9d9";
      else if (layer.type === "fill" && /building/.test(id)) paint["fill-color"] = "#ebe0cc";
      else if (layer.type === "fill") paint["fill-color"] = "#efe5d3";
      else if (layer.type === "line" && /boundary|admin/.test(id)) paint["line-color"] = "#9aa9bb";
      else if (layer.type === "line") paint["line-color"] = "#e6d8bf";
      else if (layer.type === "symbol") {
        paint["text-color"] = /water|ocean|sea|marine/.test(id) ? "#617891" : "#4a5972";
        paint["text-halo-color"] = LAND;
      }
    }
    return style;
  } catch {
    return {version: 8, sources: {}, layers: [{id: "background", type: "background", paint: {"background-color": WATER}}]};
  }
}

// Ship icons, drawn once per group colour: a slim arrow for a moving ship
// (pointing where it is heading), and a chevron for the direction marks along
// a route. Stopped ships are plain dots, drawn by MapLibre itself.
function arrowIcon(color) {
  const size = 40;
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = size;
  const c = canvas.getContext("2d");
  c.beginPath();
  c.moveTo(20, 3);
  c.lineTo(31, 35);
  c.lineTo(20, 28);
  c.lineTo(9, 35);
  c.closePath();
  c.lineJoin = "round";
  c.lineWidth = 3;
  c.strokeStyle = "#fffdf9";
  c.stroke();
  c.fillStyle = color;
  c.fill();
  return c.getImageData(0, 0, size, size);
}

function chevronIcon(color) {
  const size = 24;
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = size;
  const c = canvas.getContext("2d");
  c.beginPath();
  c.moveTo(6, 17);
  c.lineTo(12, 7);
  c.lineTo(18, 17);
  c.lineCap = c.lineJoin = "round";
  c.lineWidth = 5;
  c.strokeStyle = "#fffdf9";
  c.stroke();
  c.lineWidth = 2.5;
  c.strokeStyle = color;
  c.stroke();
  return c.getImageData(0, 0, size, size);
}

const empty = {type: "FeatureCollection", features: []};
```

```js
const mapCard = html`<div class="map-card">
  <div class="map-canvas"></div>
  <div class="map-key">
    <span><svg width="14" height="14" viewBox="0 0 40 40"><path d="M20 3 L31 35 L20 28 L9 35 Z" fill="#25344f"/></svg> moving</span>
    <span><svg width="10" height="10"><circle cx="5" cy="5" r="4" fill="#25344f"/></svg> stopped</span>
    <span><svg width="14" height="14"><circle cx="7" cy="7" r="5.5" fill="rgb(99 32 36 / 0.15)" stroke="#632024" stroke-width="1.5"/></svg> port</span>
  </div>
  <div class="map-search"></div>
  <aside class="map-panel"></aside>
  <div class="map-replay"></div>
</div>`;
// The map's starting view, which the reset button goes back to.
const HOME_VIEW = {center: [13, 64.2], zoom: 4.1};
```

<div class="map-wrap">${mapCard}</div>

```js
// If the browser can't draw the map at all (no WebGL, say), `map` is null:
// the map area says so, and the rest of the page works without it.
const map = await (async () => {
  const canvas = mapCard.querySelector(".map-canvas");
  let m;
  try {
    m = new maplibregl.Map({
      container: canvas,
      style: await baseStyle(),
      center: [13, 64.2],
      zoom: 4.1,
      minZoom: 3,
      maxZoom: 14,
      attributionControl: false
    });
  } catch (error) {
    console.error(error);
    canvas.replaceChildren(html`<p class="map-unavailable">The map can't be shown in this browser. The charts below still work.</p>`);
    return null;
  }
  m.addControl(new maplibregl.AttributionControl({compact: true}), "bottom-left");
  m.addControl(new maplibregl.NavigationControl({showCompass: false}), "top-left");
  // A button under the zoom buttons that goes back to the whole coast, for
  // when a visitor has zoomed or panned too far to tell where they are.
  m.addControl(
    {
      onAdd() {
        const group = html`<div class="maplibregl-ctrl maplibregl-ctrl-group">
          <button type="button" class="reset-view" title="Show the whole coast" aria-label="Show the whole coast">
            <svg viewBox="0 0 20 20" width="16" height="16" aria-hidden="true"><path d="M3 7V3h4M13 3h4v4M17 13v4h-4M7 17H3v-4" fill="none" stroke="#25344f" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>
          </button>
        </div>`;
        group.querySelector("button").onclick = () => m.flyTo({...HOME_VIEW, duration: 900});
        return group;
      },
      onRemove() {}
    },
    "top-left"
  );
  invalidation.then(() => m.remove());
  await new Promise((resolve) => m.on("load", resolve));
  // For the preview workflow's screenshots (dashboard/scripts/screenshots.mjs).
  window.__harbourMap = m;

  for (const g of GROUPS) {
    m.addImage(`arrow-${g.key}`, arrowIcon(g.color), {pixelRatio: 2});
    m.addImage(`chevron-${g.key}`, chevronIcon(g.color), {pixelRatio: 2});
  }

  m.addSource("ports", {type: "geojson", data: empty});
  m.addSource("route", {type: "geojson", data: empty});
  m.addSource("route-stops", {type: "geojson", data: empty});
  m.addSource("ships", {type: "geojson", data: empty});

  m.addLayer({
    id: "ports",
    type: "circle",
    source: "ports",
    paint: {
      "circle-radius": ["interpolate", ["linear"], ["sqrt", ["get", "calls"]], 1, 3, 30, 14],
      "circle-color": "rgba(99, 32, 36, 0.14)",
      "circle-stroke-color": "#632024",
      "circle-stroke-width": 1.2,
      "circle-stroke-opacity": 0.7
    }
  });
  m.addLayer({
    id: "route-line",
    type: "line",
    source: "route",
    layout: {"line-cap": "round", "line-join": "round"},
    paint: {"line-color": ["get", "color"], "line-width": 3, "line-opacity": 0.85}
  });
  m.addLayer({
    id: "route-arrows",
    type: "symbol",
    source: "route",
    layout: {
      "symbol-placement": "line",
      "symbol-spacing": 70,
      "icon-image": ["concat", "chevron-", ["get", "group"]],
      "icon-rotate": 90,
      "icon-rotation-alignment": "map",
      "icon-allow-overlap": true
    }
  });
  m.addLayer({
    id: "route-stops",
    type: "circle",
    source: "route-stops",
    paint: {"circle-radius": 5, "circle-color": "#fffdf9", "circle-stroke-color": "#25344f", "circle-stroke-width": 2}
  });
  m.addLayer({
    id: "ships-stopped",
    type: "circle",
    source: "ships",
    filter: ["!", ["get", "moving"]],
    paint: {
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 4, 2.6, 8, 4.5, 12, 6.5],
      "circle-color": ["get", "color"],
      "circle-stroke-color": "#fffdf9",
      "circle-stroke-width": ["interpolate", ["linear"], ["zoom"], 4, 0.5, 8, 1.2]
    }
  });
  m.addLayer({
    id: "ships-moving",
    type: "symbol",
    source: "ships",
    filter: ["get", "moving"],
    layout: {
      "icon-image": ["concat", "arrow-", ["get", "group"]],
      "icon-rotate": ["get", "cog"],
      "icon-rotation-alignment": "map",
      "icon-allow-overlap": true,
      "icon-ignore-placement": true,
      "icon-size": ["interpolate", ["linear"], ["zoom"], 4, 0.6, 8, 0.9, 12, 1.2]
    }
  });
  m.addLayer({
    id: "ship-selected",
    type: "circle",
    source: "ships",
    filter: ["==", ["get", "mmsi"], -1],
    paint: {"circle-radius": 13, "circle-color": "rgba(0,0,0,0)", "circle-stroke-color": "#632024", "circle-stroke-width": 2.5}
  });

  // Hover: a small label with the ship or port name.
  const popup = new maplibregl.Popup({closeButton: false, closeOnClick: false, offset: 10});
  const hoverable = ["ships-moving", "ships-stopped", "ports"];
  m.on("mousemove", (e) => {
    const [f] = m.queryRenderedFeatures(e.point, {layers: hoverable});
    m.getCanvas().style.cursor = f ? "pointer" : "";
    if (!f) return popup.remove();
    const p = f.properties;
    // Ship names come from the ships themselves, so they go in as text, never HTML.
    const label = f.layer.id === "ports"
      ? html`<b>${p.name}</b><br>${p.calls} port calls this week`
      : html`<b>${p.name || "Unnamed ship"}</b><br>${p.group} · ${p.moving ? `${(+p.sog).toFixed(1)} kn` : "stopped"}`;
    popup.setLngLat(f.geometry.coordinates).setDOMContent(label).addTo(m);
  });
  m.on("mouseout", () => popup.remove());

  // Click: a ship wins over a port; empty water clears the selection.
  m.on("click", (e) => {
    const box = [[e.point.x - 6, e.point.y - 6], [e.point.x + 6, e.point.y + 6]];
    const [ship] = m.queryRenderedFeatures(box, {layers: ["ships-moving", "ships-stopped"]});
    if (ship) return select({kind: "ship", mmsi: ship.properties.mmsi});
    const [port] = m.queryRenderedFeatures(box, {layers: ["ports"]});
    if (port) return select({kind: "port", locode: port.properties.locode});
    select(null);
  });
  return m;
})();
```

```js
// The replay bar: "Live" shows the latest positions; the slider (or Play)
// steps through the last 24 hours, 10 minutes at a time.
const replayInput = (() => {
  const max = trackEnd;
  const min = max - REPLAY_HOURS * 3600;
  const form = html`<form class="replay" onsubmit="return false">
    <button type="button" class="play" aria-label="Play the last 24 hours">▶ Play</button>
    <input type="range" min=${min} max=${max} step="600" value=${max} aria-label="Time">
    <output></output>
    <button type="button" class="live on">Live</button>
  </form>`;
  const [play, range, output, live] = [form.querySelector(".play"), form.querySelector("input"), form.querySelector("output"), form.querySelector(".live")];
  let timer = null;
  const label = () => {
    output.textContent = form.value == null ? `Live · ${when(latest)}` : when(new Date(form.value * 1000));
    live.classList.toggle("on", form.value == null);
    play.textContent = timer ? "❚❚ Pause" : "▶ Play";
  };
  const set = (value) => {
    form.value = value;
    label();
    form.dispatchEvent(new Event("input", {bubbles: true}));
  };
  const stop = () => {
    clearInterval(timer);
    timer = null;
    label();
  };
  range.addEventListener("input", (e) => {
    e.stopPropagation();
    set(+range.value);
  });
  live.addEventListener("click", () => {
    stop();
    range.value = max;
    set(null);
  });
  play.addEventListener("click", () => {
    if (timer) return stop();
    if (form.value == null || +range.value >= max) range.value = min;
    timer = setInterval(() => {
      const next = +range.value + 600;
      if (next > max) {
        stop();
        range.value = max;
        return set(null);
      }
      range.value = next;
      set(next);
    }, 180);
    set(+range.value);
  });
  invalidation.then(stop);
  form.value = null;
  label();
  return form;
})();
mapCard.querySelector(".map-replay").replaceChildren(replayInput);
const replayTime = Generators.input(replayInput);
```

```js
// Ships on the map: live positions, or the replay's moment in the last 24 hours.
if (map) {
  const shown = (replayTime == null ? liveShips : positionsAt(replayTime)).filter((d) => activeGroups.has(d.group));
  map.getSource("ships").setData({
    type: "FeatureCollection",
    features: shown.map((d) => ({
      type: "Feature",
      geometry: {type: "Point", coordinates: [d.lon, d.lat]},
      properties: {mmsi: d.mmsi, name: d.name, group: d.group, color: groupColor[d.group], cog: d.cog, sog: d.sog, moving: d.moving}
    }))
  });
  map.getSource("ports").setData({
    type: "FeatureCollection",
    features: ports.map((p) => ({
      type: "Feature",
      geometry: {type: "Point", coordinates: [p.lon, p.lat]},
      properties: {locode: p.locode, name: p.name, calls: p.calls}
    }))
  });
}
```

```js
// The selected ship's route (last 3 days) with direction chevrons, and the
// ports it stopped at along the way.
if (map) {
  const mmsi = selection?.kind === "ship" ? selection.mmsi : null;
  map.setFilter("ship-selected", ["==", ["get", "mmsi"], mmsi ?? -1]);
  const points = mmsi == null ? [] : routeOf(mmsi);
  const ship = shipByMmsi.get(mmsi);
  const group = ship?.group ?? "Other";
  // Break the line where the ship wasn't seen, rather than drawing a straight
  // line across land from where it vanished to where it reappeared.
  const segments = [];
  for (const p of points) {
    if (!segments.length || p.afterGap) segments.push([]);
    segments.at(-1).push([p.lon, p.lat]);
  }
  map.getSource("route").setData({
    type: "FeatureCollection",
    features: segments
      .filter((s) => s.length > 1)
      .map((coordinates) => ({type: "Feature", geometry: {type: "LineString", coordinates}, properties: {group, color: groupColor[group]}}))
  });
  const routeStart = points.length ? new Date(points[0].t * 1000) : latest;
  const stops = mmsi == null ? [] : (callsByMmsi.get(mmsi) ?? []).filter((c) => c.berth_end >= routeStart);
  map.getSource("route-stops").setData({
    type: "FeatureCollection",
    features: stops.map((c) => ({type: "Feature", geometry: {type: "Point", coordinates: [c.stop_longitude, c.stop_latitude]}, properties: {}}))
  });
  if (points.length > 1) {
    const bounds = points.reduce((b, p) => b.extend([p.lon, p.lat]), new maplibregl.LngLatBounds([points[0].lon, points[0].lat], [points[0].lon, points[0].lat]));
    map.fitBounds(bounds, {padding: {top: 60, bottom: 90, left: 60, right: window.innerWidth > 900 ? 390 : 60}, maxZoom: 10, duration: 900});
  } else if (ship) {
    map.flyTo({center: [ship.longitude, ship.latitude], zoom: Math.max(map.getZoom(), 8), duration: 900});
  } else if (selection?.kind === "port") {
    const port = portByLocode.get(selection.locode);
    if (port) map.flyTo({center: [port.lon, port.lat], zoom: Math.max(map.getZoom(), 9), duration: 900});
  }
}
```

```js
// The search box: type part of a port's or ship's name (or a ship's MMSI) and
// pick a match to open it, just as if it had been clicked on the map.
{
  const plain = (text) =>
    String(text ?? "")
      .toLowerCase()
      .normalize("NFD")
      .replace(/[̀-ͯ]/g, "")
      .replace(/ø/g, "o")
      .replace(/æ/g, "ae");
  const entries = [
    ...d3.sort(ports, (p) => -p.calls).map((p) => ({label: p.name, note: "Port", color: null, search: plain(p.name), pick: {kind: "port", locode: p.locode}})),
    ...ships.map((s) => ({label: shipName(s), note: s.group, color: groupColor[s.group], search: `${plain(shipName(s))} ${s.mmsi}`, pick: {kind: "ship", mmsi: s.mmsi}}))
  ];
  const input = html`<input type="search" placeholder="Find a port or ship" aria-label="Find a port or ship" autocomplete="off" spellcheck="false">`;
  const list = html`<ul class="results" role="listbox" hidden></ul>`;
  let matches = [];
  let active = 0;
  const choose = (m) => {
    select(m.pick);
    input.value = m.label;
    matches = [];
    show();
    input.blur();
  };
  const show = () => {
    list.replaceChildren(
      ...matches.map((m, i) => {
        const item = html`<li role="option" class=${i === active ? "active" : ""}>
          <span>${m.color ? html`<span class="swatch" style="background:${m.color}"></span>` : html`<span class="port-dot"></span>`}${m.label}</span>
          <span class="meta">${m.note}</span>
        </li>`;
        // pointerdown, not click, so the pick happens before the box loses focus.
        item.addEventListener("pointerdown", (e) => {
          e.preventDefault();
          choose(m);
        });
        return item;
      })
    );
    list.hidden = matches.length === 0;
  };
  input.addEventListener("input", () => {
    const q = plain(input.value.trim());
    active = 0;
    if (q.length < 2) matches = [];
    else {
      const starts = entries.filter((e) => e.search.startsWith(q));
      const within = entries.filter((e) => !e.search.startsWith(q) && e.search.includes(q));
      matches = [...starts, ...within].slice(0, 8);
    }
    show();
  });
  input.addEventListener("keydown", (e) => {
    if ((e.key === "ArrowDown" || e.key === "ArrowUp") && matches.length) {
      e.preventDefault();
      active = (active + (e.key === "ArrowDown" ? 1 : matches.length - 1)) % matches.length;
      show();
    } else if (e.key === "Enter" && matches.length) {
      e.preventDefault();
      choose(matches[active]);
    } else if (e.key === "Escape") {
      matches = [];
      show();
      input.blur();
    }
  });
  input.addEventListener("blur", () => {
    matches = [];
    show();
  });
  mapCard.querySelector(".map-search").replaceChildren(input, list);
}
```

```js
// The side panel: a ship's profile, a port's card, or a short guide.
{
  const panel = mapCard.querySelector(".map-panel");
  const close = () => html`<button class="close" aria-label="Close" onclick=${() => select(null)}>×</button>`;
  const shipButton = (s) => html`<button onclick=${() => select({kind: "ship", mmsi: s.mmsi})}><span class="swatch" style="background:${groupColor[s.group]}"></span>${shipName(s)}</button>`;

  if (selection?.kind === "ship") {
    const s = shipByMmsi.get(selection.mmsi);
    const visits = d3.sort(callsByMmsi.get(selection.mmsi) ?? [], (d) => -d.berth_start);
    const weekVisits = visits.filter((d) => d.berth_start >= weekAgo);
    const route = routeOf(selection.mmsi);
    const sailedKm = d3.sum(d3.pairs(route), ([a, b]) => (b.afterGap ? 0 : haversineKm(a, b)));
    const status = !s
      ? "Not seen in the last 7 days."
      : s.moving
      ? `Under way at ${s.speed_over_ground.toFixed(1)} knots, heading ${compass(s.course_over_ground)}.`
      : s.in_port_now
      ? `In port at ${s.last_port}.`
      : "Stopped.";
    panel.replaceChildren(html`${close()}
      <div class="meta"><span class="swatch" style="background:${groupColor[s?.group ?? "Other"]}"></span> ${s?.group ?? "Unknown type"} · MMSI ${selection.mmsi}</div>
      <h3>${shipName(s ?? {mmsi: selection.mmsi})}</h3>
      <div class="status">${status}${s ? html`<div class="meta">Last seen ${when(s.last_seen)} (${ago(minuteClock - s.last_seen)})</div>` : ""}</div>
      <div class="stats">
        <div><b>${d3.format(",.0f")(sailedKm / 1.852)}</b><span>nautical miles sailed (3 days)</span></div>
        <div><b>${weekVisits.filter((d) => d.visit_type === "port_call").length}</b><span>port calls this week</span></div>
        <div><b>${weekVisits.some((d) => d.completeness === "complete") ? stay(d3.median(weekVisits.filter((d) => d.completeness === "complete"), (d) => d.minutes_alongside)) : "–"}</b><span>typical stay</span></div>
      </div>
      ${weekVisits.length ? html`<h4>Stops this week</h4>${stopStrip(weekVisits)}` : ""}
      <h4>Latest stops</h4>
      ${visits.length
        ? html`<ul>${visits.slice(0, 6).map((v) => html`<li>
            <span>${v.port_name ?? html`<i>At sea</i>`}<br><span class="meta">${when(v.berth_start)} · ${v.in_port_now ? "still there" : stay(v.minutes_alongside)}</span></span>
            <span class="pill ${confidenceBand(v.confidence)}" title="How sure we are this was a real stop">${confidenceBand(v.confidence)}</span>
          </li>`)}</ul>`
        : html`<p class="meta">No stops recorded yet.</p>`}
    `);
  } else if (selection?.kind === "port") {
    const port = portByLocode.get(selection.locode);
    const portCalls = weekCalls.filter((d) => d.port_locode === selection.locode);
    const here = ships.filter((d) => d.in_port_now && d.last_port === port?.name && +latest - d.last_seen <= LIVE_WINDOW_MS);
    const finished = portCalls.filter((d) => d.completeness === "complete");
    panel.replaceChildren(html`${close()}
      <div class="meta">Port · ${selection.locode}</div>
      <h3>${port?.name ?? selection.locode}</h3>
      <div class="stats">
        <div><b>${portCalls.length}</b><span>port calls this week</span></div>
        <div><b>${here.length}</b><span>ships in port now</span></div>
        <div><b>${finished.length ? stay(d3.median(finished, (d) => d.minutes_alongside)) : "–"}</b><span>typical stay</span></div>
      </div>
      <h4>Arrivals by hour of day (Norway time)</h4>
      ${Plot.plot({
        height: 120,
        width: 300,
        marginLeft: 34,
        style: {fontSize: "10px", color: "#4a5972"},
        x: {label: null, tickFormat: (h) => `${h}:00`, ticks: [0, 6, 12, 18]},
        y: {label: null, grid: true, ticks: 3},
        marks: [
          Plot.rectY(portCalls, Plot.binX({y: "count"}, {x: (d) => norwayHour(d.berth_start), interval: 1, domain: [0, 24], fill: "#617891", inset: 1, rx: 2, tip: true})),
          Plot.ruleY([0], {stroke: "#d5b893"})
        ]
      })}
      <h4>Ship types this week</h4>
      ${mixBar(portCalls)}
      <h4>In port now</h4>
      ${here.length ? html`<ul>${here.slice(0, 10).map((s) => html`<li>${shipButton(s)}<span class="meta">${ago(minuteClock - s.last_seen)}</span></li>`)}</ul>` : html`<p class="meta">No ships in port right now.</p>`}
    `);
  } else {
    const busiest = d3
      .rollups(ships.filter((d) => d.in_port_now && d.last_port && +latest - d.last_seen <= LIVE_WINDOW_MS), (v) => v.length, (d) => d.last_port)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 6);
    const nameToLocode = new Map(ports.map((p) => [p.name, p.locode]));
    panel.replaceChildren(html`
      <h3>Explore the coast</h3>
      <p class="status">Click a ship to see its route over the last 3 days and where it stopped, or a port to see who is there. You can also search for either above. Press Play to watch every ship move over the last 24 hours.</p>
      <h4>Busiest ports right now</h4>
      <ul>${busiest.map(([name, n]) => html`<li><button onclick=${() => nameToLocode.has(name) && select({kind: "port", locode: nameToLocode.get(name)})}>${name}</button><span class="meta">${n} ships in port</span></li>`)}</ul>
    `);
  }
}
```

```js
// A ship's week as a strip: one bar per stop, placed in time, one row per port.
function stopStrip(visits) {
  const rows = d3.sort(new Set(visits.map((d) => d.port_name ?? "At sea")));
  return Plot.plot({
    height: 28 + 20 * rows.length,
    width: 300,
    marginLeft: 90,
    marginRight: 8,
    style: {fontSize: "10px", color: "#4a5972"},
    x: {domain: [weekAgo, latest], label: null, ticks: 4, tickFormat: weekday},
    y: {domain: rows, label: null},
    marks: [
      Plot.ruleX([weekAgo, latest], {stroke: "#efe5d4"}),
      Plot.barX(visits, {
        x1: "berth_start",
        x2: (d) => (d.in_port_now ? latest : d.berth_end),
        y: (d) => d.port_name ?? "At sea",
        fill: (d) => ({high: "#25344F", medium: "#617891", low: "#D5B893"})[confidenceBand(d.confidence)],
        insetTop: 3,
        insetBottom: 3,
        rx: 3,
        title: (d) => `${d.port_name ?? "At sea"}\n${when(d.berth_start)}\n${d.in_port_now ? "still there" : stay(d.minutes_alongside)} · ${confidenceBand(d.confidence)} confidence`
      }),
      // A dot at each arrival, so short stops still show at a week's scale.
      Plot.dot(visits, {
        x: "berth_start",
        y: (d) => d.port_name ?? "At sea",
        r: 3.5,
        fill: (d) => ({high: "#25344F", medium: "#617891", low: "#D5B893"})[confidenceBand(d.confidence)],
        stroke: "#fffdf9",
        strokeWidth: 1
      })
    ]
  });
}

// The mix of ship types among some port calls, as one stacked bar.
function mixBar(rows) {
  const counts = GROUPS.map((g) => ({group: g.key, n: rows.filter((d) => d.group === g.key).length})).filter((d) => d.n);
  return Plot.plot({
    height: 64,
    width: 300,
    marginLeft: 0,
    marginRight: 0,
    style: {fontSize: "10px", color: "#4a5972"},
    x: {axis: null},
    color: {domain: GROUPS.map((g) => g.key), range: GROUPS.map((g) => g.color), legend: false},
    marks: [
      Plot.barX(counts, Plot.stackX({x: "n", fill: "group", inset: 1, rx: 3, tip: true, title: (d) => `${d.group}: ${d.n}`})),
      Plot.text(counts, Plot.stackX({x: "n", text: (d) => (d.n / d3.sum(counts, (c) => c.n) > 0.12 ? d.group.split(" ")[0] : ""), fill: "#fffdf9", fontSize: 10}))
    ]
  });
}
```

## This week at a glance

<p class="sub" style="color: var(--muted); margin-top: -0.5rem;">Port calls in the 7 days up to the latest data, for the ship types picked above.</p>

<div class="grid grid-cols-2">
  <div class="card">
    <h2>Busiest ports</h2>
    <p class="sub">Port calls, split by ship type</p>

```js
{
  const top = d3.rollups(weekShown.filter((d) => d.port_name), (v) => v.length, (d) => d.port_name).sort((a, b) => b[1] - a[1]).slice(0, 12).map(([name]) => name);
  display(resize((width) =>
    Plot.plot({
      height: 360,
      marginLeft: 120,
      width,
      style: {color: "#4a5972"},
      x: {label: "Port calls", grid: true},
      y: {label: null, domain: top},
      color: {domain: GROUPS.map((g) => g.key), range: GROUPS.map((g) => g.color), legend: true},
      marks: [
        Plot.barX(
          weekShown.filter((d) => top.includes(d.port_name)),
          Plot.groupY({x: "count"}, {y: "port_name", fill: "group", order: GROUPS.map((g) => g.key), inset: 0.5, rx: 3, tip: true})
        ),
        Plot.ruleX([0], {stroke: "#d5b893"})
      ]
    })
  ));
}
```

  </div>
  <div class="card">
    <h2>How long ships stay</h2>
    <p class="sub">Visits where both the arrival and the departure were seen, by hours alongside (stays over 48 hours in the last bar)</p>

```js
{
  // Only visits seen from arrival to departure: when a ship drops out of
  // sight, its stay ends where the sightings stopped, not when it left.
  const finished = weekShown.filter((d) => d.completeness === "complete").map((d) => ({...d, hours: Math.min(d.minutes_alongside / 60, 47.9)}));
  display(resize((width) =>
    Plot.plot({
      height: 360,
      width,
      style: {color: "#4a5972"},
      x: {label: "Hours in port", domain: [0, 48], ticks: [0, 6, 12, 24, 36, 48]},
      y: {label: "Port calls", grid: true},
      marks: [
        Plot.rectY(finished, Plot.binX({y: "count"}, {x: "hours", interval: 2, fill: "#25344F", inset: 1, rx: 3, tip: true})),
        Plot.ruleY([0], {stroke: "#d5b893"})
      ]
    })
  ));
}
```

  </div>
</div>

<div class="grid grid-cols-2">
  <div class="card">
    <h2>How close to a known port?</h2>
    <p class="sub">Every stop is matched to the nearest official (UN/LOCODE) port. Stops far from any listed port are often quays the list leaves out, or rigs and fishing grounds.</p>

```js
{
  const stops = allCalls.filter((d) => d.berth_start >= weekAgo && activeGroups.has(d.group));
  const bands = [
    {band: "At the port (under 2 km)", color: "#25344F", test: (d) => d.nearest_port_km < 2},
    {band: "Near a port (2 to 10 km)", color: "#617891", test: (d) => d.nearest_port_km >= 2 && d.nearest_port_km <= 10},
    {band: "At sea (over 10 km)", color: "#D5B893", test: (d) => d.nearest_port_km > 10}
  ].map((b) => ({...b, n: stops.filter(b.test).length}));
  const total = d3.sum(bands, (b) => b.n) || 1;
  display(html`<div style="display:flex;flex-direction:column;gap:.6rem;margin-top:.4rem">${bands.map(
    (b) => html`<div>
      <div style="display:flex;justify-content:space-between;font-size:.85rem"><span>${b.band}</span><b>${d3.format(".0%")(b.n / total)}</b></div>
      <div style="height:10px;border-radius:5px;background:#f1e8d8;overflow:hidden"><div style="height:100%;width:${(100 * b.n) / total}%;background:${b.color};border-radius:5px"></div></div>
    </div>`
  )}</div>`);
}
```

  </div>
  <div class="card">
    <h2>How sure are we?</h2>
    <p class="sub">Each stop is scored by how many signals agree it was real. Speed and position come from GPS. The ship's status ("moored", "under way") is typed in by the crew and is often out of date, so a low score usually means the status wasn't updated, not that the stop is wrong.</p>

```js
{
  const stops = weekShown;
  const bands = [
    {band: "High", note: "speed, position and status agree", color: "#25344F", n: stops.filter((d) => d.confidence >= 0.8).length},
    {band: "Medium", note: "most signals agree", color: "#617891", n: stops.filter((d) => d.confidence >= 0.5 && d.confidence < 0.8).length},
    {band: "Low", note: "signals disagree, often a stale status", color: "#D5B893", n: stops.filter((d) => d.confidence < 0.5).length}
  ];
  const total = d3.sum(bands, (b) => b.n) || 1;
  display(html`<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:.75rem;margin-top:.4rem">${bands.map(
    (b) => html`<div style="padding:.8rem;border-radius:12px;background:#f5ede0;border-top:4px solid ${b.color}">
      <div style="font-family:var(--serif);font-size:1.8rem;color:var(--cadet)">${d3.format(".0%")(b.n / total)}</div>
      <div style="font-weight:600">${b.band}</div>
      <div style="font-size:.78rem;color:var(--ink-2)">${b.note}</div>
    </div>`
  )}</div>`);
}
```

  </div>
</div>

<div class="card">
  <h2>How reliable is HarbourOS?</h2>
  <p class="sub">An independent check, run every time the data refreshes. 100 random port calls from the latest run are compared with OpenStreetMap's map of quays, piers, harbours and ferry terminals. A call counts as confirmed when the ship sat still within 300 m of one of them.</p>

```js
{
  const r = reliability.latest;
  if (!r) {
    display(html`<p style="margin:.4rem 0 0">The first check runs with the next data refresh.</p>`);
  } else {
    const parts = [
      {key: "confirmed", label: "Confirmed", note: "sat still beside a mapped quay", color: "#25344F"},
      {key: "not_at_harbour", label: "No mapped quay nearby", note: "may be a quay OpenStreetMap leaves out", color: "#617891"},
      {key: "moved", label: "Moved during the stop", note: "positions drifted over 300 m", color: "#D5B893"},
      {key: "too_little_data", label: "Too few positions", note: "under two sightings in the stop", color: "#E8DCC6"}
    ];
    const total = r.checked || 1;
    const past = reliability.history;
    const pastRate = d3.sum(past, (d) => d.confirmed) / (d3.sum(past, (d) => d.checked) || 1);
    const misses = reliability.unconfirmed;
    const reason = Object.fromEntries(parts.map((p) => [p.key, p.label]));
    display(html`<div style="display:flex;flex-wrap:wrap;gap:1.25rem;align-items:flex-end;margin-top:.4rem">
      <div>
        <div style="font-family:var(--serif);font-size:2.6rem;line-height:1;color:var(--cadet)">${r.confirmed} of ${r.checked}</div>
        <div style="font-weight:600;margin-top:.25rem">random port calls confirmed (${d3.format(".0%")(r.confirmed / total)})</div>
      </div>
      <div style="font-size:.85rem;color:var(--ink-2);max-width:28rem">
        Checked ${when(new Date(r.checked_at))} Norway time, from ${r.candidates.toLocaleString("en-GB")} port calls that began in the 6 hours before.
        ${past.length > 1 ? html`Over the last ${past.length} checks, ${d3.format(".0%")(pastRate)} were confirmed.` : ""}
        OpenStreetMap leaves out some small quays, so this is a cautious figure.
      </div>
    </div>
    <div style="display:flex;height:12px;border-radius:6px;overflow:hidden;margin:.9rem 0 .6rem;gap:2px;background:#fffdf9">${parts.filter((p) => r[p.key]).map(
      (p) => html`<div title="${p.label}: ${r[p.key]}" style="flex:${r[p.key]};background:${p.color}"></div>`
    )}</div>
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(10rem,1fr));gap:.6rem">${parts.map(
      (p) => html`<div style="display:flex;gap:.5rem;align-items:flex-start">
        <span style="flex:none;width:10px;height:10px;border-radius:3px;margin-top:.3rem;background:${p.color};outline:1px solid #d5b893"></span>
        <div><b>${r[p.key]}</b> ${p.label}<div style="font-size:.78rem;color:var(--ink-2)">${p.note}</div></div>
      </div>`
    )}</div>
    ${misses.length ? html`<details style="margin-top:.8rem"><summary style="cursor:pointer;color:var(--cadet)">The ${misses.length} port calls that weren't confirmed</summary>
      <ul style="margin:.5rem 0 0;padding-left:1.1rem;font-size:.85rem;columns:2 18rem">${misses.map(
        (m) => html`<li>${m.ship} at ${m.port}: ${reason[m.verdict].toLowerCase()} · <a href="https://www.openstreetmap.org/?mlat=${m.lat}&mlon=${m.lon}#map=16/${m.lat}/${m.lon}" target="_blank" rel="noopener">map</a></li>`
      )}</ul></details>` : ""}`);
  }
}
```

</div>

<details class="card" style="padding: 1rem 1.25rem;">
<summary style="cursor: pointer; font-family: var(--serif); font-size: 1.05rem; color: var(--cadet);">All port calls this week, as a table</summary>

```js
Inputs.table(
  d3.sort(weekShown, (d) => -d.berth_start).map((d) => ({
    Ship: shipName(d),
    Type: d.group,
    Port: d.port_name,
    Arrived: d.berth_start,
    Stay: d.in_port_now ? "still there" : stay(d.minutes_alongside),
    Confidence: confidenceBand(d.confidence),
    "Port distance (km)": d.nearest_port_km
  })),
  {rows: 16, format: {Arrived: when}}
)
```

</details>

### How this was built

Every 10 minutes, Python collects the positions all ships along the coast are broadcasting
(BarentsWatch AIS). SQL cleans them into a trusted layer, setting aside every rejected row with
a reason. A Python state machine turns each ship's positions into stops, each with a confidence
score, and dbt matches every stop to the nearest official port and builds the tables this page
reads, including each ship's route. GitHub Actions runs the whole chain about four times a day and
republishes this page. It is a static snapshot, so no database is exposed to the internet.
