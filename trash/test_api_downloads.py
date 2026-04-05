import vimeo
import json

# Load config
with open('config.json', 'r') as f:
    config = json.load(f)

# Initialize client
client = vimeo.VimeoClient(
    token=config['vimeo_api']['token'],
    key=config['vimeo_api']['client_id'],
    secret=config['vimeo_api']['secret']
)

# Test video with download=true
video_id = '1071902981'

print(f"Testing video: {video_id}")
print("=" * 80)

# Basic request
print("\n1. Basic API request:")
response = client.get(f'/videos/{video_id}')
data = response.json()
print(f"   privacy.download: {data.get('privacy', {}).get('download')}")

# Request with fields parameter (try to get download links)
print("\n2. Request with ?fields=download:")
response2 = client.get(f'/videos/{video_id}?fields=download')
if response2.status_code == 200:
    data2 = response2.json()
    print(f"   Response: {json.dumps(data2, indent=2)}")
else:
    print(f"   Status: {response2.status_code}")

# Request with files field
print("\n3. Request with ?fields=files:")
response3 = client.get(f'/videos/{video_id}?fields=files')
if response3.status_code == 200:
    data3 = response3.json()
    print(f"   Response: {json.dumps(data3, indent=2)}")
else:
    print(f"   Status: {response3.status_code}")

# Check if there's a download endpoint
print("\n4. Check /videos/{id}/files endpoint:")
response4 = client.get(f'/videos/{video_id}/files')
print(f"   Status: {response4.status_code}")
if response4.status_code == 200:
    data4 = response4.json()
    print(f"   Response: {json.dumps(data4, indent=2)[:500]}...")

print("\n" + "=" * 80)
print("Testing complete!")
