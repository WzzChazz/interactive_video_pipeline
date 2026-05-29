from DrissionPage import ChromiumPage, ChromiumOptions
from config.settings import BROWSER_USER_DATA_DIR

def login_matrix():
    print(f"正在加载浏览器持久化配置: {BROWSER_USER_DATA_DIR}")
    opts = ChromiumOptions()
    # 核心：使用共享的用户数据目录，这样所有爬虫和发布器都会继承这里的 Cookie
    opts.set_user_data_path(BROWSER_USER_DATA_DIR)
    # 必须关闭无头模式才能让用户手动操作
    opts.headless(False)

    page = ChromiumPage(addr_or_opts=opts)
    
    print("\n" + "="*50)
    print("🌍 出海矩阵一键登录工具")
    print("="*50)
    
    import os
    import time
    
    # 清理遗留的信号文件
    if os.path.exists("tiktok_done.txt"): os.remove("tiktok_done.txt")
    if os.path.exists("x_done.txt"): os.remove("x_done.txt")
    if os.path.exists("kuaishou_done.txt"): os.remove("kuaishou_done.txt")
    
    # 1. 登录 TikTok
    print("\n[1/3] 正在打开 TikTok...")
    page.get("https://www.tiktok.com/login")
    print("👉 请在弹出的窗口中登录你的 TikTok 账号。")
    print("👉 后台进程正在等待... 等你登录完成后，请在聊天框回复我，我会发送继续指令。")
    while not os.path.exists("tiktok_done.txt"):
        time.sleep(1)
    os.remove("tiktok_done.txt")
    
    # 2. 登录 X (Twitter)
    print("\n[2/3] 正在打开 X (Twitter)...")
    page.get("https://x.com/i/flow/login")
    print("👉 请在弹出的窗口中登录你的 X 账号。")
    print("👉 等你登录完成后，请在聊天框回复我。")
    while not os.path.exists("x_done.txt"):
        time.sleep(1)
    os.remove("x_done.txt")

    # 3. 登录快手创作者平台
    print("\n[3/3] 正在打开快手创作者平台...")
    page.get("https://cp.kuaishou.com/article/publish/video")
    print("👉 请在弹出的窗口中登录你的快手账号（可用手机扫码）。")
    print("👉 登录并看到上传页面后，请在聊天框回复我。")
    while not os.path.exists("kuaishou_done.txt"):
        time.sleep(1)
    os.remove("kuaishou_done.txt")
    
    print("\n正在保存所有平台的登录状态并关闭浏览器...")
    page.quit()
    print("✅ 状态保存完毕！TikTok、X 和快手的 Cookie 已永久写入。现在发布器畅通无阻了！")

if __name__ == "__main__":
    login_matrix()
