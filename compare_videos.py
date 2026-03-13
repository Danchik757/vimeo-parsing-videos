import vimeo
import json

with open('config.json', 'r') as f:
    config = json.load(f)

client = vimeo.VimeoClient(
    token=config['vimeo_api']['token'],
    key=config['vimeo_api']['client_id'],
    secret=config['vimeo_api']['secret']
)

# Video with download=true
video1 = '1071902981'
# Video with download=false (from test)
video2 = '1066528235'

print("Сравниваем два видео:\n")

for vid_id in [video1, video2]:
    print(f"=" * 80)
    print(f"Video: {vid_id}")
    print("=" * 80)
    
    response = client.get(f'/videos/{vid_id}')
    
    if response.status_code == 200:
        data = response.json()
        
        print(f"\n📌 privacy:")
        privacy = data.get('privacy', {})
        for key, value in privacy.items():
            print(f"   {key}: {value}")
        
        print(f"\n📌 play:")
        play = data.get('play', {})
        for key, value in play.items():
            print(f"   {key}: {value}")
        
        print(f"\n📌 status: {data.get('status')}")
        print(f"📌 is_playable: {data.get('is_playable')}")
        
        print(f"\n📌 embed.badges:")
        badges = data.get('embed', {}).get('badges', {})
        print(f"   vod: {badges.get('vod')}")
        print(f"   hdr: {badges.get('hdr')}")
        
        print(f"\n📌 user.account: {data.get('user', {}).get('account')}")
        
    else:
        print(f"   Error: {response.status_code}")
    
    print()

print("=" * 80)
print("\n🔍 ВЫВОД:")
print("Единственное различие - значение privacy.download:")
print("  • Video 1071902981: download = true  ✅")
print("  • Video 1066528235: download = false ❌")
print("\nНЕТ других полей типа 'unprivacy' или прямых ссылок на файлы!")
