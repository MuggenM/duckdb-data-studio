import time
from playwright.sync_api import sync_playwright

def test_duckdb_studio_canvas():
    print("Starting Playwright E2E Verification for DuckDB Studio...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        # Navigate to studio app
        print("Navigating to http://localhost:8086/...")
        page.goto('http://localhost:8086/', wait_until='networkidle')
        page.wait_for_timeout(3000)
        
        title = page.title()
        print(f"Page title: '{title}'")
        assert "DuckDB Data Studio" in title, f"Unexpected page title: {title}"
        
        # Click on Visual Query Builder sub-tab using exact role
        print("Clicking on 'Visual Query Builder' tab...")
        vqb_tab = page.get_by_role('tab', name='Visual Query Builder')
        vqb_tab.click()
        page.wait_for_timeout(3000)
        
        # Take screenshot of the Canvas Pipeline Studio
        screenshot_path = '/home/martin/.gemini/antigravity-cli/brain/71b2a41a-99fd-4137-a776-0414ab01caec/.tempmediaStorage/canvas_studio_e2e.png'
        page.screenshot(path=screenshot_path)
        print(f"Screenshot saved to: {screenshot_path}")
        
        # Verify canvas node elements are present in DOM
        print("Verifying Canvas Pipeline Studio UI elements...")
        page_content = page.content()
        assert "Canvas Visual Pipeline Studio" in page_content, "Canvas Visual Pipeline Studio header not found!"
        assert "Add Source" in page_content, "'Add Source' button text not found!"
        assert "Add Transform" in page_content, "'Add Transform' button text not found!"
        
        browser.close()
        print("SUCCESS! All Playwright E2E tests passed cleanly with 0 errors!")

if __name__ == '__main__':
    test_duckdb_studio_canvas()
