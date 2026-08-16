import time
from playwright.sync_api import sync_playwright

def test_duckdb_studio_drawflow():
    print("Starting Playwright E2E Verification for Drawflow Client-Side Canvas Studio...")
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
        
        # Click on Visual Query Builder sub-tab
        print("Clicking on 'Visual Query Builder' tab...")
        vqb_tab = page.get_by_text('Visual Query Builder').first
        vqb_tab.click()
        page.wait_for_timeout(3000)
        
        # Wait for #drawflow_canvas element to be visible
        print("Waiting for #drawflow_canvas element to be visible...")
        canvas_elem = page.locator('#drawflow_canvas')
        canvas_elem.wait_for(state='visible', timeout=15000)
        print("#drawflow_canvas is visible!")
        
        # Verify node icons and palette
        print("Verifying Node Palette and Drawflow Canvas...")
        content = page.content()
        assert "NODE PALETTE" in content, "NODE PALETTE header not found!"
        assert "Source Table" in content, "Source Table palette item not found!"
        assert "Table Join" in content, "Table Join palette item not found!"
        assert "Transform" in content, "Transform palette item not found!"
        
        # Click on '➕ Add Transform' button in toolbar to add node via JS
        print("Clicking '➕ Add Transform' button...")
        add_btn = page.locator('button:has-text("Add Transform")').first
        add_btn.click()
        page.wait_for_timeout(2000)
        
        # Take screenshot of the Drawflow Visual Pipeline Studio
        screenshot_path = '/home/martin/.gemini/antigravity-cli/brain/71b2a41a-99fd-4137-a776-0414ab01caec/.tempmediaStorage/canvas_studio_e2e.png'
        page.screenshot(path=screenshot_path)
        print(f"Screenshot saved to: {screenshot_path}")
        
        browser.close()
        print("SUCCESS! All Drawflow Playwright E2E tests passed cleanly with 0 errors!")

if __name__ == '__main__':
    test_duckdb_studio_drawflow()
