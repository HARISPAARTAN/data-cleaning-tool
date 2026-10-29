// Playwright check of the Pages site (web/index.html and web/demo/report.html), dark and light, 1440 and 390 px.
// Usage: node tools/checks/pages.mjs
// Env: PLAYWRIGHT_FROM (a package.json whose node_modules holds playwright), OUT (screenshot folder),
//      THUMB_QUALITY (JPEG quality of thumb-1440.jpg, default 80),
//      BASE (check a deployed copy instead of serving web/), PORT.
// Fails on a request to another host, an HTTP error, a console or page error, horizontal page scroll,
// a broken in-page link or fragment, text below WCAG AA contrast, or more than 3 MB loaded per page.
// Writes <page>-<width>-<theme>.png per view and thumb-1440.jpg (the report, 1440 x 900, dark) for the hub card.
import { createRequire } from 'node:module';
import { createServer } from 'node:http';
import { mkdir, readFile } from 'node:fs/promises';
import { extname, join, normalize, sep } from 'node:path';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';

const WEB = normalize(fileURLToPath(new URL('../../web/', import.meta.url)));
const PREFIX = '/data-cleaning-tool/';
const OUT = process.env.OUT || join(tmpdir(), 'data-cleaning-tool-shots');
const LIMIT = 3_000_000;
const TYPES = { '.html': 'text/html; charset=utf-8', '.css': 'text/css; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.json': 'application/json; charset=utf-8', '.csv': 'text/csv; charset=utf-8', '.yaml': 'text/plain; charset=utf-8', '.woff2': 'font/woff2', '.md': 'text/plain; charset=utf-8' };
const PAGES = [['index.html', 'usage'], ['demo/report.html', 'report']];
const { chromium } = createRequire(process.env.PLAYWRIGHT_FROM || import.meta.url)('playwright');

function serve(port) {
  const server = createServer(async (req, res) => {
    const path = decodeURIComponent(new URL(req.url, 'http://localhost').pathname);
    if (!path.startsWith(PREFIX)) return res.writeHead(404).end();
    let file = normalize(join(WEB, path.slice(PREFIX.length)));
    if (!file.startsWith(WEB.endsWith(sep) ? WEB : WEB + sep)) return res.writeHead(403).end();
    if (path.endsWith('/')) file = join(file, 'index.html');
    try {
      const body = await readFile(file);
      return res.writeHead(200, { 'content-type': TYPES[extname(file)] ?? 'application/octet-stream' }).end(body);
    } catch {
      return res.writeHead(404).end();
    }
  });
  return new Promise((resolve) => server.listen(port, '127.0.0.1', () => resolve(server)));
}

const results = [];
function check(name, pass, detail = '') {
  results.push(Boolean(pass));
  console.log(`${pass ? 'ok  ' : 'FAIL'} ${name}${detail ? ` (${detail})` : ''}`);
}

const server = process.env.BASE ? null : await serve(Number(process.env.PORT || 8098));
const BASE = process.env.BASE || `http://127.0.0.1:${server.address().port}${PREFIX}`;
const ORIGIN = new URL(BASE).origin;
await mkdir(OUT, { recursive: true });
const browser = await chromium.launch({ channel: 'chrome', headless: true });

function watch(page) {
  const problems = [];
  const sizes = [];
  page.on('request', (r) => {
    const u = r.url();
    if (!u.startsWith('data:') && !u.startsWith('blob:') && new URL(u).origin !== ORIGIN) problems.push(`external request ${u}`);
  });
  page.on('response', async (r) => {
    if (r.status() >= 400) problems.push(`HTTP ${r.status()} ${r.url()}`);
    try { sizes.push((await r.body()).length); } catch { /* redirects have no body */ }
  });
  page.on('console', (m) => { if (m.type() === 'error') problems.push(`console error: ${m.text()}`); });
  page.on('pageerror', (e) => problems.push(`page error: ${e.message}`));
  return { problems, sizes };
}

// Text contrast: every element with its own visible text against the first opaque background behind it.
const contrast = (page) => page.evaluate(() => {
  const canvas = document.createElement('canvas').getContext('2d', { willReadFrequently: true });
  const rgba = (css) => {
    canvas.clearRect(0, 0, 1, 1);
    canvas.fillStyle = '#000';
    canvas.fillStyle = css;
    canvas.fillRect(0, 0, 1, 1);
    const [r, g, b, a] = canvas.getImageData(0, 0, 1, 1).data;
    return [r, g, b, a / 255];
  };
  const lum = ([r, g, b]) => {
    const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
  };
  const background = (el) => {
    for (let node = el; node; node = node.parentElement) {
      const bg = getComputedStyle(node).backgroundColor;
      const c = rgba(bg);
      if (bg !== 'transparent' && c[3] > 0.99) return c;
    }
    return rgba(getComputedStyle(document.body).backgroundColor);
  };
  const failures = [];
  let checked = 0;
  for (const el of document.querySelectorAll('body *')) {
    const own = [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim());
    if (!own || el.closest('.pf-visually-hidden, [hidden], svg')) continue;
    const box = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    if (!box.width || !box.height || style.visibility === 'hidden') continue;
    const fg = rgba(style.color);
    const bg = background(el);
    const mixed = fg.map((v, i) => (i < 3 ? v * fg[3] + bg[i] * (1 - fg[3]) : 1));
    const [a, b] = [lum(mixed), lum(bg)].sort((x, y) => y - x);
    const ratio = (a + 0.05) / (b + 0.05);
    const size = parseFloat(style.fontSize);
    const large = size >= 24 || (size >= 18.66 && Number(style.fontWeight) >= 700);
    checked += 1;
    if (ratio < (large ? 3 : 4.5)) failures.push(`${el.tagName.toLowerCase()}.${[...el.classList].join('.')} "${el.textContent.trim().slice(0, 30)}" ${ratio.toFixed(2)}`);
  }
  return { checked, failures };
});

async function links(page, url) {
  const found = await page.evaluate(() => [...document.querySelectorAll('a[href]')].map((a) => a.getAttribute('href')));
  const problems = [];
  for (const href of new Set(found)) {
    if (/^https?:/.test(href)) continue;
    const target = new URL(href, url);
    if (target.hash && target.pathname === new URL(url).pathname) {
      const id = decodeURIComponent(target.hash.slice(1));
      if (!(await page.evaluate((i) => Boolean(document.getElementById(i)), id))) problems.push(`no element #${id}`);
      continue;
    }
    const response = await fetch(target);
    if (!response.ok) problems.push(`${href}: HTTP ${response.status}`);
  }
  return { count: found.length, problems };
}

for (const [path, name] of PAGES) {
  for (const theme of ['dark', 'light']) {
    for (const width of [1440, 390]) {
      const tag = `${name} ${width} ${theme}`;
      const context = await browser.newContext({ viewport: { width, height: 900 }, colorScheme: theme, reducedMotion: 'reduce' });
      const page = await context.newPage();
      const { problems, sizes } = watch(page);
      const url = new URL(path, BASE).href;
      await page.goto(url, { waitUntil: 'networkidle' });
      await page.evaluate(() => document.fonts.ready);
      check(`${tag}: theme attribute or scheme`, await page.evaluate((t) => (document.documentElement.dataset.theme ?? t) === t, theme));
      const scroll = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      check(`${tag}: no horizontal page scroll`, scroll <= 0, `${scroll} px`);
      if (name === 'usage') {
        check(`${tag}: demo banner first in body`, await page.evaluate(() => document.body.firstElementChild?.classList.contains('pf-banner')));
        check(`${tag}: theme toggle rendered`, await page.locator('.pf-theme-toggle').count() === 1);
      }
      const audit = await contrast(page);
      check(`${tag}: text contrast AA (${audit.checked} elements)`, audit.failures.length === 0, audit.failures.slice(0, 5).join('; '));
      const linked = await links(page, url);
      check(`${tag}: ${linked.count} links resolve`, linked.problems.length === 0, linked.problems.join('; '));
      const bytes = sizes.reduce((a, b) => a + b, 0);
      check(`${tag}: page weight ${bytes.toLocaleString('en-GB')} bytes`, bytes <= LIMIT);
      await page.screenshot({ path: join(OUT, `${name}-${width}-${theme}.png`), fullPage: width === 390 });
      if (name === 'report' && width === 1440 && theme === 'dark') await page.screenshot({ path: join(OUT, 'thumb-1440.jpg'), type: 'jpeg', quality: Number(process.env.THUMB_QUALITY || 80) });
      check(`${tag}: no external request, HTTP error or console error`, problems.length === 0, problems.join('; '));
      await context.close();
    }
  }
}

const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: 'dark' });
const page = await context.newPage();
await page.goto(new URL('index.html', BASE).href, { waitUntil: 'networkidle' });
await page.locator('.pf-theme-toggle').click();
check('usage: the toggle switches to light', await page.evaluate(() => document.documentElement.dataset.theme === 'light'));
const keyboard = [];
for (let i = 0; i < 40; i += 1) {
  await page.keyboard.press('Tab');
  keyboard.push(await page.evaluate(() => document.activeElement?.className || document.activeElement?.tagName));
}
check('usage: Tab reaches the theme toggle and the scrollable tables', keyboard.some((c) => String(c).includes('pf-theme-toggle')) && keyboard.some((c) => String(c).includes('pf-table-wrap')));
await context.close();
await browser.close();
server?.close();
const failed = results.filter((r) => !r).length;
console.log(failed ? `${failed} of ${results.length} checks failed` : `all ${results.length} checks passed; screenshots in ${OUT}`);
process.exit(failed ? 1 : 0);
