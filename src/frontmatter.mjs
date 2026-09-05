import YAML from "yaml";

const FM_OPEN = /^(---|\+\+\+)\r?\n/;

const TRANSLATABLE_KEYS = new Set([
  "title",
  "description",
  "summary",
  "subtitle",
  "excerpt",
  "linktitle",
  "seotitle",
  "seo_title",
  "meta_description",
  "metadescription",
  "tweet",
  "tweets",
  "social",
]);

const DATE_KEYS = new Set(["date", "lastmod", "publishdate", "expirydate"]);

export function splitFrontMatter(raw) {
  const open = raw.match(FM_OPEN);
  if (!open) {
    return { delimiter: "---", fmText: "", data: {}, body: raw, hasFrontMatter: false };
  }

  const delimiter = open[1];
  const afterOpen = raw.slice(open[0].length);
  const closeIdx = afterOpen.search(new RegExp(`\\r?\\n${escapeRegExp(delimiter)}\\s*(?:\\r?\\n|$)`));
  if (closeIdx === -1) {
    throw new Error("Unclosed front matter block");
  }

  const fmText = afterOpen.slice(0, closeIdx);
  const rest = afterOpen.slice(closeIdx).replace(new RegExp(`^\\r?\\n${escapeRegExp(delimiter)}`), "");
  const body = rest.replace(/^\r?\n/, "");

  if (delimiter === "+++") {
    throw new Error("TOML front matter (+++) is not supported. Convert the file to YAML (---) first.");
  }

  const data = parseYamlMapping(fmText);
  return { delimiter, fmText, data, body, hasFrontMatter: true };
}

export function stringifyFrontMatter(data, body) {
  const lines = [];
  for (const [key, value] of Object.entries(data ?? {})) {
    writeYamlValue(lines, key, value, 0);
  }

  const fm = lines.join("\n") + "\n";
  const bodyOut = body ?? "";
  const sep = bodyOut.length === 0 || bodyOut.startsWith("\n") ? "" : "\n";
  return `---\n${fm}---${sep}${bodyOut}`;
}

export function collectTranslatableStrings(data) {
  const out = {};
  if (!data || typeof data !== "object" || Array.isArray(data)) return out;

  for (const [key, value] of Object.entries(data)) {
    if (!TRANSLATABLE_KEYS.has(key.toLowerCase())) continue;
    if (typeof value === "string" && value.trim()) out[key] = value;
  }
  return out;
}

export function applyTranslatedStrings(data, translated) {
  const next = { ...data };
  if (!translated) return next;
  for (const [key, value] of Object.entries(translated)) {
    if (typeof value === "string" && Object.prototype.hasOwnProperty.call(next, key)) {
      next[key] = value;
    }
  }
  return next;
}

function parseYamlMapping(fmText) {
  const repaired = preQuoteBareScalars(foldUnclosedQuotedLines(attachOrphanHandleLines(fmText)));

  const doc = YAML.parseDocument(repaired);
  if (doc.errors?.length) {
    throw new Error(`YAML front matter: ${doc.errors[0].message}`);
  }
  const data = doc.toJS() ?? {};
  if (data === null || typeof data !== "object" || Array.isArray(data)) {
    throw new Error("Front matter must be a YAML mapping");
  }
  return data;
}

function writeYamlValue(lines, key, value, indent) {
  const pad = "  ".repeat(indent);
  if (value === null || value === undefined) {
    lines.push(`${pad}${key}:`);
    return;
  }
  if (typeof value === "boolean" || typeof value === "number") {
    lines.push(`${pad}${key}: ${value}`);
    return;
  }
  if (typeof value === "string") {
    if (DATE_KEYS.has(key.toLowerCase())) {
      lines.push(`${pad}${key}: ${value}`);
      return;
    }
    lines.push(`${pad}${key}: ${quoteDouble(value)}`);
    return;
  }
  if (Array.isArray(value)) {
    if (value.length === 0) {
      lines.push(`${pad}${key}: []`);
      return;
    }
    lines.push(`${pad}${key}:`);
    for (const item of value) {
      if (item !== null && typeof item === "object") {
        lines.push(`${pad}-`);
        for (const [k, v] of Object.entries(item)) writeYamlValue(lines, k, v, indent + 1);
      } else if (typeof item === "string") {
        lines.push(`${pad}- ${quoteDouble(item)}`);
      } else {
        lines.push(`${pad}- ${item}`);
      }
    }
    return;
  }
  if (typeof value === "object") {
    lines.push(`${pad}${key}:`);
    for (const [k, v] of Object.entries(value)) writeYamlValue(lines, k, v, indent + 1);
  }
}

function attachOrphanHandleLines(fmText) {
  const lines = fmText.split(/\r?\n/);
  const out = [];
  for (const line of lines) {
    if (/^[ \t]*[@#]/.test(line) && out.length > 0 && /:\s/.test(out[out.length - 1])) {
      out[out.length - 1] = `${out[out.length - 1]} ${line.trim()}`;
      continue;
    }
    out.push(line);
  }
  return out.join("\n");
}

function foldUnclosedQuotedLines(fmText) {
  const lines = fmText.split(/\r?\n/);
  const out = [];

  for (let i = 0; i < lines.length; i++) {
    let line = lines[i];
    const pair = line.match(/^([ \t]*)([A-Za-z0-9_-]+):\s*(.*)$/);
    if (pair) {
      let val = pair[3];
      while (hasUnclosedQuotes(val) && i + 1 < lines.length) {
        const next = lines[i + 1];
        if (/^[ \t]*[A-Za-z0-9_-]+:\s/.test(next) && !hasUnclosedQuotes(val)) break;
        if (/^[ \t]*[A-Za-z0-9_-]+:\s/.test(next) && quoteCount(val) % 2 === 0) break;
        i += 1;
        val = `${val} ${next.trim()}`;
      }
      line = `${pair[1]}${pair[2]}: ${val.trim()}`;
    }
    out.push(line);
  }

  return out.join("\n");
}

function preQuoteBareScalars(fmText) {
  return fmText
    .split(/\r?\n/)
    .map((line) => {
      const pair = line.match(/^([ \t]*)([A-Za-z0-9_-]+):\s*(.*)$/);
      if (pair) {
        const [, indent, key, raw] = pair;
        const val = raw.trim();
        if (shouldLeaveBare(val)) return line;
        return `${indent}${key}: "${stripWrappingQuotes(val)}"`;
      }

      const item = line.match(/^([ \t]*)-\s+(.*)$/);
      if (item) {
        const [, indent, raw] = item;
        const val = raw.trim();
        if (shouldLeaveBare(val) || /^[A-Za-z0-9][A-Za-z0-9 _-]*$/.test(val)) return line;
        if (val.includes(":") || val.includes("#") || val.includes('"') || val.includes("'")) {
          return `${indent}- "${stripWrappingQuotes(val)}"`;
        }
      }
      return line;
    })
    .join("\n");
}

function shouldLeaveBare(val) {
  if (!val) return true;
  if (val === "|" || val === ">" || val === "|-" || val === ">-") return true;
  if (val.startsWith("{") || val.startsWith("[")) return true;
  if (val === "true" || val === "false" || val === "null") return true;
  if (/^-?\d+(\.\d+)?$/.test(val)) return true;
  if (isCleanWrapped(val, '"') || isCleanWrapped(val, "'")) return true;
  return false;
}

function isCleanWrapped(val, q) {
  if (!(val.startsWith(q) && val.endsWith(q) && val.length >= 2)) return false;
  return quoteCount(val) === 2;
}

function hasUnclosedQuotes(s) {
  return quoteCount(s) % 2 === 1;
}

function quoteCount(s) {
  let n = 0;
  for (let i = 0; i < s.length; i++) {
    if (s[i] === "\\") {
      i += 1;
      continue;
    }
    if (s[i] === '"') n += 1;
  }
  return n;
}

function stripWrappingQuotes(s) {
  let inner = s;
  if (inner.length >= 2 && inner.startsWith('"') && inner.endsWith('"')) {
    inner = inner.slice(1, -1);
  }
  return escapeQuotes(inner);
}

function escapeQuotes(s) {
  return s.replace(/\\/g, "\\\\").replace(/"/g, '\\"');
}

function quoteDouble(s) {
  return `"${s.replace(/\\/g, "\\\\").replace(/"/g, '\\"').replace(/\n/g, "\\n")}"`;
}

function escapeRegExp(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
