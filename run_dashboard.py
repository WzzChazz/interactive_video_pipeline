#!/usr/bin/env python3
"""
run_dashboard.py
================
启动监控后台 Web 服务。

用法:
    python run_dashboard.py           # 默认端口 8765
    python run_dashboard.py --port 9000
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

def main():
    parser = argparse.ArgumentParser(description="Pipeline Dashboard Server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--reload", action="store_true", help="开发模式热重载")
    args = parser.parse_args()

    import uvicorn
    print(f"\n🎬  Pipeline Dashboard 启动中...")
    print(f"    地址: http://localhost:{args.port}")
    print(f"    按 Ctrl+C 停止\n")

    uvicorn.run(
        "dashboard.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )

if __name__ == "__main__":
    main()
