import time
from DrissionPage import ChromiumPage, ChromiumOptions
from config.settings import BROWSER_USER_DATA_DIR

opts = ChromiumOptions()
opts.set_user_data_path(BROWSER_USER_DATA_DIR)
opts.headless(True)
opts.set_argument("--disable-blink-features=AutomationControlled")
opts.set_argument("--no-sandbox")

page = ChromiumPage(addr_or_opts=opts)
print("Navigating to Creator Center...")
page.get("https://creator.douyin.com/creator-micro/content/manage")
time.sleep(5)

print("--- Video List Analytics ---")
# 找到所有视频卡片
cards = page.eles(".card-wrap") or page.eles(".content-card") or page.eles("[data-e2e='video-card']")
if not cards:
    # 回退到全局搜索 播放
    print("Could not find video cards by standard class. Using text:播放 search.")
    views_els = page.eles("text:播放")
    for i, el in enumerate(views_els):
        try:
            parent = el.parent()
            print(f"[{i}] {parent.text.replace(chr(10), ' ')}")
        except Exception:
            pass
else:
    for i, card in enumerate(cards[:5]):
        print(f"Video {i+1}:")
        print(card.text.replace('\n', ' | ')[:200])

print("\n--- Trying to find Audience Profile ---")
# 尝试看看有没有“数据”或“画像”的入口
data_btns = page.eles("text:数据")
if data_btns:
    try:
        data_btns[0].click()
        time.sleep(4)
        print("Clicked '数据', checking page text for '画像' or '年龄'...")
        print(page.html[:1000]) # just to see if it navigated
        
        # 找年龄或性别相关文本
        age_els = page.eles("text:年龄")
        if age_els:
            for el in age_els:
                print("Age element found:", el.parent().text.replace('\n', ' '))
                
        gender_els = page.eles("text:性别")
        if gender_els:
            for el in gender_els:
                print("Gender element found:", el.parent().text.replace('\n', ' '))
                
    except Exception as e:
        print("Error clicking data:", e)

page.quit()
