import sqlite3
import json
import uuid
import hashlib
from datetime import datetime
from fastapi import FastAPI, HTTPException, Header, Body
from pydantic import BaseModel
from typing import List, Dict, Optional

app = FastAPI(title="ID租用系统云端API", version="1.0")

DB_PATH = "/data/app.db"

# ---------- 数据库工具 ----------
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    import os
    os.makedirs("/data", exist_ok=True)
    conn = get_db()
    c = conn.cursor()
    
    # 创建用户表（基础结构）
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    
    # 检查并添加缺失列（兼容旧表）
    c.execute("PRAGMA table_info(users)")
    existing_columns = [col[1] for col in c.fetchall()]
    
    if "username" not in existing_columns:
        c.execute("ALTER TABLE users ADD COLUMN username TEXT UNIQUE")
    if "password_hash" not in existing_columns:
        c.execute("ALTER TABLE users ADD COLUMN password_hash TEXT")
    if "disabled" not in existing_columns:
        c.execute("ALTER TABLE users ADD COLUMN disabled INTEGER DEFAULT 0")
    
    # 创建日志表
    c.execute('''CREATE TABLE IF NOT EXISTS logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT REFERENCES users(id),
        content TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    
    # 创建配置表
    c.execute('''CREATE TABLE IF NOT EXISTS config (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    
    # 插入默认配置（如果不存在）
    # 注册开关
    c.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('allow_register', '1')")
    # 身份证区域表（最小值）
    default_area = {"110000": "北京市", "110101": "北京市东城区"}
    c.execute("INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)", ("id_area_map", json.dumps(default_area)))
    # 禁区配置
    default_forbidden = {
        "normal": ["新疆维吾尔自治区", "甘肃省", "西藏自治区", "四川省彝族", "汕尾市", "江西省赣州市寻乌县"],
        "supervise": []
    }
    c.execute("INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)", ("forbidden_areas", json.dumps(default_forbidden)))
    # 价格配置（完整版，可以从原代码中复制，这里只保留最小示例，但实际运行时也会通过客户端上传更新）
    # 建议保持完整字典，但为节省篇幅，此处省略，实际部署时请保留完整。
    # 但为了功能完整，我们放一个基本结构，避免启动报错。
    default_price = {
        "price_map": {
            "正常": {
                "X": "200", "XR": "200", "XS": "200", "XSM": "200",
                "11": "300", "11P": "400", "11PM": "500",
                "12": "400", "12MN": "300", "13": "600", "12P": "600", "12PM": "600",
                "14": "800", "13P": "800", "13MN": "500", "15": "1100", "14P": "1000",
                "13PM": "1100", "15P": "1200", "14PM": "1300", "15PM": "1300",
                "16": "1200", "16P": "1300", "16PM": "1300", "17": "1000", "17P": "1000", "17PM": "1000",
                "AIR": "1000", "18": "500", "18P": "500", "18PM": "500",
                "14PLUS": "800", "15PLUS": "1100", "16PLUS": "1200", "16E": "1200"
            },
            "监": {
                "12": "200", "13": "300", "14": "300", "15": "500", "16": "500", "17": "500",
                "12P": "200", "12PM": "200", "13P": "300", "13PM": "300", "14P": "300", "14PM": "300",
                "15P": "500", "15PM": "500", "16P": "500", "16PM": "500", "17P": "500", "17PM": "500",
                "AIR": "500", "18": "500", "18P": "500", "18PM": "500",
                "14PLUS": "300", "15PLUS": "500", "16PLUS": "500", "16E": "500"
            },
            "卡": {
                "XR": "200", "XS": "200", "XSM": "200", "11": "200", "11P": "200", "11PM": "200",
                "12": "200", "12mn": "200", "13": "300", "12P": "300", "12PM": "300",
                "14": "400", "13P": "400", "13mn": "200", "15": "700", "14P": "600",
                "13PM": "700", "15P": "800", "14PM": "800", "15PM": "800",
                "16": "800", "16P": "800", "16PM": "800", "17": "500", "17P": "500", "17PM": "500",
                "AIR": "500", "18": "500", "18P": "500", "18PM": "500",
                "14PLUS": "400", "15PLUS": "700", "16PLUS": "800", "16E": "800"
            },
            "安卓": {
                "200": "200", "300": "300", "400": "400", "500": "500", "600": "600",
                "700": "700", "800": "800", "900": "900", "1000": "1000", "1200": "1200"
            }
        },
        "original_price_map": {
            # 因篇幅，此处只保留示例结构，完整数据可通过客户端修改上传。
            "正常": {
                "200": {"无监": ("300-200-100-25", 250), "有监": ("300-200-100-25", 250)}
            }
        }
    }
    c.execute("INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)", ("price_config", json.dumps(default_price)))
    
    conn.commit()
    conn.close()

# ---------- 模型 ----------
class LogData(BaseModel):
    logs: List[dict]

class ForbiddenConfig(BaseModel):
    normal: List[str]
    supervise: List[str]

class RegisterData(BaseModel):
    username: str
    password: str

class LoginData(BaseModel):
    username: str
    password: str

# ---------- 启动初始化 ----------
@app.on_event("startup")
def startup():
    init_db()

# ========== API 路由 ==========

# 用户注册
@app.post("/user/register")
def register(data: RegisterData):
    username = data.username.strip()
    password = data.password.strip()
    if not username or not password:
        raise HTTPException(status_code=400, detail="用户名和密码不能为空")
    conn = get_db()
    c = conn.cursor()
    # 检查注册开关
    c.execute("SELECT value FROM config WHERE key='allow_register'")
    row = c.fetchone()
    allow_register = int(row[0]) if row else 1
    if allow_register == 0:
        conn.close()
        raise HTTPException(status_code=403, detail="管理员已关闭注册，请联系管理员")
    # 检查用户名是否已存在
    c.execute("SELECT id FROM users WHERE username=?", (username,))
    if c.fetchone():
        conn.close()
        raise HTTPException(status_code=400, detail="用户名已存在")
    user_id = str(uuid.uuid4())
    password_hash = hashlib.sha256(password.encode()).hexdigest()
    c.execute("INSERT INTO users (id, username, password_hash) VALUES (?, ?, ?)", (user_id, username, password_hash))
    conn.commit()
    conn.close()
    return {"user_id": user_id, "username": username}

# 用户登录
@app.post("/user/login")
def login(data: LoginData):
    username = data.username.strip()
    password = data.password.strip()
    if not username or not password:
        raise HTTPException(status_code=400, detail="用户名和密码不能为空")
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT id, password_hash, disabled FROM users WHERE username=?", (username,))
    row = c.fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    if row[2] == 1:
        raise HTTPException(status_code=403, detail="该账号已被禁用，请联系管理员")
    password_hash = hashlib.sha256(password.encode()).hexdigest()
    if password_hash != row[1]:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    return {"user_id": row[0], "username": username}

# 上传日志
@app.post("/log/upload")
def upload_log(data: dict = Body(...), user_id: str = Header(...)):
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT INTO logs (user_id, content) VALUES (?, ?)", (user_id, json.dumps(data)))
    conn.commit()
    conn.close()
    return {"status": "ok"}

# 获取用户日志
@app.get("/log/list")
def list_logs(user_id: str):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT content, created_at FROM logs WHERE user_id=? ORDER BY created_at DESC", (user_id,))
    rows = c.fetchall()
    conn.close()
    result = []
    for row in rows:
        result.append({"content": json.loads(row[0]), "time": row[1]})
    return {"logs": result}

# 批量迁移日志
@app.post("/log/migrate")
def migrate_logs(data: LogData, user_id: str = Header(...)):
    conn = get_db()
    c = conn.cursor()
    count = 0
    for log in data.logs:
        c.execute("INSERT INTO logs (user_id, content) VALUES (?, ?)", (user_id, json.dumps(log)))
        count += 1
    conn.commit()
    conn.close()
    return {"count": count}

# 获取禁区配置
@app.get("/config/forbidden")
def get_forbidden():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT value FROM config WHERE key='forbidden_areas'")
    row = c.fetchone()
    conn.close()
    if row:
        return json.loads(row[0])
    return {"normal": [], "supervise": []}

# 更新禁区配置
@app.put("/config/forbidden")
def update_forbidden(data: ForbiddenConfig):
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE config SET value=?, updated_at=CURRENT_TIMESTAMP WHERE key='forbidden_areas'", (json.dumps(data.dict()),))
    conn.commit()
    conn.close()
    return {"status": "ok"}

# 获取价格配置
@app.get("/config/price")
def get_price():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT value FROM config WHERE key='price_config'")
    row = c.fetchone()
    conn.close()
    if row:
        return json.loads(row[0])
    return {}

# 更新价格配置
@app.put("/config/price")
def update_price(data: dict = Body(...)):
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE config SET value=?, updated_at=CURRENT_TIMESTAMP WHERE key='price_config'", (json.dumps(data),))
    conn.commit()
    conn.close()
    return {"status": "ok"}

# 获取身份证区域表
@app.get("/config/id_area")
def get_id_area():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT value FROM config WHERE key='id_area_map'")
    row = c.fetchone()
    conn.close()
    if row:
        return json.loads(row[0])
    return {}

# 更新身份证区域表
@app.put("/config/id_area")
def update_id_area(data: dict = Body(...)):
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE config SET value=?, updated_at=CURRENT_TIMESTAMP WHERE key='id_area_map'", (json.dumps(data),))
    conn.commit()
    conn.close()
    return {"status": "ok"}

# 管理员查看所有日志
ADMIN_PASSWORD = "000"

@app.post("/log/all")
def get_all_logs(data: dict = Body(...)):
    password = data.get("password")
    if password != ADMIN_PASSWORD:
        raise HTTPException(status_code=403, detail="Invalid password")
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT user_id, content, created_at FROM logs ORDER BY created_at DESC")
    rows = c.fetchall()
    conn.close()
    logs = []
    for row in rows:
        try:
            content = json.loads(row[1])
        except:
            content = {}
        logs.append({"user_id": row[0], "content": content, "time": row[2]})
    return {"logs": logs}

# 获取注册开关状态
@app.get("/config/register_status")
def get_register_status():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT value FROM config WHERE key='allow_register'")
    row = c.fetchone()
    conn.close()
    allow = int(row[0]) if row else 1
    return {"allow_register": allow}

# 设置注册开关（管理员）
@app.post("/config/register_status")
def set_register_status(data: dict = Body(...)):
    password = data.get("password")
    allow = data.get("allow_register")
    if password != ADMIN_PASSWORD:
        raise HTTPException(status_code=403, detail="Invalid admin password")
    if allow not in [0, 1]:
        raise HTTPException(status_code=400, detail="allow_register 必须为 0 或 1")
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE config SET value=? WHERE key='allow_register'", (str(allow),))
    conn.commit()
    conn.close()
    return {"status": "ok", "allow_register": allow}

# 管理员获取所有用户
@app.post("/admin/users")
def get_users(data: dict = Body(...)):
    if data.get("password") != ADMIN_PASSWORD:
        raise HTTPException(status_code=403, detail="Invalid admin password")
    conn = get_db()
    c = conn.cursor()
    # 确保 disabled 列存在
    c.execute("PRAGMA table_info(users)")
    columns = [col[1] for col in c.fetchall()]
    if "disabled" not in columns:
        c.execute("ALTER TABLE users ADD COLUMN disabled INTEGER DEFAULT 0")
    c.execute("SELECT id, username, created_at, disabled FROM users ORDER BY created_at DESC")
    rows = c.fetchall()
    conn.close()
    users = [{"id": row[0], "username": row[1], "created_at": row[2], "disabled": row[3]} for row in rows]
    return {"users": users}

# 禁用用户
@app.post("/admin/users/disable")
def disable_user(data: dict = Body(...)):
    if data.get("password") != ADMIN_PASSWORD:
        raise HTTPException(status_code=403, detail="Invalid admin password")
    user_id = data.get("user_id")
    if not user_id:
        raise HTTPException(status_code=400, detail="Missing user_id")
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE users SET disabled=1 WHERE id=?", (user_id,))
    conn.commit()
    conn.close()
    return {"status": "ok"}

# 启用用户
@app.post("/admin/users/enable")
def enable_user(data: dict = Body(...)):
    if data.get("password") != ADMIN_PASSWORD:
        raise HTTPException(status_code=403, detail="Invalid admin password")
    user_id = data.get("user_id")
    if not user_id:
        raise HTTPException(status_code=400, detail="Missing user_id")
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE users SET disabled=0 WHERE id=?", (user_id,))
    conn.commit()
    conn.close()
    return {"status": "ok"}

@app.get("/")
def root():
    return {"message": "ID Rent System API is running"}
