from __future__ import annotations
import asyncio
import hashlib
import io
import json
import os
import sqlite3
import time
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit
import httpx
from fastapi import FastAPI,HTTPException,Request,UploadFile,File
from fastapi.responses import FileResponse,JSONResponse,Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel,Field
from . import __version__
from .engine import Runner
from .llm import provider_endpoint
from .models import SettingsInput,ProjectInput,BudgetInput,SourceInput
from .security import Security
from .sources import normalize_url
from .store import Store,dumps,uid
from .quality import char_count


class WorkerLock:
    """Prevent two app processes from concurrently spending against the same database."""
    def __init__(self,path):
        self.file=open(path,"a+b")
        self.file.seek(0)
        if self.file.read(1)==b"":
            self.file.write(b"0")
            self.file.flush()
        self.file.seek(0)
        try:
            if os.name=="nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.file,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise RuntimeError("同一个 data 目录只能运行一个 worker；请勿使用 uvicorn --workers 2 或多个副本") from None
    def close(self):
        self.file.close()


class Login(BaseModel):
    password: str=Field(min_length=1,max_length=300)


class TestProvider(BaseModel):
    provider_id: str


def create_app(data_dir: Path | str | None=None,start_worker: bool=True,password: str | None=None):
    data=Path(data_dir or os.getenv("DATA_DIR","data"))
    store=Store(data/"studio.sqlite3")
    security=Security(data,password)
    runner=Runner(store,security)
    @asynccontextmanager
    async def lifespan(app):
        lock=WorkerLock(data/"worker.lock") if start_worker else None
        if start_worker:
            runner.start()
        try:
            yield
        finally:
            await runner.close()
            if lock:
                lock.close()
    app=FastAPI(title="墨流 · 自动小说工作台",version=__version__,lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
    app.state.store,app.state.security,app.state.runner=store,security,runner

    @app.middleware("http")
    async def protection(request,call_next):
        if request.url.path.startswith("/api/"):
            if request.method not in ("GET","HEAD","OPTIONS"):
                origin=request.headers.get("origin")
                configured=os.getenv("PUBLIC_ORIGIN","").rstrip("/")
                expected=str(request.base_url).rstrip("/")
                if request.headers.get("x-requested-with")!="MoLiu" or (origin and origin.rstrip("/") not in {expected,configured}):
                    return JSONResponse({"detail":"请求来源验证失败"},403)
            if request.url.path not in {"/api/auth/login","/api/health"} and not security.valid(request.cookies.get("moliu_session","")):
                return JSONResponse({"detail":"请先登录"},401)
        response=await call_next(request)
        response.headers["X-Content-Type-Options"]="nosniff"
        response.headers["X-Frame-Options"]="DENY"
        response.headers["Referrer-Policy"]="same-origin"
        response.headers["Content-Security-Policy"]="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"]="no-store"
        return response

    def project_row(pid):
        row=store.one("SELECT * FROM projects WHERE id=?",(pid,))
        if not row:
            raise HTTPException(404,"项目不存在")
        return row

    def settings_raw():
        encrypted=store.setting("providers_encrypted")
        return security.decrypt(encrypted) if encrypted else {"providers":[],"roles":{}}

    def source_write(pid,title,text):
        row=project_row(pid)
        if row["status"]!="draft":
            raise HTTPException(409,"任务启动后参考资料固定；请新建任务以使用不同资料")
        cfg=json.loads(row["config"])
        text=text.replace("\r\n","\n").strip()
        if len(text)<200 or "\x00" in text:
            raise HTTPException(400,"正文至少200字符，且不能包含二进制空字符")
        digest=hashlib.sha256(text.encode()).hexdigest()
        if store.one("SELECT id FROM sources WHERE project_id=? AND digest=?",(pid,digest)):
            raise HTTPException(409,"相同内容已导入，无需重复上传")
        total=store.one("SELECT COALESCE(SUM(length(text)),0) n FROM sources WHERE project_id=?",(pid,))["n"]
        if total+len(text)>cfg["max_source_chars"]:
            raise HTTPException(400,f"导入后超过本项目资料上限 {cfg['max_source_chars']} 字符；请新建更大上限项目")
        sid=uid()
        store.execute("INSERT INTO sources VALUES (?,?,?,?,?,?,?,?)",(sid,pid,"upload:"+sid,title,text,digest,"ready",dumps({"captured_chars":len(text),"truncated":False,"coverage":"用户提供的文本，不假定为整本"})))
        return {"id":sid,"chars":len(text)}

    @app.get("/api/health")
    async def health():
        return {"ok":True,"version":__version__}

    @app.post("/api/auth/login")
    async def login(body: Login,request: Request):
        try:
            token=security.login(body.password,request.client.host if request.client else "unknown")
        except ValueError as exc:
            raise HTTPException(429,str(exc)) from None
        if not token:
            raise HTTPException(401,"密码错误")
        response=JSONResponse({"ok":True})
        response.set_cookie("moliu_session",token,max_age=86400,httponly=True,samesite="strict",secure=os.getenv("COOKIE_SECURE","0")=="1")
        return response

    @app.post("/api/auth/logout")
    async def logout():
        response=JSONResponse({"ok":True})
        response.delete_cookie("moliu_session")
        return response

    @app.get("/api/meta")
    async def meta():
        humanizer=Path(__file__).parent.parent/"third_party/humanizer-zh/SKILL.md"
        return {"version":__version__,"humanizer_available":humanizer.is_file(),"worker_enabled":start_worker,
                "privacy":"模型Key加密保存，只发给所配置的模型地址；小说资料会随任务发送给该模型。"}

    @app.get("/api/settings")
    async def get_settings():
        raw=settings_raw()
        return {"providers":[{**{k:v for k,v in p.items() if k!="api_key"},"api_key":"","has_key":bool(p.get("api_key"))} for p in raw["providers"]],"roles":raw["roles"]}

    @app.put("/api/settings")
    async def put_settings(body: SettingsInput):
        old={p["id"]:p for p in settings_raw()["providers"]}
        ids=[p.id for p in body.providers]
        if len(set(ids))!=len(ids):
            raise HTTPException(400,"模型配置ID不可重复")
        if any(role not in {"research","planner","writer","reviewer","polisher"} or value not in ids for role,value in body.roles.items()):
            raise HTTPException(400,"角色或角色对应的模型配置不存在")
        result=[]
        for p in body.providers:
            try:
                provider_endpoint(p.base_url)
            except ValueError as exc:
                raise HTTPException(400,str(exc)) from None
            value=p.model_dump()
            if not value["api_key"]:
                value["api_key"]=old.get(p.id,{}).get("api_key","")
            result.append(value)
        store.put_setting("providers_encrypted",security.encrypt({"providers":result,"roles":body.roles}))
        return {"ok":True,"message":"已加密保存。已排队/运行任务保持原模型快照，暂停后重新开始使用新配置。"}

    @app.post("/api/settings/test")
    async def test_provider(body: TestProvider):
        p=next((p for p in settings_raw()["providers"] if p["id"]==body.provider_id),None)
        if not p:
            raise HTTPException(404,"请先保存模型配置")
        headers={"Authorization":"Bearer "+p["api_key"]} if p.get("api_key") else {}
        started=time.time()
        try:
            async with httpx.AsyncClient(timeout=min(p["timeout_seconds"],60),trust_env=False,follow_redirects=False) as client:
                response=await client.post(provider_endpoint(p["base_url"]),headers=headers,json={"model":p["model"],"messages":[{"role":"user","content":"只回复 OK"}],"max_tokens":32,"stream":False})
            if response.status_code!=200:
                raise HTTPException(400,f"连接测试返回 HTTP {response.status_code}；未向页面显示供应商原始响应")
            raw=response.json()
            text=(raw.get("choices") or [{}])[0].get("message",{}).get("content")
            if not isinstance(text,str) or not text.strip():
                raise HTTPException(400,"收到响应但没有可用正文；当前接口需支持 chat/completions")
            usage=raw.get("usage") or {}
            store.put_setting("last_connection_test",{"provider_id":p["id"],"time":time.time(),"usage":usage})
            return {"ok":True,"seconds":round(time.time()-started,2),"usage":usage,"note":"测试可能消耗少量供应商token，不计入某一本书的预算"}
        except (httpx.HTTPError,ValueError,TypeError,IndexError):
            raise HTTPException(400,"网络连接失败、超时或响应格式不兼容；请核对 API 地址和模型") from None

    @app.get("/api/projects")
    async def projects():
        return store.all("SELECT p.id,p.title,p.status,p.stage,p.created,p.updated,p.error,p.config,(SELECT COUNT(*) FROM chapters c WHERE c.project_id=p.id) chapters FROM projects p ORDER BY created DESC")

    @app.post("/api/projects",status_code=201)
    async def new_project(body: ProjectInput):
        try:
            body.urls=[normalize_url(u) for u in body.urls]
        except ValueError as exc:
            raise HTTPException(400,str(exc)) from None
        pid=uid()
        now=time.time()
        store.execute("INSERT INTO projects(id,title,config,created,updated) VALUES (?,?,?,?,?)",(pid,body.title,dumps(body.model_dump()),now,now))
        store.event(pid,"项目已建立",demo=body.demo)
        return {"id":pid}

    @app.get("/api/projects/{pid}")
    async def detail(pid: str):
        row=project_row(pid)
        row["config"]=json.loads(row["config"])
        chapters=store.all("SELECT number,title,char_count(text) chars,summary,report,created FROM chapters WHERE project_id=? ORDER BY number",(pid,))
        sources=store.all("SELECT id,url,title,status,length(text) chars,meta FROM sources WHERE project_id=? ORDER BY rowid",(pid,))
        artifacts=store.all("SELECT key,length(value) chars,created FROM artifacts WHERE project_id=? ORDER BY created",(pid,))
        events=store.all("SELECT * FROM events WHERE project_id=? ORDER BY id DESC LIMIT 120",(pid,))
        revisions=store.all("SELECT id,number,label,title,char_count(text) chars,created FROM revisions WHERE project_id=? ORDER BY number,created",(pid,))
        jobs=store.all("SELECT id,status,stop,created,updated,error FROM jobs WHERE project_id=? ORDER BY created DESC LIMIT 10",(pid,))
        return {"project":row,"chapters":chapters,"sources":sources,"artifacts":artifacts,"events":events,"revisions":revisions,"jobs":jobs,"usage":store.usage(pid)}

    @app.post("/api/projects/{pid}/sources/text",status_code=201)
    async def add_text(pid: str,body: SourceInput):
        return source_write(pid,body.title,body.text)

    @app.post("/api/projects/{pid}/sources/file",status_code=201)
    async def add_file(pid: str,file: UploadFile=File(...)):
        if not file.filename or Path(file.filename).suffix.lower() not in {".txt",".md"}:
            raise HTTPException(400,"当前支持 TXT / Markdown 文本，不处理 PDF、图片或 Word")
        raw=await file.read(6000001)
        if len(raw)>6000000:
            raise HTTPException(400,"单文件不能超过6MB")
        try:
            text=raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            try:
                text=raw.decode("gb18030")
            except UnicodeDecodeError:
                raise HTTPException(400,"无法识别编码，请保存为 UTF-8 TXT 后重试") from None
        return source_write(pid,Path(file.filename).name[:200],text)

    @app.post("/api/projects/{pid}/start")
    async def start(pid: str):
        row=project_row(pid)
        if row["status"] in {"queued","running","completed","completed_with_warnings"}:
            raise HTTPException(409,"项目已在运行/排队或已经完成，不能重复启动")
        cfg=json.loads(row["config"])
        settings=settings_raw()
        if not cfg["demo"] and not settings["providers"]:
            raise HTTPException(400,"请先配置模型 API")
        if not cfg["demo"] and not cfg["urls"] and not store.one("SELECT id FROM sources WHERE project_id=? AND status='ready'",(pid,)):
            raise HTTPException(400,"请先提供参考网址或导入文本")
        jid=uid()
        now=time.time()
        try:
            with store.connect() as conn:
                conn.execute("INSERT INTO jobs(id,project_id,status,snapshot,created,updated) VALUES (?,?,?,?,?,?)",(jid,pid,"queued",security.encrypt(settings),now,now))
                conn.execute("UPDATE projects SET status='queued',error='',updated=? WHERE id=?",(now,pid))
        except sqlite3.IntegrityError:
            raise HTTPException(409,"同一本书已有活动任务") from None
        store.event(pid,"任务进入自动生产队列",job_id=jid)
        return {"job_id":jid}

    @app.post("/api/projects/{pid}/stop/{action}")
    async def stop(pid: str,action: str):
        project_row(pid)
        if action not in {"pause","cancel"}:
            raise HTTPException(400,"仅支持 pause/cancel")
        row=store.one("SELECT * FROM jobs WHERE project_id=? AND status IN ('queued','running')",(pid,))
        if not row:
            raise HTTPException(409,"没有活动任务")
        with store.connect() as conn:
            conn.execute("UPDATE jobs SET stop=?,updated=? WHERE id=?",(action,time.time(),row["id"]))
            if row["status"]=="queued":
                state="paused" if action=="pause" else "cancelled"
                conn.execute("UPDATE jobs SET status=? WHERE id=?",(state,row["id"]))
                conn.execute("UPDATE projects SET status=?,updated=? WHERE id=?",(state,time.time(),pid))
        return {"ok":True,"note":"正在执行的模型请求不会强行丢弃结果；会在下一个安全检查点停止。"}

    @app.put("/api/projects/{pid}/budget")
    async def budget(pid: str,body: BudgetInput):
        row=project_row(pid)
        cfg=json.loads(row["config"])
        cfg.update(body.model_dump())
        store.execute("UPDATE projects SET config=?,updated=? WHERE id=?",(dumps(cfg),time.time(),pid))
        return {"ok":True,"note":"预算立即生效；修订次数调整在重新开始任务后生效。"}

    @app.get("/api/projects/{pid}/artifacts/{key:path}")
    async def artifact(pid: str,key: str):
        project_row(pid)
        value=store.artifact(pid,key)
        if value is None:
            raise HTTPException(404,"该步骤尚未生成结果")
        return value

    @app.get("/api/projects/{pid}/chapters/{number}")
    async def chapter(pid: str,number: int):
        project_row(pid)
        row=store.one("SELECT * FROM chapters WHERE project_id=? AND number=?",(pid,number))
        if not row:
            raise HTTPException(404,"该章节尚未验收，草稿可在版本记录中查看")
        row["chars"]=char_count(row["text"])
        return row

    @app.get("/api/projects/{pid}/revisions/{revision_id}")
    async def revision(pid: str,revision_id: str):
        project_row(pid)
        row=store.one("SELECT * FROM revisions WHERE project_id=? AND id=?",(pid,revision_id))
        if not row:
            raise HTTPException(404,"版本不存在")
        return row

    @app.get("/api/projects/{pid}/calls")
    async def calls(pid: str):
        project_row(pid)
        return store.all("SELECT id,step,provider,model,state,input_tokens,output_tokens,charged,reserved,usage_kind,error,elapsed,created FROM calls WHERE project_id=? ORDER BY created DESC LIMIT 300",(pid,))

    @app.get("/api/projects/{pid}/export/{kind}")
    def export(pid: str,kind: str):
        project=project_row(pid)
        chapters=store.all("SELECT * FROM chapters WHERE project_id=? ORDER BY number",(pid,))
        if not chapters:
            raise HTTPException(400,"还没有已验收章节；草稿请在版本记录中查看")
        full=project["title"]+"\n\n"+"\n\n".join(f"第{c['number']}章 {c['title']}\n\n{c['text']}" for c in chapters)
        if kind=="txt":
            return Response(full.encode("utf-8-sig"),media_type="text/plain; charset=utf-8",headers={"Content-Disposition":f'attachment; filename="novel-{pid[:8]}.txt"'})
        if kind!="zip":
            raise HTTPException(400,"导出仅支持 txt/zip")
        content=io.BytesIO()
        with zipfile.ZipFile(content,"w",zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("novel.txt",full.encode("utf-8-sig"))
            archive.writestr("project.json",dumps({**project,"config":json.loads(project["config"])}))
            archive.writestr("usage.json",dumps(store.usage(pid)))
            archive.writestr("sources-metadata.json",dumps(store.all("SELECT title,url,status,meta FROM sources WHERE project_id=?",(pid,))))
            for c in chapters:
                archive.writestr(f"chapters/{c['number']:04d}.txt",c["text"].encode("utf-8-sig"))
                archive.writestr(f"reports/{c['number']:04d}.json",c["report"])
            for a in store.all("SELECT key,value FROM artifacts WHERE project_id=? AND (key LIKE 'plan/%' OR key LIKE 'audit/%' OR key LIKE 'coverage/%' OR key='research/synthesis')",(pid,)):
                archive.writestr(f"artifacts/{a['key']}.json",a["value"])
            for r in store.all("SELECT number,label,text FROM revisions WHERE project_id=?",(pid,)):
                archive.writestr(f"versions/{r['number']:04d}-{r['label']}.txt",r["text"])
            archive.writestr("README.txt","此包不包含模型密钥、会话、原始参考全文。完成章数与任务目标可能不同，见project.json。质量报告不构成原创性或作者身份认证。")
        return Response(content.getvalue(),media_type="application/zip",headers={"Content-Disposition":f'attachment; filename="novel-{pid[:8]}-project.zip"'})

    static=Path(__file__).parent/"static"
    app.mount("/assets",StaticFiles(directory=static),name="assets")
    @app.get("/")
    async def index():
        return FileResponse(static/"index.html")
    return app
