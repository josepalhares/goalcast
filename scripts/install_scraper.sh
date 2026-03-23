#!/bin/bash
# Install dependencies for the xG scraper (local use only)
pip install playwright
playwright install chromium
echo "Done. Run: python scripts/scrape_xg.py"
