import { readdir } from "node:fs/promises";
import path from "node:path";

const MD_EXT = new Set([".md", ".markdown", ".mdx"]);
const LANG_DIR = /^[a-z]{2,3}(?:-[a-z0-9]{2,8})?$/i;

export async function listLangCodes(contentRoot, sourceLang) {
  const entries = await readdir(contentRoot, { withFileTypes: true });
  return entries
    .filter((e) => e.isDirectory() && e.name !== sourceLang && LANG_DIR.test(e.name))
    .map((e) => e.name)
    .sort();
}

export async function listMarkdownFiles(dir) {
  const out = [];
  await walk(dir, dir, out);
  return out.sort();
}

async function walk(root, current, out) {
  let entries;
  try {
    entries = await readdir(current, { withFileTypes: true });
  } catch (err) {
    if (err.code === "ENOENT") return;
    throw err;
  }

  for (const entry of entries) {
    const full = path.join(current, entry.name);
    if (entry.isDirectory()) {
      await walk(root, full, out);
      continue;
    }
    if (!entry.isFile()) continue;
    if (!MD_EXT.has(path.extname(entry.name).toLowerCase())) continue;
    out.push(path.relative(root, full).split(path.sep).join("/"));
  }
}

export function languageName(code) {
  const names = new Intl.DisplayNames(["en"], { type: "language" });
  try {
    return names.of(code) ?? code;
  } catch {
    return code;
  }
}
