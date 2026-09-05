const DEFAULT_MODEL = "gpt-5.6-luna";
const DEFAULT_TIMEOUT_MS = 120_000;
const MAX_RETRIES = 4;

export function createTranslator({
  apiKey,
  model = DEFAULT_MODEL,
  timeoutMs = DEFAULT_TIMEOUT_MS,
} = {}) {
  if (!apiKey) {
    throw new Error("OPENAI_API_KEY is required");
  }

  return {
    model,
    async translateArticle({ sourceLang, targetLang, targetLanguageName, frontMatterStrings, body }) {
      const payload = {
        sourceLang,
        targetLang,
        title: frontMatterStrings.title ?? null,
        description: frontMatterStrings.description ?? null,
        otherFrontMatter: Object.fromEntries(
          Object.entries(frontMatterStrings).filter(([k]) => k !== "title" && k !== "description"),
        ),
        body,
      };

      const system = [
        "You are a professional translator for a Hugo static site.",
        `Translate from ${sourceLang} to ${targetLanguageName} (${targetLang}).`,
        "Return ONLY valid JSON with keys: title, description, otherFrontMatter, body.",
        "Rules:",
        "- Preserve Markdown structure, headings, lists, links, images, HTML, and whitespace intent.",
        "- Do not translate URLs, file paths, Hugo shortcodes ({{< ... >}} and {{% ... %}}), code spans, or fenced code blocks.",
        "- Do not translate YAML keys, slugs, dates, or IDs.",
        "- Keep link text translated when it is prose; keep the href unchanged.",
        "- Do not add commentary, quotes around the whole document, or markdown fences around the JSON.",
        "- otherFrontMatter must use the same keys you were given.",
        "- description must be a single line of prose, no raw line breaks.",
      ].join("\n");

      const data = await chatJson({
        apiKey,
        model,
        timeoutMs,
        messages: [
          { role: "system", content: system },
          { role: "user", content: JSON.stringify(payload) },
        ],
      });

      const other = data.otherFrontMatter && typeof data.otherFrontMatter === "object"
        ? data.otherFrontMatter
        : {};

      return {
        frontMatterStrings: {
          ...frontMatterStrings,
          ...(typeof data.title === "string" ? { title: data.title } : {}),
          ...(typeof data.description === "string" ? { description: data.description } : {}),
          ...other,
        },
        body: typeof data.body === "string" ? data.body : body,
      };
    },
  };
}

async function chatJson({ apiKey, model, timeoutMs, messages }) {
  let lastError;

  for (let attempt = 0; attempt <= MAX_RETRIES; attempt++) {
    const ac = new AbortController();
    const timer = setTimeout(() => ac.abort(), timeoutMs);

    try {
      const res = await fetch("https://api.openai.com/v1/chat/completions", {
        method: "POST",
        signal: ac.signal,
        headers: {
          authorization: `Bearer ${apiKey}`,
          "content-type": "application/json",
        },
        body: JSON.stringify({
          model,
          response_format: { type: "json_object" },
          messages,
        }),
      });

      const text = await res.text();

      if (res.status === 429 || res.status >= 500) {
        lastError = new Error(`OpenAI ${res.status}: ${safeSnippet(text)}`);
        await sleep(backoffMs(attempt));
        continue;
      }

      if (!res.ok) {
        throw new Error(`OpenAI ${res.status}: ${safeSnippet(text)}`);
      }

      const json = JSON.parse(text);
      const content = json?.choices?.[0]?.message?.content;
      if (typeof content !== "string" || !content.trim()) {
        throw new Error("OpenAI returned an empty completion");
      }

      return JSON.parse(stripFences(content));
    } catch (err) {
      lastError = err;
      const retryable =
        err.name === "AbortError" ||
        err.code === "ECONNRESET" ||
        err.message?.startsWith("OpenAI 429") ||
        err.message?.startsWith("OpenAI 5");
      if (!retryable || attempt === MAX_RETRIES) throw err;
      await sleep(backoffMs(attempt));
    } finally {
      clearTimeout(timer);
    }
  }

  throw lastError;
}

function backoffMs(attempt) {
  const base = Math.min(16_000, 500 * 2 ** attempt);
  return base + Math.floor(Math.random() * 250);
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

function stripFences(s) {
  return s.replace(/^```(?:json)?\s*/i, "").replace(/\s*```$/, "").trim();
}

function safeSnippet(text) {
  return String(text).replace(/\s+/g, " ").slice(0, 240);
}
