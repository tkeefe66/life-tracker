#!/usr/bin/env node
// OKLCH -> sRGB hex (standard OKLab math, gamut-clipped).
// Usage: node oklch2hex.js <L 0-1> <C> <H degrees>   e.g. node oklch2hex.js 0.48 0.145 90
function oklchToHex(L, C, Hdeg) {
  const h = (Hdeg * Math.PI) / 180;
  const a = C * Math.cos(h), b = C * Math.sin(h);
  const l_ = L + 0.3963377774 * a + 0.2158037573 * b;
  const m_ = L - 0.1055613458 * a - 0.0638541728 * b;
  const s_ = L - 0.0894841775 * a - 1.2914855480 * b;
  const l = l_ ** 3, m = m_ ** 3, s = s_ ** 3;
  const r = 4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s;
  const g = -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s;
  const bb = -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s;
  const toSrgb = (c) => {
    c = Math.min(1, Math.max(0, c));
    return c <= 0.0031308 ? 12.92 * c : 1.055 * c ** (1 / 2.4) - 0.055;
  };
  const hex = (c) => Math.round(toSrgb(c) * 255).toString(16).padStart(2, "0");
  return `#${hex(r)}${hex(g)}${hex(bb)}`;
}

const [L, C, H] = process.argv.slice(2).map(Number);
if ([L, C, H].some(Number.isNaN)) {
  console.error("Usage: node oklch2hex.js <L 0-1> <C> <H degrees>");
  process.exit(1);
}
console.log(oklchToHex(L, C, H));
