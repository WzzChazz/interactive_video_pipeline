from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional, Any, Dict
import httpx
from datetime import datetime

app = FastAPI(title="业务后端 - 报告回调与查询服务")

# ==========================================
# 1. 存储结构定义 (这里用内存 Dict 模拟 DB)
# ==========================================
"""
实际项目中建议在 MySQL 创建如下表：
CREATE TABLE user_reports (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id VARCHAR(64) COMMENT '业务用户ID',
    report_id VARCHAR(64) UNIQUE NOT NULL COMMENT '报告全局唯一ID',
    status VARCHAR(20) DEFAULT 'pending' COMMENT '状态: pending/processing/succeeded/failed',
    report_data JSON COMMENT '成功时的报告数据',
    error_info JSON COMMENT '失败时的错误信息',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);
"""
FAKE_DB = {}

def update_report_in_db(report_id: str, status: str, data: dict = None, error: dict = None):
    """更新本地数据库，注意需做幂等处理"""
    if report_id not in FAKE_DB:
        # 如果是回调比提交先到（极小概率），或者本地数据丢失，做个防坑初始化
        FAKE_DB[report_id] = {"report_id": report_id, "created_at": datetime.now()}
        
    # 幂等：如果是终态，无需重复更新（应对 AI 侧重复回调）
    if FAKE_DB[report_id].get("status") in ("succeeded", "failed"):
        return

    FAKE_DB[report_id]["status"] = status
    FAKE_DB[report_id]["updated_at"] = datetime.now()
    if data:
        FAKE_DB[report_id]["report_data"] = data
    if error:
        FAKE_DB[report_id]["error_info"] = error

def get_report_from_db(report_id: str):
    """从数据库获取报告"""
    return FAKE_DB.get(report_id)


# ==========================================
# 2. 接口 3: 回调接口 (给 AI 服务 POST 用)
# ==========================================
class ReportCallbackPayload(BaseModel):
    report_id: str
    status: str
    data: Optional[Dict[str, Any]] = None
    error: Optional[Dict[str, Any]] = None
    generated_at: Optional[str] = None

@app.post("/internal/reports/callback")
async def report_callback(payload: ReportCallbackPayload):
    """
    处理 AI 服务生成的报告回调
    """
    # 1. 更新本地业务数据库
    update_report_in_db(
        report_id=payload.report_id,
        status=payload.status,
        data=payload.data,
        error=payload.error
    )
    
    # 2. 返回 2xx 响应
    return {"ok": True}


# ==========================================
# 3. 接口 2: 前端查询 / 兜底轮询接口
# ==========================================
AI_SERVICE_BASE_URL = "http://replacement-report.internal:8000"

@app.get("/api/reports/{report_id}")
async def get_report(report_id: str):
    """
    业务前端轮询此接口获取报告状态。
    结合了对 AI 服务的「兜底轮询」机制。
    """
    report = get_report_from_db(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    # 如果本地已经是终态，直接返回，不调 AI 服务
    if report.get("status") in ("succeeded", "failed"):
        return report

    # 如果本地还是 pending/processing，向 AI 服务发起兜底查询
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{AI_SERVICE_BASE_URL}/api/v1/reports/{report_id}", 
                timeout=5.0
            )
            if resp.status_code == 200:
                ai_data = resp.json()
                new_status = ai_data.get("status")
                
                # 同步 AI 侧的最新状态到本地 DB
                update_report_in_db(
                    report_id=report_id,
                    status=new_status,
                    data=ai_data.get("data"),
                    error=ai_data.get("error")
                )
                return get_report_from_db(report_id)
    except Exception as e:
        # 兜底查询失败不阻断，继续返回本地的 pending 状态给前端
        print(f"[Warning] Fallback polling failed: {e}")

    return report


# ==========================================
# 4. 接口 4: 健康检查
# ==========================================
@app.get("/healthz")
async def health_check():
    """
    健康检查
    """
    return {
        "status": "ok",
        "timestamp": datetime.now().isoformat()
    }

if __name__ == "__main__":
    import uvicorn
    # 本地测试运行：python report_api_demo.py
    uvicorn.run(app, host="0.0.0.0", port=8080)
