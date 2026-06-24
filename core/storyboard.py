"""
分镜板审阅页：生图后生成一个自包含 HTML，浏览器一看就知道要不要继续渲染。
把"渲染完整片才能审"(12分钟+烧额度) → "生图后几十秒先审分镜板"。
每个分镜显示：缩略图 + 谁说的台词 + 静/动镜 + 完整生图提示词 + 预估时长 + "要重生"勾选。
"""
import base64
import html
import subprocess
import tempfile
from pathlib import Path


def _thumb_data_uri(image_path: str, width: int = 480) -> str:
    """图片缩成缩略图并内嵌为 base64，页面自包含、可移植、可分享。"""
    try:
        out = Path(tempfile.gettempdir()) / f"sb_{Path(image_path).stem}.jpg"
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(image_path), "-vf", f"scale={width}:-2", "-q:v", "4", str(out)],
            capture_output=True, timeout=20,
        )
        if out.exists() and out.stat().st_size > 0:
            return "data:image/jpeg;base64," + base64.b64encode(out.read_bytes()).decode()
    except Exception:
        pass
    return ""


def _build_image_prompt(vp) -> str:
    """复刻 image_gen 的提示词拼接逻辑，显示真正喂给 Seedream 的完整提示词。"""
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


def _est_dur(dialogue: str) -> float:
    """按台词字数粗估单镜时长（中文约 4 字/秒 + 1 秒留白；空台词=纯空镜 3 秒）。"""
    n = len((dialogue or "").strip())
    if n == 0:
        return 3.0
    return max(3.0, round(n / 4.0 + 1.0, 1))


def generate_storyboard_html(script: dict, image_manifest: dict, output_path) -> Path:
    """script: 剧本dict(含scenes/episode_title等)；image_manifest: {scene_index: 图片路径}。"""
    output_path = Path(output_path)
    scenes = script.get("scenes", [])
    title = script.get("episode_title", "")
    summary = script.get("episode_summary", "")
    cover = script.get("cover_teaser", "")
    nb = script.get("next_branches", {}) or {}

    total_dur = 0.0
    cards = []
    for sc in scenes:
        idx = sc.get("scene_index")
        spk = sc.get("speaker", "") or "旁白"
        dia = sc.get("dialogue", "") or "（纯画面，无台词）"
        emo = sc.get("emotion", "")
        is_motion = bool(sc.get("needs_motion"))
        motion = "🎬 动作镜 · Seedance" if is_motion else "🖼️ 静镜 · Ken Burns"
        vp = sc.get("visual_prompt", {}) or {}
        shot = vp.get("type", "") if isinstance(vp, dict) else ""
        env = vp.get("environment", "") if isinstance(vp, dict) else ""
        full_prompt = _build_image_prompt(vp)
        dur = _est_dur(sc.get("dialogue", ""))
        total_dur += dur
        img = image_manifest.get(idx) or image_manifest.get(str(idx)) or ""
        thumb = _thumb_data_uri(img) if img and Path(img).exists() else ""
        img_html = f'<img src="{thumb}">' if thumb else '<div class="noimg">（未生成图）</div>'
        cards.append(f"""
        <div class="card">
          <div class="thumb">{img_html}<span class="num">{idx}</span></div>
          <div class="info">
            <div class="topbar">
              <span class="motion">{html.escape(motion)}</span>
              <span class="dur">⏱ ~{dur:.1f}s</span>
              <label class="regenlbl"><input type="checkbox" class="regen" data-idx="{idx}"> 这镜要重生</label>
            </div>
            <div class="line"><b>{html.escape(spk)}</b> <span class="emo">（{html.escape(emo)}）</span>　<span class="shot">{html.escape(shot)}</span></div>
            <div class="dia">{html.escape(dia)}</div>
            <div class="env">📍 {html.escape(env)}</div>
            <details class="pp"><summary>▸ 完整生图提示词</summary><div class="ppbody">{html.escape(full_prompt)}<br><span class="ref">＋ 定妆照参考图（锁脸）</span></div></details>
          </div>
        </div>""")

    page = f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>分镜板审阅 · {html.escape(title)}</title>
<style>
  body{{font-family:-apple-system,system-ui,"PingFang SC",sans-serif;background:#faf7f2;margin:0;padding:24px 24px 90px;color:#3a3a3a}}
  .head{{max-width:940px;margin:0 auto 20px}}
  .head h1{{margin:0 0 8px;font-size:22px}}
  .head .sum{{color:#777;font-size:14px;line-height:1.7}}
  .head .meta{{margin-top:10px}}
  .head .cover{{display:inline-block;background:#ffe6f0;color:#c2548a;padding:4px 12px;border-radius:12px;font-size:13px}}
  .head .total{{display:inline-block;margin-left:8px;background:#eef3e6;color:#6f8a45;padding:4px 12px;border-radius:12px;font-size:13px}}
  .grid{{max-width:940px;margin:0 auto;display:flex;flex-direction:column;gap:14px}}
  .card{{display:flex;gap:18px;background:#fff;border-radius:14px;box-shadow:0 2px 12px rgba(0,0,0,.05);overflow:hidden}}
  .thumb{{position:relative;flex:0 0 250px;background:#eee}}
  .thumb img{{width:250px;display:block}}
  .thumb .num{{position:absolute;top:8px;left:8px;background:rgba(0,0,0,.6);color:#fff;width:28px;height:28px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:14px}}
  .noimg{{width:250px;height:444px;display:flex;align-items:center;justify-content:center;color:#bbb}}
  .info{{flex:1;padding:14px 18px 14px 0;min-width:0}}
  .topbar{{display:flex;align-items:center;gap:12px;margin-bottom:8px;flex-wrap:wrap}}
  .motion{{font-size:12px;color:#8a9a72;font-weight:600}}
  .dur{{font-size:12px;color:#b58a4a;background:#fdf3e3;padding:2px 8px;border-radius:8px}}
  .regenlbl{{font-size:13px;color:#c2548a;cursor:pointer;margin-left:auto}}
  .line{{font-size:15px;margin-bottom:2px}} .emo{{color:#aaa;font-size:13px}} .shot{{color:#9aa;font-size:12px}}
  .dia{{font-size:19px;font-weight:600;color:#222;margin:6px 0 10px;line-height:1.5}}
  .env{{font-size:13px;color:#999;line-height:1.5;margin-bottom:8px}}
  .pp summary{{font-size:12px;color:#88a;cursor:pointer;outline:none}}
  .ppbody{{font-size:12px;color:#888;background:#f7f7fb;border-radius:8px;padding:8px 10px;margin-top:6px;line-height:1.6;word-break:break-word}}
  .ref{{color:#c2548a}}
  .regenbar{{position:fixed;left:0;right:0;bottom:0;background:#fff;border-top:1px solid #eee;padding:12px 24px;display:flex;gap:12px;align-items:center;justify-content:center;box-shadow:0 -2px 12px rgba(0,0,0,.05)}}
  .regenbar button{{background:#c2548a;color:#fff;border:none;padding:9px 18px;border-radius:10px;font-size:14px;cursor:pointer}}
  .regenbar input{{flex:0 1 420px;padding:9px 12px;border:1px solid #eecbe0;border-radius:10px;font-size:14px}}
  .foot{{max-width:940px;margin:18px auto;color:#888;font-size:14px;line-height:1.7}}
</style></head><body>
  <div class="head">
    <h1>📋 分镜板审阅 · {html.escape(title)}</h1>
    <div class="sum">{html.escape(summary)}</div>
    <div class="meta"><span class="cover">封面钩子：{html.escape(cover)}</span><span class="total">预估总时长 ~{total_dur:.0f}s · {len(scenes)}个分镜</span></div>
  </div>
  <div class="grid">{''.join(cards)}</div>
  <div class="foot">🔮 明日预告：{html.escape(nb.get('branch_a_teaser',''))}　／　{html.escape(nb.get('branch_b_teaser',''))}</div>
  <div class="regenbar">
    <button onclick="collectRegen()">📋 生成"要重生"清单</button>
    <input id="regenout" readonly placeholder="勾选要重生的镜头 → 点左边按钮 → 复制结果发我">
  </div>
  <script>
    function collectRegen(){{
      const ids=[...document.querySelectorAll('.regen:checked')].map(c=>c.dataset.idx);
      const out=document.getElementById('regenout');
      out.value = ids.length ? ('重生镜头: '+ids.join(', ')) : '（没勾选任何镜头）';
      out.select();
    }}
  </script>
</body></html>"""

    output_path.write_text(page, encoding="utf-8")
    return output_path
