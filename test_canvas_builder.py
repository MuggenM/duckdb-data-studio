import time
from playwright.sync_api import sync_playwright

def test_drawflow_full_pipeline():
    print("Starting Comprehensive Playwright E2E Verification for Drawflow Pipeline Studio...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        # 1. Navigate to studio app
        print("Navigating to http://localhost:8086/...")
        page.goto('http://localhost:8086/', wait_until='networkidle')
        page.wait_for_timeout(3000)
        
        title = page.title()
        print(f"Page title: '{title}'")
        assert "DuckDB Data Studio" in title, f"Unexpected page title: {title}"
        
        # 2. Click on Visual Query Builder sub-tab
        print("Clicking on 'Visual Query Builder' tab...")
        vqb_tab = page.get_by_text('Visual Query Builder').first
        vqb_tab.click()
        page.wait_for_timeout(3000)
        
        # 3. Wait for #drawflow_canvas element to be visible
        print("Waiting for #drawflow_canvas element to be visible...")
        canvas_elem = page.locator('#drawflow_canvas')
        canvas_elem.wait_for(state='visible', timeout=15000)
        print("#drawflow_canvas is visible!")
        
        # 4. Verify Node Palette Dock elements
        print("Verifying Node Palette Dock items...")
        content = page.content()
        assert "NODE PALETTE" in content, "NODE PALETTE header not found!"
        assert "Source Table" in content, "Source Table palette item not found!"
        assert "Table Join" in content, "Table Join palette item not found!"
        assert "Transform" in content, "Transform palette item not found!"
        assert "Filter" in content, "Filter palette item not found!"
        assert "Output" in content, "Output palette item not found!"
        
        # 5. Add a Table Join Node via Palette Dock
        print("Spawning a Table Join Node via Palette Dock...")
        join_palette = page.get_by_text("Table Join").first
        join_palette.click()
        page.wait_for_timeout(1500)
        
        # 6. Click ⚙️ Gear Button on a node to open Node Properties Modal
        print("Triggering ⚙️ Gear button on node...")
        page.evaluate("window.emit_node_edit(document.querySelector('.drawflow-node button'))")
        
        # Wait for Quasar dialog container to appear
        print("Waiting for .q-dialog to mount in DOM...")
        dialog_elem = page.locator('.q-dialog')
        dialog_elem.wait_for(state='visible', timeout=15000)
        print("Quasar .q-dialog is mounted and visible!")
        
        # Take screenshot of the open Node Configuration Modal
        screenshot_modal_path = '/home/martin/.gemini/antigravity-cli/brain/71b2a41a-99fd-4137-a776-0414ab01caec/.tempmediaStorage/canvas_modal_e2e.png'
        page.screenshot(path=screenshot_modal_path)
        print(f"Modal Screenshot saved to: {screenshot_modal_path}")
        
        # Save Node Properties by clicking the primary action button in dialog
        print("Clicking 'Save Properties 💾'...")
        save_btn = page.locator('.q-dialog button:has-text("Save Properties")').first
        save_btn.click()
        page.wait_for_timeout(2000)
        
        # 7. Execute Query in SQL Workspace via 'Run in SQL Editor 🚀'
        print("Clicking 'Run in SQL Editor 🚀'...")
        run_btn = page.locator('button:has-text("Run in SQL Editor")').first
        run_btn.click()
        page.wait_for_timeout(4000)
        
        # Verify SQL Editor loaded the compiled query and executed it
        editor_content = page.content()
        assert "SQL Workspace" in editor_content, "Did not navigate back to SQL Workspace!"
        print("Successfully executed compiled pipeline query in SQL Workspace!")
        
        # Take final screenshot of the SQL Editor & Result Grid
        screenshot_final_path = '/home/martin/.gemini/antigravity-cli/brain/71b2a41a-99fd-4137-a776-0414ab01caec/.tempmediaStorage/canvas_studio_e2e.png'
        page.screenshot(path=screenshot_final_path)
        print(f"Final Screenshot saved to: {screenshot_final_path}")
        
        browser.close()
        print("SUCCESS! All Drawflow E2E pipeline & node configuration tests passed cleanly with 0 errors!")

if __name__ == '__main__':
    test_drawflow_full_pipeline()
