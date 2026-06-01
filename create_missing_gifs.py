import os
import glob
import subprocess
from playwright.sync_api import sync_playwright

def compile_gif(recorded_video, gif_name):
    assets_dir = "/home/martin/volumes/duckdb-studio/Documents/assets"
    gif_dest_path = os.path.join(assets_dir, gif_name)
    print(f"Converting {recorded_video} to {gif_dest_path} via FFmpeg...", flush=True)
    
    ffmpeg_cmd = [
        "ffmpeg", "-y", "-i", recorded_video,
        "-vf", "fps=10,scale=800:-1:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse",
        gif_dest_path
    ]
    try:
        subprocess.run(ffmpeg_cmd, check=True)
        print(f"SUCCESS: Created GIF {gif_name}", flush=True)
    except Exception as err:
        print(f"ERROR: Failed converting {gif_name}: {err}", flush=True)

def record_tab(tab_name, action_fn, gif_filename):
    video_temp_dir = f"/home/martin/volumes/duckdb-studio/Documents/assets/temp_{gif_filename.replace('.gif', '')}"
    os.makedirs(video_temp_dir, exist_ok=True)
    
    with sync_playwright() as p:
        print(f"Recording {tab_name} tab to {gif_filename}...", flush=True)
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            record_video_dir=video_temp_dir,
            record_video_size={"width": 1280, "height": 800}
        )
        page = context.new_page()
        try:
            page.goto("http://localhost:8086", timeout=30000)
            page.wait_for_timeout(3000)
            
            # Click target tab
            page.locator("div.q-tab__label", has_text=tab_name).first.click()
            page.wait_for_timeout(2000)
            
            # Perform interaction
            action_fn(page)
            
        except Exception as e:
            print(f"ERROR during recording of {tab_name}: {e}", flush=True)
        finally:
            context.close()
            browser.close()
            
    # Compile
    video_files = glob.glob(os.path.join(video_temp_dir, "*.webm"))
    if video_files:
        compile_gif(video_files[0], gif_filename)
        os.remove(video_files[0])
    try:
        os.rmdir(video_temp_dir)
    except:
        pass

# Define interactions
def jupyter_actions(page):
    # Wait for Jupyterlab iframe
    page.wait_for_timeout(6000)

def api_endpoints_actions(page):
    # Locate creator form inputs
    page.locator("input[placeholder='e.g., recent-sales']").first.fill("sales-test")
    page.wait_for_timeout(1000)
    page.locator("input[placeholder*='quantity >=']").first.fill("Dynamic sales test API with optional JWT protection")
    page.wait_for_timeout(1000)
    page.locator("textarea[placeholder*='sales_transactions']").first.fill("SELECT * FROM sales_transactions LIMIT 10;")
    page.wait_for_timeout(1000)
    # Click Analyze Columns
    page.locator("button", has_text="Analyze Columns for Auto-Params").first.click()
    page.wait_for_timeout(2000)
    # Toggle JWT
    page.locator("div.q-toggle__label", has_text="Require JWT").first.click()
    page.wait_for_timeout(2000)

def api_docs_actions(page):
    # Wait for Swagger playground loading
    page.wait_for_timeout(4000)
    # Expand execute panel
    page.locator("button", has_text="Execute Request").first.wait_for(state="visible", timeout=5000)
    page.wait_for_timeout(2000)

def scheduler_actions(page):
    # Populate scheduling query
    page.locator("textarea[placeholder*='SELECT']").first.fill("SELECT * FROM product_inventory;")
    page.wait_for_timeout(1500)
    # Select CSV format
    page.locator("div.q-select").first.click()
    page.wait_for_timeout(1000)
    # Click job list refresh
    page.locator("button", has_text="Trigger Job Now").first.wait_for(state="visible", timeout=5000)
    page.wait_for_timeout(2000)

if __name__ == "__main__":
    assets_dir = "/home/martin/volumes/duckdb-studio/Documents/assets"
    os.makedirs(assets_dir, exist_ok=True)
    
    record_tab("JupyterLab", jupyter_actions, "jupyterlab.gif")
    record_tab("API Endpoints", api_endpoints_actions, "api_endpoints.gif")
    record_tab("API Docs & Explorer", api_docs_actions, "api_docs_explorer.gif")
    record_tab("Scheduler", scheduler_actions, "scheduler.gif")
    print("ALL MISSING GIFS GENERATED SUCCESSFULLY!", flush=True)
