// Copy JS files from frontend/js/ to dist/js/
// Copy index.html from webui root to dist/ (keeps dist/ as deployable snapshot)
const fs   = require('fs');
const path = require('path');

const root = path.join(__dirname, '..');

// ── JS files ─────────────────────────────────────────────────────────────────
const jsSrc = path.join(root, 'frontend', 'js');
const jsDst = path.join(root, 'dist', 'js');

fs.mkdirSync(jsDst, { recursive: true });

const files = fs.readdirSync(jsSrc).filter(f => f.endsWith('.js'));
for (const f of files) {
  fs.copyFileSync(path.join(jsSrc, f), path.join(jsDst, f));
  console.log(`  copied: js/${f}`);
}

// ── index.html ────────────────────────────────────────────────────────────────
const htmlSrc = path.join(root, 'index.html');
const htmlDst = path.join(root, 'dist', 'index.html');
if (fs.existsSync(htmlSrc)) {
  fs.copyFileSync(htmlSrc, htmlDst);
  console.log('  copied: index.html');
}

console.log('Build OK');
