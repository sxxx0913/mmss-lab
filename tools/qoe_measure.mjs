#!/usr/bin/env node
/**
 * Lab 2 — automated QoE measurement.
 *
 * Drives stream/player.html in Google Chrome through the DevTools protocol with
 * `Network.emulateNetworkConditions`, i.e. exactly the throttling presets that
 * the instructions create by hand in the Network panel, and reads the QoE row
 * that the page computes from the media-element events.
 *
 *   node tools/qoe_measure.mjs                        # P1,P2,P3 x 2 runs
 *   node tools/qoe_measure.mjs --profiles P3:700:400 --repeats 3 --startlevel -1
 *
 * Requirements: Chrome installed, `npm i puppeteer-core` in tools/, and a
 * static server on the stream/ directory (`python -m http.server 8000`).
 */
import fs from 'node:fs';
import path from 'node:path';
import puppeteer from 'puppeteer-core';

const args = process.argv.slice(2);
const arg = (name, def) => {
  const i = args.indexOf(`--${name}`);
  return i >= 0 && args[i + 1] ? args[i + 1] : def;
};

const BASE = arg('url', 'http://127.0.0.1:8000/player.html');
const REPEATS = Number(arg('repeats', '2'));
const OUT = arg('out', 'results/qoe.csv');
const APPEND = args.includes('--append');
const VERBOSE = args.includes('--verbose');   // print the level timeline of every run
const STARTLEVEL = arg('startlevel', null);
const SRC = arg('src', null);          // e.g. hls4/master.m3u8
// optional: drop the bandwidth mid-playback (FR-03 of Lab 1)
const DROP_AFTER = arg('drop-after', null) === null ? null : Number(arg('drop-after'));
const DROP_TO = arg('drop-to', null);
const CHROME = arg('chrome', 'C:/Program Files/Google/Chrome/Application/chrome.exe');

// profile name : download kbit/s : latency ms   (Table 2 of the instructions)
const PROFILES = arg('profiles', 'P1:8000:40,P2:2000:120,P3:700:400')
  .split(',')
  .map(s => {
    const [name, kbps, latency] = s.split(':');
    return { name, kbps: Number(kbps), latency: Number(latency) };
  });

const HEADER = 'profile,startup_s,switches,stalls,stall_s,avg_kbps';
const rows = [];

const browser = await puppeteer.launch({
  executablePath: CHROME,
  headless: 'new',
  args: [
    '--autoplay-policy=no-user-gesture-required',
    '--mute-audio',
    '--disable-background-timer-throttling',
    '--disable-backgrounding-occluded-windows',
    '--disable-renderer-backgrounding',
    '--window-size=1280,800',
  ],
});

for (const p of PROFILES) {
  for (let run = 1; run <= REPEATS; run++) {
    const page = await browser.newPage();
    await page.setViewport({ width: 1280, height: 800 });
    const cdp = await page.createCDPSession();
    await cdp.send('Network.enable');
    await cdp.send('Network.setCacheDisabled', { cacheDisabled: true });
    await cdp.send('Network.emulateNetworkConditions', {
      offline: false,
      latency: p.latency,
      downloadThroughput: (p.kbps * 1000) / 8,   // bytes per second
      uploadThroughput: (p.kbps * 1000) / 8,
    });

    const q = new URLSearchParams({ auto: '1', profile: p.name });
    if (STARTLEVEL !== null) q.set('startlevel', STARTLEVEL);
    if (SRC !== null) q.set('src', SRC);

    process.stdout.write(`${p.name} run ${run} (${p.kbps} kbit/s, ${p.latency} ms) ... `);
    await page.goto(`${BASE}?${q}`, { waitUntil: 'domcontentloaded' });

    // optional mid-playback bandwidth drop: FR-03 of Lab 1
    if (DROP_AFTER !== null && DROP_TO !== null) {
      // --drop-to accepts either a profile name or an explicit "kbps:latency"
      const named = PROFILES.find(x => x.name === DROP_TO) ||
                    { P1: { kbps: 8000, latency: 40 }, P2: { kbps: 2000, latency: 120 },
                      P3: { kbps: 700, latency: 400 } }[DROP_TO];
      const to = named || (DROP_TO.includes(':')
        ? { kbps: Number(DROP_TO.split(':')[0]), latency: Number(DROP_TO.split(':')[1]) }
        : { kbps: Number(DROP_TO), latency: 100 });
      await new Promise(r => setTimeout(r, DROP_AFTER * 1000));
      await cdp.send('Network.emulateNetworkConditions', {
        offline: false,
        latency: to.latency,
        downloadThroughput: (to.kbps * 1000) / 8,
        uploadThroughput: (to.kbps * 1000) / 8,
      });
      console.log(`[t=${DROP_AFTER}s bandwidth -> ${to.kbps} kbit/s] `);
    }

    let row = null;
    try {
      await page.waitForFunction('window.__qoeDone === true', { timeout: 300000, polling: 500 });
      row = await page.evaluate(() => window.__qoe);
    } catch {
      row = await page.evaluate(() => window.__qoe || null);
    }
    if (VERBOSE) {
      const tl = await page.evaluate(() =>
        Array.from(document.querySelectorAll('#log li')).map(li => li.textContent.trim()));
      for (const line of tl) console.log('    ' + line);
    }
    if (row) {
      rows.push(row);
      console.log(`startup ${row.startup_s}s  switches ${row.switches}  stalls ${row.stalls}  ` +
                  `stall ${row.stall_s}s  avg ${row.avg_kbps} kbit/s`);
    } else {
      console.log('TIMEOUT / no result');
    }
    await page.close();
  }
}

await browser.close();

const target = path.resolve(OUT);
fs.mkdirSync(path.dirname(target), { recursive: true });
const body = rows.map(r => `${r.profile},${r.startup_s},${r.switches},${r.stalls},${r.stall_s},${r.avg_kbps}`);
if (APPEND && fs.existsSync(target)) {
  fs.appendFileSync(target, body.join('\n') + '\n');
} else {
  fs.writeFileSync(target, [HEADER, ...body].join('\n') + '\n');
}
console.log(`\nwrote ${rows.length} rows to ${target}`);
