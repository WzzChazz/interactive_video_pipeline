from DrissionPage import ChromiumPage, ChromiumOptions
from config.settings import BROWSER_USER_DATA_DIR

def login():
    print(f"正在加载浏览器配置: {BROWSER_USER_DATA_DIR}")
    opts = ChromiumOptions()
    # 使用和项目中完全一样的用户数据目录，这样登录状态就能持久化给自动化脚本用
    opts.set_user_data_path(BROWSER_USER_DATA_DIR)
    # 必须关闭无头模式才能看到二维码
    opts.headless(False)

    print("正在打开抖音创作者中心...")
    page = ChromiumPage(addr_or_opts=opts)
    page.get("https://creator.douyin.com/")
    
    print("\n" + "="*50)
    print("👉 请在弹出的浏览器窗口中，使用抖音 APP 扫码登录。")
    print("👉 登录成功并看到创作者后台后，请在终端里按下回车键 (Enter) 结束程序。")
    print("="*50 + "\n")
    
    input("登录完成后，请按回车键退出并保存状态...")
    print("正在保存登录状态并关闭浏览器...")
    page.quit()
    print("✅ 登录状态已保存！现在自动化脚本可以无需扫码直接运行了。")

if __name__ == "__main__":
    login()
