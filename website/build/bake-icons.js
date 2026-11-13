// Replaces <i data-lucide="name"> placeholders in the page markup with the finished SVG,
// so icons are there on first paint instead of popping in after the icon script loads.
// Icons inside <script> blocks (built at runtime) are left for lucide.createIcons().
const fs = require('fs'), path = require('path');
const { icons } = require('lucide');
const pascal = n => n.replace(/(^|-)([a-z0-9])/g, (_, __, c) => c.toUpperCase());
const esc = s => String(s).replace(/&/g, '&amp;').replace(/"/g, '&quot;');
function svg(name, rest) {
  const node = icons[pascal(name)];
  if (!node) throw new Error('Unknown icon: ' + name);
  const cls = (rest.match(/class="([^"]*)"/) || [, ''])[1];
  const other = rest.replace(/\s*class="[^"]*"/, '');
  const kids = node.map(([tag, attrs]) => `<${tag} ${Object.entries(attrs).map(([k, v]) => `${k}="${esc(v)}"`).join(' ')}></${tag}>`).join('');
  const hidden = /aria-hidden=/.test(other) ? '' : ' aria-hidden="true"';
  return `<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"${hidden} class="lucide lucide-${name}${cls ? ' ' + cls : ''}"${other}>${kids}</svg>`;
}
let total = 0;
for (const file of process.argv.slice(2)) {
  const html = fs.readFileSync(file, 'utf8');
  const parts = html.split(/(<script[\s\S]*?<\/script>)/);
  const out = parts.map(p => p.startsWith('<script') ? p : p.replace(/<i data-lucide="([a-z0-9-]+)"([^>]*)><\/i>/g, (_, n, rest) => { total++; return svg(n, rest); })).join('');
  fs.writeFileSync(file, out);
  console.log(path.basename(file), 'icons left for runtime:', (out.match(/data-lucide="/g) || []).length);
}
console.log('baked', total);
