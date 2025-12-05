import feedparser
import sys
import json
from datetime import datetime

def debug_rss(username):
    url = f"https://letterboxd.com/{username}/rss/"
    print(f"Fetching {url}...")
    
    feed = feedparser.parse(url)
    
    if feed.bozo:
        print(f"Error parsing feed: {feed.bozo_exception}")
        return

    print(f"Found {len(feed.entries)} entries.")
    
    for i, entry in enumerate(feed.entries):
        print(f"\n--- Entry {i+1} ---")
        print(f"Title: {entry.get('title', 'N/A')}")
        print(f"Link: {entry.get('link', 'N/A')}")
        
        # Check Letterboxd specific fields
        print("Letterboxd Fields:")
        for key in entry.keys():
            if key.startswith('letterboxd_'):
                print(f"  {key}: {entry[key]}")
        
        # Check TMDB ID
        if hasattr(entry, 'tmdb_movieid'):
            print(f"TMDB ID: {entry.tmdb_movieid}")
        else:
            print("TMDB ID: Not found")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python debug_rss.py <username>")
    else:
        debug_rss(sys.argv[1])
