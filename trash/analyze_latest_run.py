import re

with open('output/logs/download.log', 'r') as f:
    lines = f.readlines()

# Find start of latest run
start_line = 0
for i, line in enumerate(lines):
    if "VIMEO DOWNLOADER FINAL (SeleniumBase UC Mode)" in line:
        start_line = i

# Get only latest run
latest_run = ''.join(lines[start_line:])

# Find all video IDs that were processed
processed = []
for match in re.finditer(r'\[(\d+)/50\] Processing video (\d+)', latest_run):
    idx, vid_id = match.groups()
    processed.append(vid_id)

print(f"=== LATEST RUN ANALYSIS ===")
print(f"Videos processed: {len(processed)} / 50")
print(f"Video IDs: {', '.join(processed[:5])}... (showing first 5)")

# Count outcomes
downloadable_count = 0
not_downloadable_count = 0
paid_count = 0
button_not_found_count = 0
success_count = 0

for vid_id in processed:
    if f"✓ Video {vid_id} is downloadable (privacy.download = true)" in latest_run:
        downloadable_count += 1
        if f"Download button not found for {vid_id}" in latest_run:
            button_not_found_count += 1
        elif f"✓ Successfully downloaded {vid_id}" in latest_run:
            success_count += 1
    elif f"Video {vid_id} is not downloadable (privacy.download = false)" in latest_run:
        not_downloadable_count += 1
    elif f"Video {vid_id} is On Demand (paid)" in latest_run:
        paid_count += 1

print(f"\n=== API CHECKS ===")
print(f"✓ Downloadable (privacy.download = true): {downloadable_count}")
print(f"⏭  Not downloadable (privacy.download = false): {not_downloadable_count}")
print(f"💰 Paid videos (On Demand): {paid_count}")

print(f"\n=== DOWNLOAD RESULTS ===")
print(f"✅ Successfully downloaded: {success_count}")
print(f"❌ Failed (button not found): {button_not_found_count}")

print(f"\n=== SUMMARY ===")
print(f"Total processed: {len(processed)}")
print(f"Could attempt: {downloadable_count}")
print(f"Successful: {success_count}")
print(f"Failed: {button_not_found_count}")
print(f"Skipped: {not_downloadable_count + paid_count}")

# Show which videos had privacy.download = true
print(f"\n=== VIDEOS WITH PRIVACY.DOWNLOAD = TRUE ===")
for vid_id in processed:
    if f"✓ Video {vid_id} is downloadable (privacy.download = true)" in latest_run:
        print(f"  - {vid_id}")
