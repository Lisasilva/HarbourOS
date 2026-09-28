// Screenshot a built dashboard (dashboard/dist) in a real browser, so a
// preview can be checked without opening its address. Used by the Dashboard
// preview workflow; run locally with: node scripts/screenshots.mjs dist shots
//
// It serves the folder on localhost, takes a few pictures (the page, a moving
// ship's route, a port, the replay, a phone-sized screen) and writes
// report.txt with any errors the page logged.

import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import {chromium} from "playwright";

const [dist = "dist", out = "shots"] = process.argv.slice(2);
fs.mkdirSync(out, {recursive: true});

const types = {
  ".html": "text/html",
  ".js": "text/javascript",
  ".css": "text/css",
  ".csv": "text/csv",
  ".json": "application/json",
  ".wasm": "application/wasm",
  ".png": "image/png"
};
const server = http
  .createServer((req, res) => {
    let file = path.join(dist, decodeURIComponent(new URL(req.url, "http://localhost").pathname));
    if (file.endsWith(path.sep)) file = path.join(file, "index.html");
    if (!fs.existsSync(file) && fs.existsSync(`${file}.html`)) file = `${file}.html`;
    if (!fs.existsSync(file)) {
      res.writeHead(404);
      return res.end();
    }
    res.writeHead(200, {"content-type": types[path.extname(file)] ?? "application/octet-stream"});
    fs.createReadStream(file).pipe(res);
  })
  .listen(8765);

const report = [];
const browser = await chromium.launch({channel: process.env.CHROME_CHANNEL || undefined, args: ["--enable-unsafe-swiftshader"]});

async function open(viewport) {
  const page = await browser.newPage({viewport, deviceScaleFactor: 1});
  page.on("pageerror", (e) => report.push(`page error: ${e.message}`));
  page.on("console", (m) => m.type() === "error" && report.push(`console error: ${m.text()}`));
  await page.goto("http://localhost:8765/", {waitUntil: "networkidle"});
  await page.waitForFunction(() => window.__harbourMap?.loaded(), null, {timeout: 60_000});
  await page.waitForTimeout(4000);
  return page;
}

// Click the screen position of the first feature in a map layer.
async function clickFeature(page, layer) {
  const point = await page.evaluate((layer) => {
    const map = window.__harbourMap;
    const [f] = map.queryRenderedFeatures({layers: [layer]});
    if (!f) return null;
    const p = map.project(f.geometry.coordinates);
    const box = map.getCanvas().getBoundingClientRect();
    return {x: box.left + p.x, y: box.top + p.y, name: f.properties.name};
  }, layer);
  if (!point) return null;
  await page.mouse.click(point.x, point.y);
  await page.waitForTimeout(3000);
  return point.name;
}

const page = await open({width: 1440, height: 1000});
await page.screenshot({path: `${out}/1-page.png`});
await page.screenshot({path: `${out}/2-full-page.png`, fullPage: true});
report.push(`kpis: ${(await page.locator(".kpi .value").allInnerTexts()).join(" | ")}`);
report.push(`ships drawn: ${await page.evaluate(() => window.__harbourMap.querySourceFeatures("ships").length)}`);

const map = page.locator(".map-card");
report.push(`clicked moving ship: ${await clickFeature(page, "ships-moving")}`);
await map.screenshot({path: `${out}/3-ship.png`});
report.push(`ship panel: ${(await page.locator(".map-panel").innerText()).replace(/\s+/g, " ").slice(0, 300)}`);

report.push(`clicked port: ${await clickFeature(page, "ports")}`);
await map.screenshot({path: `${out}/4-port.png`});

await page.locator(".map-panel .close").click().catch(() => {});
await page.evaluate(() => window.__harbourMap.jumpTo({center: [5.3, 60.4], zoom: 7}));
await page.waitForTimeout(3000);
await map.screenshot({path: `${out}/5-bergen-zoom.png`});

const range = page.locator(".replay input[type=range]");
await range.evaluate((el) => {
  el.value = +el.min + (+el.max - +el.min) / 2;
  el.dispatchEvent(new Event("input", {bubbles: true}));
});
await page.waitForTimeout(2000);
await map.screenshot({path: `${out}/6-replay.png`});
await page.close();

const phone = await open({width: 390, height: 844});
await phone.screenshot({path: `${out}/7-phone.png`, fullPage: true});
await phone.close();

fs.writeFileSync(`${out}/report.txt`, report.join("\n") + "\n");
console.log(report.join("\n"));
await browser.close();
server.close();
