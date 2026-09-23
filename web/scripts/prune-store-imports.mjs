// One-shot dev utility: prune each view's store.js import list down to the
// identifiers actually referenced in the file (template + script, word
// boundaries, over-approximate: anything appearing anywhere is kept).
// Safe direction — an unused-but-kept import is harmless; a dropped-but-used
// import is a runtime break. Run: node scripts/prune-store-imports.mjs
import { readFileSync, writeFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

const storeSrc = readFileSync("src/store.js", "utf8");
// Every binding store.js exports (export const/function/... and `export { ... }`)
const exportNames = new Set();
for (const match of storeSrc.matchAll(/^export (?:const|function|async function|let|var)\s+(\w+)/gm)) {
  exportNames.add(match[1]);
}
for (const match of storeSrc.matchAll(/^export\s*\{([^}]*)\}/gm)) {
  for (const piece of match[1].split(",")) {
    const name = piece.trim().split(/\s+as\s+/).pop().trim();
    if (name) exportNames.add(name);
  }
}
// api.js names re-exported through store (imported into store then re-exported
// via `export { ... }`) — covered above. Views also import a few names
// directly from ./api.js; prune those too.
const apiSrc = readFileSync("src/api.js", "utf8");
const apiNames = new Set();
for (const match of apiSrc.matchAll(/^export (?:const|function|async function|let|var)\s+(\w+)/gm)) {
  apiNames.add(match[1]);
}

const dir = "src/components";
let totalRemoved = 0;
let filesChanged = 0;
for (const file of readdirSync(dir)) {
  if (!file.endsWith(".vue")) continue;
  const path = join(dir, file);
  const src = readFileSync(path, "utf8");
  // Body without the import lines, for usage detection.
  const body = src.replace(/^import\s*\{[^}]*\}\s*from\s*["'][^"']+["'];?\s*$/gm, "");
  const used = (name) => new RegExp(`\\b${name}\\b`).test(body);

  let changed = false;
  // store.js import block
  const storeImport = src.match(/^import\s*\{([^}]*)\}\s*from\s*["']\.\.\/store\.js["'];?$/m);
  if (storeImport) {
    const names = storeImport[1].split(",").map((s) => s.trim()).filter(Boolean);
    const kept = names.filter((n) => used(n) && exportNames.has(n));
    const dropped = names.length - kept.length;
    if (dropped > 0) {
      const replacement = kept.length
        ? `import { ${kept.join(", ")} } from "../store.js";`
        : "";
      const updated = src.replace(storeImport[0], replacement);
      writeFileSync(path, updated);
      totalRemoved += dropped;
      changed = true;
      console.log(`${file}: store imports ${names.length} -> ${kept.length}`);
    }
  }
  // api.js direct import block
  const apiImport = src.match(/^import\s*\{([^}]*)\}\s*from\s*["']\.\.\/api\.js["'];?$/m);
  if (apiImport) {
    const names = apiImport[1].split(",").map((s) => s.trim()).filter(Boolean);
    const kept = names.filter((n) => used(n) && apiNames.has(n));
    const dropped = names.length - kept.length;
    if (dropped > 0) {
      const replacement = kept.length
        ? `import { ${kept.join(", ")} } from "../api.js";`
        : "";
      let updated = readFileSync(path, "utf8").replace(apiImport[0], replacement);
      writeFileSync(path, updated);
      totalRemoved += dropped;
      changed = true;
      console.log(`${file}: api imports ${names.length} -> ${kept.length}`);
    }
  }
  if (changed) filesChanged++;
}
console.log(`done: ${totalRemoved} imports removed across ${filesChanged} files`);
