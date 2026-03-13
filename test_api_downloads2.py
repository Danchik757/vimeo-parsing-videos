import vimeo
import json

with open('config.json', 'r') as f:
    config = json.load(f)

client = vimeo.VimeoClient(
    token=config['vimeo_api']['token'],
    key=config['vimeo_api']['client_id'],
    secret=config['vimeo_api']['secret']
)

video_id = '1071902981'

print("Проверяем различные endpoints для download links:\n")

# 1. Metadata connections
print("1. Checking metadata.connections:")
response = client.get(f'/videos/{video_id}')
data = response.json()
connections = data.get('metadata', {}).get('connections', {})
for key, value in connections.items():
    if 'download' in key.lower() or 'file' in key.lower():
        print(f"   {key}: {value}")

# 2. Check all top-level keys
print("\n2. All top-level keys in response:")
all_keys = list(data.keys())
print(f"   {', '.join(all_keys)}")

# 3. Look for download/file related keys
print("\n3. Keys containing 'download', 'file', 'link':")
for key in all_keys:
    if any(word in key.lower() for word in ['download', 'file', 'link']):
        print(f"   {key}: {data[key]}")

# 4. Try to get user's videos to see if owner can see download links
print("\n4. Trying /me endpoint:")
me_response = client.get('/me')
if me_response.status_code == 200:
    me_data = me_response.json()
    print(f"   Current user: {me_data.get('name')}")
    print(f"   User ID: {me_data.get('uri')}")
else:
    print(f"   Status: {me_response.status_code}")

# 5. Check if there's a specific download URL in the API
print("\n5. Checking for 'player' or 'files' in embed:")
embed = data.get('embed', {})
print(f"   Embed keys: {list(embed.keys())}")

print("\n" + "=" * 80)
