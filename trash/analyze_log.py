import re

with open('output/logs/download.log', 'r') as f:
    log_content = f.read()

# Find all video processing blocks
processed_videos = re.findall(r'\[(\d+)/50\] Processing video (\d+)', log_content)
print(f"Total videos attempted: {len(processed_videos)}")

# Count different outcomes
downloadable_true = len(re.findall(r'✓ Video \d+ is downloadable \(privacy\.download = true\)', log_content))
downloadable_false = len(re.findall(r'Video \d+ is not downloadable \(privacy\.download = false\)', log_content))
paid_videos = len(re.findall(r'Video \d+ is On Demand \(paid\)', log_content))
download_button_not_found = len(re.findall(r'ERROR - Download button not found for \d+', log_content))
successfully_downloaded = len(re.findall(r'✓ Successfully downloaded \d+', log_content))

print(f"\nAPI Results:")
print(f"  privacy.download = true: {downloadable_true}")
print(f"  privacy.download = false: {downloadable_false}")
print(f"  On Demand (paid): {paid_videos}")

print(f"\nDownload Attempts:")
print(f"  Download button not found: {download_button_not_found}")
print(f"  Successfully downloaded: {successfully_downloaded}")

print(f"\nSummary:")
print(f"  Attempted downloads: {downloadable_true}")
print(f"  Failed (button not found): {download_button_not_found}")
print(f"  Successful: {successfully_downloaded}")
print(f"  Skipped (not downloadable): {downloadable_false}")
print(f"  Skipped (paid): {paid_videos}")
