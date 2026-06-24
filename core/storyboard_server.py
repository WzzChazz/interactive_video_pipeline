"""
本地审阅服务器：浏览器里改提示词 → 点「重新生成这镜」→ 实时重出图。
这是 skill 的核心交互(对标 chengfeng 的 review_server)。
用法: serve_review(script_dict, image_manifest, port=8731)
"""
import base64
import html
import subprocess
import tempfile
from pathlib import Path


def _thumb(image_path: str, width: int = 460) -> str:
    try:
        out = Path(tempfile.gettempdir()) / f"rv_{Path(image_path).stem}.jpg"
        subprocess.run(["ffmpeg", "-y", "-i", str(image_path), "-vf", f"scale={width}:-2",
                        "-q:v", "4", str(out)], capture_output=True, timeout=20)
        if out.exists() and out.stat().st_size > 0:
            return "data:image/jpeg;base64," + base64.b64encode(out.read_bytes()).decode()
    except Exception:
        pass
    return ""


def _build_image_prompt(vp) -> str:
    if not isinstance(vp, dict):
        return str(vp)
    parts = []
    if vp.get("type"):
        parts.append(str(vp["type"]))
    char = vp.get("character")
    if isinstance(char, dict):
        parts += [str(char[k]) for k in ("identity", "appearance", "attire") if char.get(k)]
    elif char:
        parts.append(str(char))
    for k in ["pose", "action", "environment", "lighting", "style", "camera_spec", "constraints"]:
        if vp.get(k):
            parts.append(str(vp[k]))
    return ", ".join(p for p in parts if p)


def _render(script, scenes, prompts, imgs) -> str:
    title = script.get("episode_title", "")
    summary = script.get("episode_summary", "")
    cards = []
    for idx in sorted(scenes):
        sc = scenes[idx]
        spk = sc.get("speaker", "") or "旁白"
        dia = sc.get("dialogue", "") or "（纯画面）"
        motion = "🎬 动作镜" if sc.get("needs_motion") else "🖼️ 静镜"
        img = imgs.get(idx, "")
        thumb = _thumb(img) if img and Path(img).exists() else ""
        img_html = f'<img id="img{idx}" src="{thumb}">' if thumb else f'<div id="img{idx}" class="noimg">（未生成）</div>'
        cards.append(f"""
        <div class="card">
          <div class="thumb">{img_html}<span class="num">{idx}</span></div>
          <div class="info">
            <div class="line">{motion}　<b>{html.escape(spk)}</b>：{html.escape(dia)}</div>
            <label class="lbl">生图提示词（可改）：</label>
            <textarea id="p{idx}">{html.escape(prompts.get(idx, ''))}</textarea>
            <div class="btnrow">
              <button onclick="regen({idx})">🔄 重新生成这镜</button>
              <span id="st{idx}" class="st"></span>
            </div>
          </div>
        </div>""")
    return f"""<!doctype html><html lang="zh"><head><meta charset="utf-8"><title>审阅 · {html.escape(title)}</title>
<style>
 body{{font-family:-apple-system,"PingFang SC",sans-serif;background:#faf7f2;margin:0;padding:24px;color:#3a3a3a}}
 .head{{max-width:960px;margin:0 auto 18px}} .head h1{{font-size:21px;margin:0 0 6px}} .head .sum{{color:#777;font-size:14px;line-height:1.6}}
 .grid{{max-width:960px;margin:0 auto;display:flex;flex-direction:column;gap:14px}}
 .card{{display:flex;gap:18px;background:#fff;border-radius:14px;box-shadow:0 2px 12px rgba(0,0,0,.05);overflow:hidden}}
 .thumb{{position:relative;flex:0 0 250px;background:#eee}} .thumb img{{width:250px;display:block}}
 .thumb .num{{position:absolute;top:8px;left:8px;background:rgba(0,0,0,.6);color:#fff;width:28px;height:28px;border-radius:50%;display:flex;align-items:center;justify-content:center}}
 .noimg{{width:250px;height:444px;display:flex;align-items:center;justify-content:center;color:#bbb}}
 .info{{flex:1;padding:14px 18px 14px 0;min-width:0;display:flex;flex-direction:column}}
 .line{{font-size:15px;margin-bottom:10px}} .lbl{{font-size:12px;color:#999;margin-bottom:4px}}
 textarea{{width:100%;min-height:120px;box-sizing:border-box;border:1px solid #eedfe8;border-radius:8px;padding:10px;font-size:13px;line-height:1.6;color:#555;resize:vertical;font-family:inherit}}
 .btnrow{{margin-top:8px;display:flex;align-items:center;gap:12px}}
 button{{background:#c2548a;color:#fff;border:none;padding:8px 16px;border-radius:9px;font-size:14px;cursor:pointer}}
 button:disabled{{opacity:.5}} .st{{font-size:13px;color:#888}}
</style></head><body>
 <div class="head"><h1>🎨 分镜审阅(可改提示词重生)· {html.escape(title)}</h1><div class="sum">{html.escape(summary)}</div></div>
 <div class="grid">{''.join(cards)}</div>
 <div style="height:70px"></div>
 <div style="position:fixed;left:0;right:0;bottom:0;background:#fff;border-top:1px solid #eee;padding:14px;text-align:center;box-shadow:0 -2px 12px rgba(0,0,0,.06)">
   <button onclick="confirmRender()" style="background:#6f8a45;font-size:15px;padding:10px 28px">✅ 确认,开始渲染视频</button>
   <span id="cfst" style="margin-left:14px;color:#888"></span>
 </div>
 <script>
 async function confirmRender(){{
   document.getElementById('cfst').textContent='已确认,开始渲染… 可关闭本页';
   try{{ await fetch('/confirm',{{method:'POST'}}); }}catch(e){{}}
 }}</script>
 <script>
 async function regen(idx){{
   const btn=event.target, st=document.getElementById('st'+idx);
   const prompt=document.getElementById('p'+idx).value;
   btn.disabled=true; st.textContent='生成中…(约30秒)';
   try{{
     const r=await fetch('/regen',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{idx:idx,prompt:prompt}})}});
     const d=await r.json();
     if(d.ok){{ document.getElementById('img'+idx).src=d.thumb; st.textContent='✅ 已更新'; }}
     else{{ st.textContent='❌ '+d.error; }}
   }}catch(e){{ st.textContent='❌ '+e; }}
   btn.disabled=false;
 }}
 </script>
</body></html>"""


def serve_review(script: dict, image_manifest: dict, theme_key: str = "capybara_healing", port: int = 8731) -> dict:
    """启动审阅页(阻塞),用户点"确认渲染"后关闭并返回【可能已重生过的】image_manifest。"""
    import webbrowser
    from fastapi import FastAPI, Request
    from fastapi.responses import HTMLResponse, JSONResponse
    import uvicorn

    app = FastAPI()
    scenes = {sc["scene_index"]: sc for sc in script.get("scenes", [])}
    prompts = {idx: _build_image_prompt(sc.get("visual_prompt", {})) for idx, sc in scenes.items()}
    imgs = {int(k): v for k, v in image_manifest.items()}
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)

    @app.get("/", response_class=HTMLResponse)
    def home():
        return _render(script, scenes, prompts, imgs)

    @app.post("/confirm")
    async def confirm():
        server.should_exit = True
        return JSONResponse({"ok": True})

    @app.post("/regen")
    async def regen(req: Request):
        body = await req.json()
        idx = int(body["idx"])
        new_prompt = (body.get("prompt") or "").strip()
        prompts[idx] = new_prompt
        from core.image_gen import generate_single_image
        save = Path(imgs.get(idx) or f"storage/temp/review/scene_{idx:02d}.png")
        save.parent.mkdir(parents=True, exist_ok=True)
        try:
            generate_single_image(idx, new_prompt, save)  # 走 Seedream + 定妆照锁脸
            imgs[idx] = str(save)
            return JSONResponse({"ok": True, "thumb": _thumb(str(save))})
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)[:200]})

    print(f"\n>>> 审阅页: http://127.0.0.1:{port}  (改提示词重生 → 点'确认渲染'继续)\n", flush=True)
    try:
        webbrowser.open(f"http://127.0.0.1:{port}/")
    except Exception:
        pass
    server.run()  # 阻塞，直到 /confirm 把 should_exit 置 True
    return imgs
