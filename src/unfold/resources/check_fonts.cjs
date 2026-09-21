// Use the pinned renderer's browser and native font decoder before starting capture.
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const {createRequire} = require('node:module');
const backendRequire = createRequire(path.join(process.argv[2], 'package.json'));
const puppeteer = backendRequire('puppeteer-core');
const {getInstalledBrowsers} = backendRequire('@puppeteer/browsers');
(async () => {
  let browser;
  try {
    let executablePath;
    for (const cacheDir of [path.join(os.homedir(), '.cache/hyperframes/chrome'), path.join(os.homedir(), '.cache/puppeteer')]) {
      const installed = await getInstalledBrowsers({cacheDir});
      executablePath = installed.find(b => b.browser === 'chrome-headless-shell')?.executablePath;
      if (executablePath) break;
    }
    if (!executablePath) throw new Error('Renderer browser missing; see unfold --help for backend setup.');
    browser = await puppeteer.launch({executablePath, headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage']});
    const page = await browser.newPage();
    for (const file of process.argv.slice(3)) {
      const bytes = fs.readFileSync(file).toString('base64');
      await page.evaluate(async bytes => {
        const buffer = Uint8Array.from(atob(bytes), c => c.charCodeAt(0)).buffer;
        const face = await new FontFace('unfold_check', buffer).load();
        if (face.status !== 'loaded') throw new Error('Required font could not load.');
      }, bytes);
    }
  } finally {
    if (browser) await browser.close();
  }
})().catch(error => { console.error(String(error)); process.exitCode = 1; });
