import os
from playwright.async_api import async_playwright

async def run_search(query: str) -> str:
    """Open Edge, search query on Bing, and return the page title."""
    headless = os.getenv("HEADLESS", "1") != "0"
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="msedge", headless=headless)
        page = await browser.new_page()
        await page.goto("https://www.bing.com")
        await page.fill('textarea[name="q"]', query)
        await page.keyboard.press("Enter")
        # wait a bit for results
        await page.wait_for_timeout(2000)
        title = await page.title()
        await browser.close()
    return title
