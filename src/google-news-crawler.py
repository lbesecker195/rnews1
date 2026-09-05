#!/usr/bin/env python3
"""
Google News RSS Crawler with Viral Tweet Generation - Batched Processing
Processes industries in batches of 2 to reduce rate limiting and improve reliability.
"""

import os
import sys
import json
import time
import shutil
import zipfile
import tempfile
import asyncio
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import feedparser
import requests
from anthropic import Anthropic
from slugify import slugify

# Google News RSS URLs by industry with 24-hour filter (when:1d)
GOOGLE_NEWS_FEEDS = {
    "AI": "https://news.google.com/rss/search?q=artificial+intelligence+when:1d&hl=en-US&gl=US&ceid=US:en",
    "Business": "https://news.google.com/rss/search?q=business+when:1d&hl=en-US&gl=US&ceid=US:en",
    "Cosmos": "https://news.google.com/rss/search?q=astronomy+space+cosmos+when:1d&hl=en-US&gl=US&ceid=US:en",
    "Compliance": "https://news.google.com/rss/search?q=compliance+breach+security+when:1d&hl=en-US&gl=US&ceid=US:en",
    "Crypto": "https://news.google.com/rss/search?q=cryptocurrency+bitcoin+blockchain+when:1d&hl=en-US&gl=US&ceid=US:en",
    "Entertainment": "https://news.google.com/rss/search?q=entertainment+when:1d&hl=en-US&gl=US&ceid=US:en",
    "Health": "https://news.google.com/rss/search?q=health+medical+when:1d&hl=en-US&gl=US&ceid=US:en",
    "Science": "https://news.google.com/rss/search?q=science+when:1d&hl=en-US&gl=US&ceid=US:en",
    "Sports": "https://news.google.com/rss/search?q=sports+when:1d&hl=en-US&gl=US&ceid=US:en",
    "Technology": "https://news.google.com/rss/search?q=technology+when:1d&hl=en-US&gl=US&ceid=US:en",
    "USA": "https://news.google.com/rss/search?q=USA+when:1d&hl=en-US&gl=US&ceid=US:en",
    "World": "https://news.google.com/rss/search?q=world+news+when:1d&hl=en-US&gl=US&ceid=US:en",
}

INDUSTRIES = list(GOOGLE_NEWS_FEEDS.keys())
BATCH_SIZE = 2

# Industry-specific X.com influencers and hashtags
INDUSTRY_INFLUENCERS = {
    "AI": ["@ylecun", "@karpathy"],
    "Business": ["@elonmusk", "@garyvee"],
    "Cosmos": ["@nasa", "@neil_tyson"],
    "Compliance": ["@CISAgov", "@SecurityJoe"],
    "Crypto": ["@CZ_binance", "@aantonop"],
    "Entertainment": ["@eonline", "@Variety"],
    "Health": ["@WHO", "@DrFauci"],
    "Science": ["@ScienceMagazine", "@NatGeo"],
    "Sports": ["@ESPN", "@SkySports"],
    "Technology": ["@TechCrunch", "@vergetech"],
    "USA": ["@NPR", "@CNNpolitics"],
    "World": ["@Reuters", "@AP"],
}

INDUSTRY_HASHTAGS = {
    "AI": ["#AI", "#MachineLearning", "#ArtificialIntelligence"],
    "Business": ["#Business", "#Economy", "#Markets"],
    "Cosmos": ["#Space", "#NASA", "#Astronomy"],
    "Compliance": ["#Cybersecurity", "#Security", "#DataBreach"],
    "Crypto": ["#Crypto", "#Bitcoin", "#Web3"],
    "Entertainment": ["#Entertainment", "#Hollywood", "#PopCulture"],
    "Health": ["#Health", "#MedicalNews", "#Wellness"],
    "Science": ["#Science", "#Research", "#Innovation"],
    "Sports": ["#Sports", "#Athletics", "#GameDay"],
    "Technology": ["#Tech", "#Innovation", "#StartUp"],
    "USA": ["#USA", "#Politics", "#News"],
    "World": ["#WorldNews", "#International", "#GlobalNews"],
}


class GoogleNewsCrawler:
    def __init__(self, api_key: Optional[str] = None):
        """Initialize the crawler with Anthropic API key."""
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY environment variable not set")
        self.client = Anthropic(api_key=self.api_key)
        self.temp_dir = None
        self.semaphore = asyncio.Semaphore(2)

    def fetch_feed(self, industry: str, retries: int = 1) -> List[Dict]:
        """Fetch RSS feed for an industry with retry logic."""
        url = GOOGLE_NEWS_FEEDS[industry]
        for attempt in range(retries + 1):
            try:
                print(f"  Fetching {industry} feed (attempt {attempt + 1})...", file=sys.stderr)
                feed = feedparser.parse(url)
                if feed.entries:
                    print(f"  ✓ Found {len(feed.entries)} articles for {industry}", file=sys.stderr)
                    return self._filter_recent_articles(feed.entries)
                else:
                    print(f"  ⚠ No entries found for {industry}", file=sys.stderr)
                    return []
            except Exception as e:
                print(f"  ✗ Error fetching {industry}: {str(e)}", file=sys.stderr)
                if attempt < retries:
                    time.sleep(2 ** attempt)
                continue
        return []

    def _filter_recent_articles(self, entries: List, hours: int = 24) -> List[Dict]:
        """Filter articles from the last N hours."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        recent = []

        for entry in entries:
            try:
                pub_time = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                if pub_time > cutoff:
                    recent.append({
                        "title": entry.get("title", "No Title"),
                        "link": entry.get("link", ""),
                        "summary": entry.get("summary", ""),
                        "published": entry.get("published", ""),
                        "author": entry.get("author", ""),
                        "source": entry.get("source", {}).get("title", ""),
                    })
            except (AttributeError, IndexError, TypeError):
                continue

        return recent[:10]

    def _extract_number(self, text: str) -> int:
        """Extract number from text, handling markdown formatting."""
        # Remove markdown formatting
        text = re.sub(r'\*+', '', text)
        text = re.sub(r'_+', '', text)
        text = text.strip()
        
        # Find first integer in text
        match = re.search(r'\d+', text)
        if match:
            return int(match.group())
        raise ValueError(f"No number found in: {text}")

    async def select_and_synthesize_article(self, industry: str, articles: List[Dict], retries: int = 1) -> Optional[str]:
        """Select most viral article and synthesize with tweet."""
        if not articles:
            return None

        async with self.semaphore:
            articles_for_selection = "\n\n".join([
                f"Article {i+1}: {a['title'][:100]}\nSummary: {a['summary'][:150]}"
                for i, a in enumerate(articles[:10])
            ])

            selection_prompt = f"""Expert X.com analyst for {industry}: Which ONE article will go viral based on novelty, emotional impact, and shareability?

{articles_for_selection}

Reply with ONLY the number (1-{len(articles)}) - no explanation, no markdown, just the number."""

            try:
                selection_msg = self.client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=10,
                    messages=[{"role": "user", "content": selection_prompt}]
                )

                selection_text = ""
                for block in selection_msg.content:
                    if hasattr(block, 'text'):
                        selection_text += block.text

                selected_idx = self._extract_number(selection_text) - 1
                selected_article = articles[min(selected_idx, len(articles)-1)]
            except Exception as e:
                print(f"  ⚠ Viral selection error for {industry}: {str(e)}", file=sys.stderr)
                selected_article = articles[0]

            selected_text = f"""Title: {selected_article['title']}
Source: {selected_article['source']}
Summary: {selected_article['summary']}"""

            synthesis_prompt = f"""Create a ~2000 word professional article on this {industry} news:

{selected_text}

Also create an engaging X.com tweet (max 280 chars) that will resonate with the {industry} community.

Output ONLY valid JSON (no markdown, no formatting):
{{
  "title": "SEO headline 5-7 words",
  "description": "One compelling sentence",
  "content": "~2000 word markdown article",
  "tweet": "Engaging tweet under 280 chars"
}}"""

            for attempt in range(retries + 1):
                try:
                    print(f"  Synthesizing {industry} (attempt {attempt + 1})...", file=sys.stderr)
                    message = self.client.messages.create(
                        model="claude-haiku-4-5-20251001",
                        max_tokens=4000,
                        messages=[{"role": "user", "content": synthesis_prompt}]
                    )

                    response_text = ""
                    for block in message.content:
                        if hasattr(block, 'text'):
                            response_text += block.text

                    if not response_text:
                        raise ValueError("No text content")

                    try:
                        data = json.loads(response_text)
                        print(f"  ✓ Synthesized article & tweet for {industry}", file=sys.stderr)
                        return json.dumps(data)
                    except json.JSONDecodeError:
                        start = response_text.find("{")
                        end = response_text.rfind("}") + 1
                        if start != -1 and end > start:
                            data = json.loads(response_text[start:end])
                            print(f"  ✓ Synthesized article & tweet for {industry}", file=sys.stderr)
                            return json.dumps(data)
                        raise
                except Exception as e:
                    print(f"  ✗ Error synthesizing {industry}: {str(e)}", file=sys.stderr)
                    if attempt < retries:
                        await asyncio.sleep(2 ** attempt)

            return None

    def generate_slug(self, title: str) -> str:
        """Generate SEO slug."""
        words = title.split()[:7]
        return slugify(" ".join(words), max_length=60)

    def enrich_tweet(self, tweet: str, slug: str, industry: str) -> str:
        """Add influencers, hashtags, and URL to tweet."""
        influencers = INDUSTRY_INFLUENCERS.get(industry, ["@News", "@Trending"])
        hashtags = INDUSTRY_HASHTAGS.get(industry, ["#News", "#Trending", "#Breaking"])

        url = f"www.rnews1.com/en/{industry.lower()}/{slug}/"

        return f"{tweet} {url}\n\n{influencers[0]} {influencers[1]} {hashtags[0]} {hashtags[1]} {hashtags[2]}"

    def create_article_file(self, industry: str, article_json: str, slug: str) -> Optional[Tuple[str, str]]:
        """Create Hugo markdown with tweet in frontmatter."""
        try:
            data = json.loads(article_json)
            title = data.get("title", "Untitled")
            description = data.get("description", "")
            content = data.get("content", "")
            base_tweet = data.get("tweet", "Check it out!")

            enriched_tweet = self.enrich_tweet(base_tweet, slug, industry)

            now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            tags = [industry.lower()]

            front_matter = f"""---
title: "{title}"
description: "{description}"
date: {now}
draft: false
categories: ["{industry}"]
tags: {json.dumps(tags)}
tweet: "{enriched_tweet}"
---

"""

            return slug, front_matter + content
        except Exception as e:
            print(f"  ✗ Error creating article: {str(e)}", file=sys.stderr)
            return None

    def setup_directories(self, base_dir: str) -> None:
        """Create industry directories."""
        for industry in INDUSTRIES:
            os.makedirs(os.path.join(base_dir, "c", industry), exist_ok=True)

    def write_article_to_file(self, base_dir: str, industry: str, slug: str, content: str) -> bool:
        """Write article to file."""
        try:
            file_path = os.path.join(base_dir, "c", industry, f"{slug}.md")
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)
            print(f"  ✓ Wrote {industry}/{slug}.md", file=sys.stderr)
            return True
        except Exception as e:
            print(f"  ✗ Error writing file: {str(e)}", file=sys.stderr)
            return False

    def create_zip(self, base_dir: str, output_path: str) -> bool:
        """Create zip file."""
        try:
            print(f"Creating zip: {output_path}", file=sys.stderr)
            with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zipf:
                for root, dirs, files in os.walk(os.path.join(base_dir, "c")):
                    for file in files:
                        file_path = os.path.join(root, file)
                        arcname = os.path.relpath(file_path, base_dir)
                        zipf.write(file_path, arcname)
            print(f"✓ Created: {output_path}", file=sys.stderr)
            return True
        except Exception as e:
            print(f"✗ Error: {str(e)}", file=sys.stderr)
            return False

    async def process_industry(self, industry: str) -> bool:
        """Process industry asynchronously."""
        try:
            articles = self.fetch_feed(industry, retries=1)
            if not articles:
                return False

            article_json = await self.select_and_synthesize_article(industry, articles, retries=1)
            if not article_json:
                return False

            data = json.loads(article_json)
            slug = self.generate_slug(data.get("title", "article"))

            result = self.create_article_file(industry, article_json, slug)
            if result:
                slug, content = result
                return self.write_article_to_file(self.temp_dir, industry, slug, content)
            return False

        except Exception as e:
            print(f"  ✗ {industry} error: {str(e)}", file=sys.stderr)
            return False

    async def process_batch(self, batch: List[str]) -> List[bool]:
        """Process a batch of industries in parallel."""
        tasks = [self.process_industry(industry) for industry in batch]
        return await asyncio.gather(*tasks)

    async def run_async(self) -> Optional[str]:
        """Execute workflow with batched processing."""
        self.temp_dir = tempfile.mkdtemp()
        print(f"Working directory: {self.temp_dir}\n", file=sys.stderr)

        try:
            self.setup_directories(self.temp_dir)

            # Process industries in batches
            all_results = []
            for i in range(0, len(INDUSTRIES), BATCH_SIZE):
                batch = INDUSTRIES[i:i+BATCH_SIZE]
                batch_num = (i // BATCH_SIZE) + 1
                total_batches = (len(INDUSTRIES) + BATCH_SIZE - 1) // BATCH_SIZE
                
                print(f"🚀 Batch {batch_num}/{total_batches}: Processing {', '.join(batch)}...", file=sys.stderr)
                batch_results = await self.process_batch(batch)
                all_results.extend(batch_results)
                
                # Wait between batches to reduce rate limiting
                if i + BATCH_SIZE < len(INDUSTRIES):
                    time.sleep(2)

            success_count = sum(1 for r in all_results if r)
            print(f"\n✓ Processed {success_count}/{len(INDUSTRIES)} industries", file=sys.stderr)

            downloads_dir = os.path.expanduser("~/Downloads")
            timestamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
            zip_filename = f"google-news-articles-{timestamp}.zip"
            zip_path = os.path.join(downloads_dir, zip_filename)

            if self.create_zip(self.temp_dir, zip_path):
                print(f"✓ Saved: {zip_path}", file=sys.stderr)
                return zip_path
            return None

        finally:
            if self.temp_dir and os.path.exists(self.temp_dir):
                shutil.rmtree(self.temp_dir)

    def run(self) -> Optional[str]:
        """Entry point."""
        return asyncio.run(self.run_async())


def main():
    """Main."""
    try:
        crawler = GoogleNewsCrawler()
        result = crawler.run()
        if result:
            print(f"✓ Articles saved: {result}")
            sys.exit(0)
        else:
            print("✗ Failed")
            sys.exit(1)
    except Exception as e:
        print(f"✗ Fatal: {str(e)}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
