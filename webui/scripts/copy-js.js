// copies js/ → dist/js/ (no bundling — native ES modules)
const fs   = require('fs');
const path = require('path');

const src  = path.join(__dirname, '..', 'js');
const dest = path.join(__dirname, '..', 'dist', 'js');

fs.mkdirSync(dest, { recursive: true });

fs.readdirSync(src).filter(f => f.endsWith('.js')).forEach(file => {
  fs.copyFileSync(path.join(src, file), path.join(dest, file));
  console.log(`  copy ${file}`);
});

console.log('JS files copied to dist/js/');
