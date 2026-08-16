import time
from playwright.sync_api import sync_playwright

def test_duckdb_studio_canvas():
    print("Starting Playwright E2E Verification for Canvas Studio...")
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
        vqb_tab = page.get_by_role('tab', name='Visual Query Builder')
        vqb_tab.click()
        
        # Wait for NODE PALETTE to become visible on page
        print("Waiting for NODE PALETTE element to be visible...")
        palette_lbl = page.locator('text=NODE PALETTE').first
        palette_lbl.wait_for(state='visible', timeout=10000)
        print("NODE PALETTE is visible!")
        
        # Verify node icons and palette
        print("Verifying Node Palette and Node Icons...")
        content = page.content()
        assert "NODE PALETTE" in content, "NODE PALETTE header not found!"
        assert "Source Table" in content, "Source Table palette item not found!"
        assert "Table Join" in content, "Table Join palette item not found!"
        assert "Transform" in content, "Transform palette item not found!"
        
        # Click on 'Transform & Function' palette card to add node
        print("Clicking 'Transform & Function' in palette dock...")
        transform_palette = page.locator('div:has-text("Transform & Function")').last
        transform_palette.click()
        page.wait_for_timeout(2000)
        
        # Click on ⚙️ Settings Gear button on a node to open properties modal
        print("Clicking ⚙️ Gear button on node...")
        gear_btn = page.locator('button i:has-text("settings")').first
        gear_btn.click()
        page.wait_for_timeout(2000)
        
        # Verify Properties Modal is open
        modal_content = page.content()
        assert "Configure" in modal_content, "Node properties modal header not found!"
        print("Successfully opened Node Properties Modal!")
        
        # Take screenshot of the open Node Properties Modal & Canvas
        screenshot_path = '/home/martin/.gemini/antigravity-cli/brain/71b2a41a-99fd-4137-a776-0414ab01caec/.tempmediaStorage/canvas_studio_e2e.png'
        page.screenshot(path=screenshot_path)
        print(f"Screenshot saved to: {screenshot_path}")
        
        # Close modal using Escape key
        print("Closing Node Properties Modal via Escape key...")
        page.keyboard.press('Escape')
        page.wait_for_timeout(1000)
        
        browser.close()
        print("SUCCESS! All Playwright E2E tests passed cleanly with 0 errors!")

if __name__ == '__main__':
    test_duckdb_studio_canvas()
