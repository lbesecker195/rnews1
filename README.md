# hugo-i18n-translate

Translate `content/en/{category}/*.md` into every sibling `content/{lang}/{category}/` directory using OpenAI `gpt-5.6-luna`, and rewrite Hugo YAML front matter so `title` / `description` strings are safely quoted.

## Layout this script expects

```
content/
  en/ai/some-article.md
  es/                 # target language present
  fr/
  zh/
```

Targets are discovered from directories next to `en`. Existing files are left alone unless you pass `--force`. Missing translations are created with the same relative path so Hugo can pair them.

## Setup

```bash
cd hugo-i18n-translate
export OPENAI_API_KEY="sk-..."    # do not commit this
node src/translate-content.mjs --help
```

`node_modules/yaml` is already vendored. Node.js 22 or 24 is required.

## Commands

Rewrite quoting on every language, no API calls:

```bash
node src/translate-content.mjs --content ./content --fix-only
```

Translate every English article that is missing in other languages:

```bash
node src/translate-content.mjs --content ./content
```

One section, two languages, overwrite:

```bash
node src/translate-content.mjs --content ./content --category health --langs es,fr --force
```

Preview work:

```bash
node src/translate-content.mjs --content ./content --dry-run
```

## What is translated

- `title`, `description`, `summary`, `subtitle`, `excerpt`
- Markdown body

Left alone: `date`, `draft`, `translationKey`, slugs, URLs, shortcodes, fenced code, categories/tags keys (tag *values* stay in the source language so taxonomies do not fragment).

## Ops notes

- Concurrency default is 3. Raise carefully; 429s are retried with jitter.
- Each article is one Chat Completions call with `response_format=json_object`.
- Do not put the API key in the repo, argv, or a committed `.env`.
- If a key was pasted into chat or a ticket, rotate it.
