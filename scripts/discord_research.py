#!/usr/bin/env python3
"""
Background research script for Discord server and community management.
Focuses on moderation, engagement, tools, and community growth.
"""

import os
import json
import requests
from bs4 import BeautifulSoup
from datetime import datetime

# --- Config ---
OUTPUT_DIR = "data/research_output"
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "discord_management_research.md")

# Ensure output directory exists
os.makedirs(OUTPUT_DIR, exist_ok=True)

# --- Research Sources ---
SOURCES = {
    "Discord Developer Docs": {
        "url": "https://discord.com/developers/docs/intro",
        "focus": ["bot setup", "permissions", "api limits"]
    },
    "Discord Moderation Guide": {
        "url": "https://support.discord.com/hc/en-us/articles/360040347752",
        "focus": ["roles", "bans", "mutes", "rules"]
    },
    "Community Engagement": {
        "url": "https://dis.gd/building_communities",
        "focus": ["welcome messages", "events", "polls"]
    },
    "Bot Libraries": {
        "url": "https://discordpy.readthedocs.io/en/stable/",
        "focus": ["custom commands", "automation", "scheduling"]
    }
}

# --- Research Functions ---
def fetch_url(url: str) -> str:
    """Fetch content from a URL."""
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        return response.text
    except Exception as e:
        return f"Error fetching {url}: {str(e)}"

def extract_key_points(html: str, focus: list) -> dict:
    """Extract relevant sections from HTML."""
    soup = BeautifulSoup(html, "html.parser")
    results = {}
    
    # Simple extraction: headings and paragraphs
    for item in focus:
        results[item] = []
        for heading in soup.find_all(["h1", "h2", "h3", "h4"]):
            if item.lower() in heading.text.lower():
                next_node = heading.find_next_sibling()
                while next_node and next_node.name != "h2":  # Stop at next major section
                    if next_node.name == "p":
                        results[item].append(next_node.get_text(strip=True))
                    next_node = next_node.find_next_sibling()
    
    return results

def compile_research() -> dict:
    """Compile research from all sources."""
    research = {
        "metadata": {
            "generated_at": datetime.now().isoformat(),
            "sources": list(SOURCES.keys())
        },
        "sections": {
            "moderation": {},
            "engagement": {},
            "tools": {},
            "community_growth": {}
        }
    }
    
    for source_name, source_data in SOURCES.items():
        print(f"Researching: {source_name}...")
        html = fetch_url(source_data["url"])
        if not html.startswith("Error"):
            key_points = extract_key_points(html, source_data["focus"])
            for section in source_data["focus"]:
                if section in ["roles", "bans", "mutes", "rules"]:
                    research["sections"]["moderation"][section] = key_points.get(section, [])
                elif section in ["welcome messages", "events", "polls"]:
                    research["sections"]["engagement"][section] = key_points.get(section, [])
                elif section in ["custom commands", "automation", "scheduling"]:
                    research["sections"]["tools"][section] = key_points.get(section, [])
                else:
                    research["sections"]["community_growth"][section] = key_points.get(section, [])
    
    return research

def save_research(research: dict) -> str:
    """Save research to markdown file."""
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write("# Discord Server & Community Management Research\n\n")
        f.write(f"Generated: {research['metadata']['generated_at']}\n\n")
        f.write("## Sources\n")
        for source in research["metadata"]["sources"]:
            f.write(f"- {source}\n")
        
        for section_name, section_content in research["sections"].items():
            f.write(f"\n## {section_name.capitalize()}\n")
            for topic, points in section_content.items():
                f.write(f"### {topic.replace('_', ' ').title()}\n")
                if points:
                    for point in points:
                        f.write(f"- {point}\n")
                else:
                    f.write("- No specific details found.\n")
    
    return OUTPUT_FILE

# --- Main ---
if __name__ == "__main__":
    print("Starting Discord management research...")
    research = compile_research()
    output_path = save_research(research)
    print(f"Research complete. Saved to: {output_path}")