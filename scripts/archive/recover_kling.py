import os, time, jwt, requests, json
from dotenv import load_dotenv

load_dotenv()
KLING_AK = os.getenv("KLING_AK", "").strip()
KLING_SK = os.getenv("KLING_SK", "").strip()

task_ids = [
    "893929865362706504",
    "893928945321001018",
    "893928760020844587",
    "893928812067950674",
    "893928713497628674",
    "893926323646124127"
]

def get_headers():
    payload = {
        "iss": KLING_AK,
        "exp": int(time.time()) + 1800,
        "nbf": int(time.time()) - 5
    }
    token = jwt.encode(payload, KLING_SK.encode("utf-8"), algorithm="HS256", headers={"alg": "HS256", "typ": "JWT"})
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

os.makedirs("storage/temp/S01E024/clips", exist_ok=True)

headers = get_headers()
for idx, tid in enumerate(task_ids):
    url = f"https://api-beijing.klingai.com/v1/videos/image2video/{tid}"
    try:
        r = requests.get(url, headers=headers)
        res = r.json()
        print(f"Task {tid}: status={res.get('data', {}).get('task_status')}")
        videos = res.get('data', {}).get('task_result', {}).get('videos', [])
        if videos and videos[0].get('url'):
            vurl = videos[0]['url']
            print(f"Downloading from {vurl[:30]}...")
            vr = requests.get(vurl)
            with open(f"storage/temp/S01E024/clips/task_{tid}.mp4", "wb") as f:
                f.write(vr.content)
            print(f"Saved task_{tid}.mp4")
            
            # Save raw json to check if prompt is inside
            with open(f"storage/temp/S01E024/clips/task_{tid}.json", "w") as f:
                json.dump(res, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Error fetching task {tid}: {e}")
