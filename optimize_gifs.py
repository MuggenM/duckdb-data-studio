import os
import glob
import subprocess

def optimize_gif(file_path):
    temp_path = file_path + ".tmp.gif"
    print(f"Compressing and optimizing {os.path.basename(file_path)}...", flush=True)
    
    # FFmpeg command to scale to 800px width, 10 FPS, using Lanczos split palette generation
    ffmpeg_cmd = [
        "ffmpeg", "-y", "-i", file_path,
        "-vf", "fps=10,scale=800:-1:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse",
        temp_path
    ]
    try:
        orig_size = os.path.getsize(file_path)
        subprocess.run(ffmpeg_cmd, check=True)
        new_size = os.path.getsize(temp_path)
        
        # Only replace if the new file is actually smaller
        if new_size < orig_size:
            os.replace(temp_path, file_path)
            reduction = (orig_size - new_size) / (1024 * 1024)
            print(f"SUCCESS: Reduced {os.path.basename(file_path)} from {orig_size/(1024*1024):.2f}MB to {new_size/(1024*1024):.2f}MB (Saved {reduction:.2f}MB)", flush=True)
        else:
            os.remove(temp_path)
            print(f"Skipped {os.path.basename(file_path)} (No size savings)", flush=True)
    except Exception as err:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        print(f"ERROR optimizing {os.path.basename(file_path)}: {err}", flush=True)

if __name__ == "__main__":
    assets_dir = "/home/martin/volumes/duckdb-studio/Documents/assets"
    gif_files = glob.glob(os.path.join(assets_dir, "*.gif"))
    
    large_gifs = [g for g in gif_files if os.path.getsize(g) > 1 * 1024 * 1024]
    
    print(f"Found {len(large_gifs)} GIFs larger than 1MB to optimize.", flush=True)
    for g in large_gifs:
        optimize_gif(g)
    print("ALL GIF OPTIMIZATION TASKS COMPLETED!", flush=True)
