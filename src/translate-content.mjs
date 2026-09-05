#!/usr/bin/env node
/**
 * Translate Hugo articles from content/{source}/{category}/ into
 * content/{lang}/{category}/ for every language directory present.
 *
 * Auth: OPENAI_API_KEY in the environment. Never pass a key on the CLI.
 *
 *   node src/translate-content.mjs --content ./content
 *   node src/translate-content.mjs --fix-only
 *   node src/translate-content.mjs --langs es,fr --category ai
 */

import { mkdir, readFile, writeFile, stat } from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import {
  applyTranslatedStrings,
  collectTranslatableStrings,
  splitFrontMatter,
  stringifyFrontMatter,
} from "./frontmatter.mjs";
import { createTranslator } from "./openai.mjs";
import { languageName, listLangCodes, listMarkdownFiles } from "./walk.mjs";

const DEFAULTS = {
  content: "content",
  source: "en",
  concurrency: 20,
  model: "gpt-5.6-luna",
};

function parseArgs(argv) {
  const out = {
    content: DEFAULTS.content,
    source: DEFAULTS.source,
    langs: null,
    category: null,
    concurrency: DEFAULTS.concurrency,
    model: DEFAULTS.model,
    force: false,
    fixOnly: false,
    dryRun: false,
    help: false,
  };

  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    const next = () => {
      const v = argv[++i];
      if (v === undefined || v.startsWith("--")) throw new Error(`Missing value for ${a}`);
      return v;
    };
    switch (a) {
      case "--content":
        out.content = next();
        break;
      case "--source":
        out.source = next();
        break;
      case "--langs":
        out.langs = next()
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean);
        break;
      case "--category":
        out.category = next();
        break;
      case "--concurrency":
        out.concurrency = Math.max(1, Number.parseInt(next(), 10) || DEFAULTS.concurrency);
        break;
      case "--model":
        out.model = next();
        break;
      case "--force":
        out.force = true;
        break;
      case "--fix-only":
        out.fixOnly = true;
        break;
      case "--dry-run":
        out.dryRun = true;
        break;
      case "--help":
      case "-h":
        out.help = true;
        break;
      default:
        throw new Error(`Unknown flag ${a}`);
    }
  }
  return out;
}

function usage() {
  return `Usage:
  node src/translate-content.mjs [options]

Options:
  --content DIR       Content root (default: content)
  --source LANG       Source language directory (default: en)
  --langs a,b,c       Target langs (default: every sibling of --source)
  --category NAME     Only this section under the source language
  --concurrency N     Parallel OpenAI calls (default: 3)
  --model NAME        OpenAI model (default: gpt-5.6-luna)
  --force             Overwrite existing translations
  --fix-only          Rewrite front matter quoting; do not call OpenAI
  --dry-run           Print work, write nothing
  --help

Environment:
  OPENAI_API_KEY      Required unless --fix-only or --dry-run
`;
}

function log(event, fields = {}) {
  process.stdout.write(`${JSON.stringify({ ts: new Date().toISOString(), event, ...fields })}\n`);
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) {
    process.stdout.write(usage());
    return;
  }

  const contentRoot = path.resolve(args.content);
  const sourceRoot = path.join(contentRoot, args.source);
  const sourceStat = await stat(sourceRoot).catch(() => null);
  if (!sourceStat?.isDirectory()) {
    throw new Error(`Source language directory not found: ${sourceRoot}`);
  }

  const discovered = await listLangCodes(contentRoot, args.source);
  const targets = args.langs ?? discovered;
  if (!args.fixOnly && targets.length === 0) {
    throw new Error(
      `No target language directories under ${contentRoot}. Create content/{lang}/ or pass --langs.`,
    );
  }

  const relFiles = await listMarkdownFiles(sourceRoot);
  const selected = relFiles.filter((rel) => {
    if (!args.category) return true;
    return rel === `${args.category}` || rel.startsWith(`${args.category}/`);
  });

  if (selected.length === 0) {
    throw new Error("No Markdown files matched");
  }

  if (args.fixOnly) {
    const langs = [args.source, ...targets];
    const jobs = [];
    for (const lang of langs) {
      const root = path.join(contentRoot, lang);
      const files = await listMarkdownFiles(root);
      for (const rel of files) {
        if (args.category && !(rel === args.category || rel.startsWith(`${args.category}/`))) continue;
        jobs.push({ lang, rel, filePath: path.join(root, rel) });
      }
    }
    log("plan", { mode: "fix-only", files: jobs.length, langs });
    for (const job of jobs) {
      await fixFile(job.filePath, { dryRun: args.dryRun, lang: job.lang, rel: job.rel });
    }
    return;
  }

  const apiKey = process.env.OPENAI_API_KEY;
  if (!apiKey && !args.dryRun) {
    throw new Error("OPENAI_API_KEY is not set");
  }

  const translator = args.dryRun
    ? null
    : createTranslator({ apiKey, model: args.model });

  const jobs = [];
  for (const rel of selected) {
    for (const lang of targets) {
      jobs.push({
        rel,
        lang,
        src: path.join(sourceRoot, rel),
        dest: path.join(contentRoot, lang, rel),
      });
    }
  }

  log("plan", {
    mode: "translate",
    sourceFiles: selected.length,
    langs: targets,
    jobs: jobs.length,
    concurrency: args.concurrency,
    model: args.model,
    dryRun: args.dryRun,
    force: args.force,
  });

  const summary = { translated: 0, skipped: 0, fixed: 0, failed: 0 };

  await mapLimit(jobs, args.concurrency, async (job) => {
    try {
      const result = await translateOne(job, {
        translator,
        sourceLang: args.source,
        force: args.force,
        dryRun: args.dryRun,
      });
      summary[result]++;
    } catch (err) {
      summary.failed++;
      log("error", {
        lang: job.lang,
        file: job.rel,
        message: err.message,
      });
    }
  });

  log("done", summary);
  if (summary.failed > 0) process.exitCode = 1;
}

async function translateOne(job, { translator, sourceLang, force, dryRun }) {
  const exists = await stat(job.dest).then(() => true).catch(() => false);
  if (exists && !force) {
    await fixFile(job.dest, { dryRun, lang: job.lang, rel: job.rel, quiet: true });
    log("skip", { lang: job.lang, file: job.rel, reason: "exists" });
    return "skipped";
  }

  const raw = await readFile(job.src, "utf8");
  const parsed = splitFrontMatter(raw);
  const fmStrings = collectTranslatableStrings(parsed.data);

  if (dryRun) {
    log("would_translate", { lang: job.lang, file: job.rel });
    return "translated";
  }

  const translated = await translator.translateArticle({
    sourceLang,
    targetLang: job.lang,
    targetLanguageName: languageName(job.lang),
    frontMatterStrings: fmStrings,
    body: parsed.body,
  });

  const nextData = applyTranslatedStrings(parsed.data, translated.frontMatterStrings);
  const out = stringifyFrontMatter(nextData, translated.body);
  await mkdir(path.dirname(job.dest), { recursive: true });
  await writeFile(job.dest, out, "utf8");
  log("translated", { lang: job.lang, file: job.rel });
  return "translated";
}

async function fixFile(filePath, { dryRun, lang, rel, quiet = false }) {
  const raw = await readFile(filePath, "utf8");
  const parsed = splitFrontMatter(raw);
  if (!parsed.hasFrontMatter) return "fixed";
  const out = stringifyFrontMatter(parsed.data, parsed.body);
  if (out === raw) return "fixed";
  if (dryRun) {
    if (!quiet) log("would_fix", { lang, file: rel });
    return "fixed";
  }
  await writeFile(filePath, out, "utf8");
  if (!quiet) log("fixed", { lang, file: rel });
  return "fixed";
}

async function mapLimit(items, limit, fn) {
  if (items.length === 0) return;
  let cursor = 0;
  async function worker() {
    while (true) {
      const idx = cursor++;
      if (idx >= items.length) return;
      await fn(items[idx], idx);
    }
  }
  const n = Math.min(limit, items.length);
  await Promise.all(Array.from({ length: n }, worker));
}

process.on("unhandledRejection", (err) => {
  log("fatal", { message: err?.message ?? String(err) });
  process.exit(1);
});

main().catch((err) => {
  process.stderr.write(`${err.message}\n\n${usage()}`);
  process.exit(1);
});
