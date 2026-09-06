#!/usr/bin/env python3
"""
Google News RSS Crawler with Optimized Viral Tweet Generation
Writes directly to ./content/en/{industry} directory structure.
Uses 1 large + 1 micro account per tweet for optimal engagement.
Generates 5-10 relevant tags per article.
"""

import os
import sys
import json
import time
import asyncio
import re
import random
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

# X.com accounts: 1 large + 1 micro per industry for optimal engagement
INDUSTRY_ACCOUNTS = {
    "AI": {
        "large": ["@openai", "@DeepMind"],
        "micro": ["@jacksonwofford", "@npew", "@hardmaru", "@emollick", "@karpathy"]
    },
    "Business": {
        "large": ["@WSJ", "@Bloomberg"],
        "micro": ["@ycombinator", "@jason", "@peakscale", "@paulg", "@naval"]
    },
    "Cosmos": {
        "large": ["@nasa", "@ESA"],
        "micro": ["@CarlSagan", "@sciencechannel", "@universe_today", "@nasahubble", "@spacedotcom"]
    },
    "Compliance": {
        "large": ["@CISAgov", "@SecurityWeekly"],
        "micro": ["@jeremiahgrossman", "@troyhunt", "@schneierblog", "@SwiftOnSecurity", "@davidbott"]
    },
    "Crypto": {
        "large": ["@CoinDesk", "@Cointelegraph"],
        "micro": ["@aantonop", "@chrislbrwn", "@aaronkday", "@raoulGMI", "@DocumentingBTC"]
    },
    "Entertainment": {
        "large": ["@Variety", "@TheHollywoodRpt"],
        "micro": ["@entmaven", "@nikitalowrey", "@toniarmstrong", "@enterainment", "@carlesgates"]
    },
    "Health": {
        "large": ["@WHO", "@CDCgov"],
        "micro": ["@drericding", "@sailorrooscott", "@AriellaNarrative", "@drsanjaygupta", "@thehealthsite"]
    },
    "Science": {
        "large": ["@ScienceMagazine", "@NatGeo"],
        "micro": ["@carlasomoza", "@drsarahvj", "@BrianMalow", "@SciCommCollab", "@ScienceDaily"]
    },
    "Sports": {
        "large": ["@ESPN", "@SkySports"],
        "micro": ["@sportsintel", "@FournierFootball", "@SBNationGIF", "@SoccerInsider", "@thescore"]
    },
    "Technology": {
        "large": ["@TechCrunch", "@vergetech"],
        "micro": ["@mmasnick", "@stevesilberman", "@theonion", "@swyx", "@jsoverson"]
    },
    "USA": {
        "large": ["@AP", "@Reuters"],
        "micro": ["@nprpolitics", "@NPR", "@NBCNews", "@CBSNews", "@CNN"]
    },
    "World": {
        "large": ["@Reuters", "@BBCNews"],
        "micro": ["@FT", "@TheEconomist", "@AlJazeera", "@ReutersWorld", "@globalbriefing"]
    }
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
    def __init__(self, api_key: Optional[str] = None, content_base: str = "./content/en"):
        """Initialize the crawler with Anthropic API key."""
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY environment variable not set")
        self.client = Anthropic(api_key=self.api_key)
        self.content_base = content_base
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
        text = re.sub(r'\*+', '', text)
        text = re.sub(r'_+', '', text)
        text = text.strip()
        
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

    async def generate_tags(self, title: str, description: str, content: str, industry: str) -> List[str]:
        """Generate 5-10 relevant tags based on article content."""
        try:
            tag_prompt = f"""Analyze this article and generate 5-10 relevant, SEO-friendly tags (lowercase, no spaces, use hyphens).
Include: the industry category, main topics, and key concepts mentioned.

Title: {title}
Description: {description}
First 500 chars of content: {content[:500]}

Return ONLY a JSON array of tags, like this:
["tag1", "tag2", "tag3", "tag4", "tag5"]

Ensure:
- 5-10 tags total
- lowercase only
- hyphens for multi-word tags
- no # or @ symbols
- always include: "{industry.lower()}"
"""

            message = self.client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=200,
                messages=[{"role": "user", "content": tag_prompt}]
            )

            response_text = ""
            for block in message.content:
                if hasattr(block, 'text'):
                    response_text += block.text

            # Extract JSON array
            try:
                tags = json.loads(response_text)
                if isinstance(tags, list) and 5 <= len(tags) <= 10:
                    return tags
            except json.JSONDecodeError:
                start = response_text.find("[")
                end = response_text.rfind("]") + 1
                if start != -1 and end > start:
                    tags = json.loads(response_text[start:end])
                    if isinstance(tags, list) and 5 <= len(tags) <= 10:
                        return tags

        except Exception as e:
            print(f"  ⚠ Error generating tags: {str(e)}", file=sys.stderr)

        # Fallback tags if generation fails
        return [industry.lower(), "news", "breaking-news", "trending", "analysis"]

    def generate_slug(self, title: str) -> str:
        """Generate SEO slug."""
        words = title.split()[:7]
        return slugify(" ".join(words), max_length=60)

    def enrich_tweet(self, tweet: str, slug: str, industry: str) -> str:
        """Format tweet: @users tweet_content url #hashtags"""
        accounts = INDUSTRY_ACCOUNTS.get(industry, {"large": ["@News"], "micro": ["@Trending"]})
        hashtags = INDUSTRY_HASHTAGS.get(industry, ["#News", "#Trending", "#Breaking"])
        
        # Randomly select 1 large and 1 micro account
        large_account = random.choice(accounts["large"])
        micro_account = random.choice(accounts["micro"])
        
        url = f"www.rnews1.com/en/{industry.lower()}/{slug}/"
        
        # Format: @user1 @user2 tweet_content url #hashtag1 #hashtag2 #hashtag3
        return f"{large_account} {micro_account} {tweet} {url} {hashtags[0]} {hashtags[1]} {hashtags[2]}"

    async def create_article_file(self, industry: str, article_json: str, slug: str) -> Optional[Tuple[str, str]]:
        """Create Hugo markdown with tweet and tags in frontmatter."""
        try:
            data = json.loads(article_json)
            title = data.get("title", "Untitled")
            description = data.get("description", "")
            content = data.get("content", "")
            base_tweet = data.get("tweet", "Check it out!")

            # Generate relevant tags (5-10)
            tags = await self.generate_tags(title, description, content, industry)
            
            enriched_tweet = self.enrich_tweet(base_tweet, slug, industry)
            
            # Escape quotes for YAML safety
            enriched_tweet = enriched_tweet.replace('"', '\\"')

            now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

            # Proper YAML frontmatter with generated tags
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

    def setup_directories(self) -> None:
        """Create industry directories in ./content/en/"""
        for industry in INDUSTRIES:
            industry_dir = os.path.join(self.content_base, industry.lower())
            os.makedirs(industry_dir, exist_ok=True)

    def write_article_to_file(self, industry: str, slug: str, content: str) -> bool:
        """Write article to ./content/en/{industry}/{slug}.md"""
        try:
            industry_dir = os.path.join(self.content_base, industry.lower())
            file_path = os.path.join(industry_dir, f"{slug}.md")
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)
            print(f"  ✓ Wrote {industry.lower()}/{slug}.md", file=sys.stderr)
            return True
        except Exception as e:
            print(f"  ✗ Error writing file: {str(e)}", file=sys.stderr)
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

            result = await self.create_article_file(industry, article_json, slug)
            if result:
                slug, content = result
                return self.write_article_to_file(industry, slug, content)
            return False

        except Exception as e:
            print(f"  ✗ {industry} error: {str(e)}", file=sys.stderr)
            return False

    async def process_batch(self, batch: List[str]) -> List[bool]:
        """Process a batch of industries in parallel."""
        tasks = [self.process_industry(industry) for industry in batch]
        return await asyncio.gather(*tasks)

    async def run_async(self) -> int:
        """Execute workflow with batched processing."""
        print(f"Writing to: {os.path.abspath(self.content_base)}\n", file=sys.stderr)

        try:
            self.setup_directories()

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
            
            return success_count

        except Exception as e:
            print(f"✗ Fatal error: {str(e)}", file=sys.stderr)
            return 0

    def run(self) -> int:
        """Entry point."""
        return asyncio.run(self.run_async())


def main():
    """Main."""
    try:
        # Get content base directory from current working directory
        content_base = "./content/en"
        
        crawler = GoogleNewsCrawler(content_base=content_base)
        result = crawler.run()
        
        if result > 0:
            print(f"✓ Successfully created {result} articles")
            sys.exit(0)
        else:
            print("✗ Failed to create articles")
            sys.exit(1)
    except Exception as e:
        print(f"✗ Fatal: {str(e)}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
