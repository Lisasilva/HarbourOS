---
title: Port calls
toc: false
---

# HarbourOS
## Confidence-scored port calls from Norwegian AIS

```js
const calls = FileAttachment("data/port_calls.csv").csv({typed: true});
```

```js
const portNames = d3.sort(new Set(calls.filter((d) => d.port_name).map((d) => d.port_name)));

const portPick = view(Inputs.select(["All ports", ...portNames], {label: "Port", value: "All ports"}));
const stopPick = view(Inputs.select(["All", "berthed", "anchored"], {label: "Stop type", value: "All"}));
const visitPick = view(Inputs.select(["Port calls", "All stops", "At sea only"], {label: "Visit", value: "Port calls"}));
const minConfidence = view(Inputs.range([0, 1], {label: "Min confidence", step: 0.1, value: 0}));
const completeOnly = view(Inputs.toggle({label: "Fully observed visits only", value: false}));
```

```js
const filtered = calls.filter(
  (d) =>
    (portPick === "All ports" || d.port_name === portPick) &&
    (stopPick === "All" || d.stop_type === stopPick) &&
    (visitPick === "All stops"
      || (visitPick === "Port calls" ? d.visit_type === "port_call" : d.visit_type === "at_sea")) &&
    d.confidence >= minConfidence &&
    (!completeOnly || d.completeness === "complete")
);

// A ship still in port has no real stay length yet, so it's excluded from
// the median and the stay-length chart rather than counted as a short (or
// suspiciously long) visit.
const settled = filtered.filter((d) => !d.in_port_now);
const inPortNow = filtered.filter((d) => d.in_port_now).length;

const medianStay = d3.median(settled, (d) => d.minutes_alongside);
const vessels = new Set(filtered.map((d) => d.mmsi)).size;
const atSea = calls.filter((d) => d.visit_type === "at_sea").length;

const tableRows = filtered.map((d) => ({
  ...d,
  status: d.in_port_now ? "In port now" : d.completeness
}));
```

<div class="grid grid-cols-4">
  <div class="card">
    <h2>${visitPick === "At sea only" ? "Stops at sea" : "Port calls"}</h2>
    <span class="big">${filtered.length}</span>
  </div>
  <div class="card">
    <h2>In port now</h2>
    <span class="big">${inPortNow}</span>
  </div>
  <div class="card">
    <h2>Median stay</h2>
    <span class="big">${medianStay ? d3.format(",.0f")(medianStay) + " min" : "—"}</span>
    <div style="font-size: 0.75rem; opacity: 0.7; margin-top: 4px;">of ${settled.length} finished visits</div>
  </div>
  <div class="card">
    <h2>Stops at sea (all time)</h2>
    <span class="big">${d3.format(",")(atSea)}</span>
  </div>
</div>

A *port call* is one vessel stopping once, derived from raw AIS position reports by a
state machine. Confidence reflects whether the ship's own reported status agrees with
its measured speed — a vessel broadcasting "moored" while making 7 knots scores low.
A stop with no known seaport within 10 km is a rig, an anchorage or a fishing ground,
not a port call — it's recorded as "at sea" rather than guessed at, and the "Port calls"
view leaves it out by default. Switch "Visit" above to see it. A visit still marked
"in port now" hasn't been seen leaving yet, so its stay length isn't final and it's left
out of the stay-length numbers below until it is.

<div class="grid grid-cols-2">
  <div class="card">

```js
Plot.plot({
  title: "Busiest ports",
  marginLeft: 130,
  height: 340,
  x: {label: "Port calls", grid: true},
  y: {label: null},
  marks: [
    Plot.barX(
      filtered.filter((d) => d.port_name),
      Plot.groupY({x: "count"}, {y: "port_name", sort: {y: "x", reverse: true, limit: 12}, fill: "#4269d0", tip: true})
    ),
    Plot.ruleX([0])
  ]
})
```

  </div>
  <div class="card">

```js
Plot.plot({
  title: "How long ships stayed",
  subtitle: "Excludes visits still in port",
  height: 340,
  x: {label: "Minutes alongside", tickFormat: (d) => d3.format(",d")(d)},
  y: {label: "Visits", grid: true},
  marks: [
    Plot.rectY(settled, Plot.binX({y: "count"}, {x: "minutes_alongside", thresholds: 28, fill: "#4269d0", tip: true})),
    Plot.ruleY([0])
  ]
})
```

  </div>
</div>

<div class="card">

```js
Plot.plot({
  title: "Where ships stopped",
  subtitle: "Each dot is one visit, placed at the average of the AIS fixes recorded during the stop",
  height: 560,
  projection: {
    type: "mercator",
    domain: {type: "MultiPoint", coordinates: filtered.map((d) => [d.stop_longitude, d.stop_latitude])},
    inset: 20
  },
  color: {legend: true, domain: ["berthed", "anchored"], range: ["#4269d0", "#efb118"]},
  marks: [
    Plot.dot(filtered, {
      x: "stop_longitude",
      y: "stop_latitude",
      r: 5,
      fill: "stop_type",
      fillOpacity: 0.75,
      tip: true,
      channels: {vessel: "vessel_name", port: "port_name", minutes: "minutes_alongside", confidence: "confidence"}
    })
  ]
})
```

</div>

<div class="card">

```js
Inputs.table(tableRows, {
  columns: [
    "vessel_name",
    "port_name",
    "visit_type",
    "stop_type",
    "berth_start",
    "minutes_alongside",
    "confidence",
    "status",
    "nearest_port_km"
  ],
  header: {
    vessel_name: "Vessel",
    port_name: "Port",
    visit_type: "Visit",
    stop_type: "Stop",
    berth_start: "Arrived",
    minutes_alongside: "Minutes",
    confidence: "Confidence",
    status: "Observed",
    nearest_port_km: "Port dist (km)"
  },
  sort: "berth_start",
  reverse: true,
  rows: 18
})
```

</div>

### How this was built

Python polls the BarentsWatch AIS API into a Bronze table; SQL deduplicates and validates
into Silver, quarantining every rejected row with a reason rather than dropping it; a
Python state machine derives confidence-scored voyage states; those become port-call
events; dbt builds the star schema and matches each visit to the nearest UN/LOCODE
seaport. Dagster runs the whole chain on a schedule. This page is a static snapshot of
the Gold layer — no database is exposed to the internet.
