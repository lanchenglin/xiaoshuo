from __future__ import annotations
import asyncio
import hashlib
import json
import time
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit
import httpx
from pydantic import ValidationError
from . import demo
from .store import Store, dumps, uid


class PipelineError(Exception):
    pass


class BudgetStop(PipelineError):
    pass


class UserStop(PipelineError):
    pass


class ModelFailure(PipelineError):
    pass


def provider_endpoint(base: str) -> str:
    parts = urlsplit(base)
    if parts.scheme not in ("https", "http") or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("模型地址必须是无账号、查询参数和片段的 HTTP(S) API 基地址")
    path = base.rstrip("/")
    if path.endswith("/chat/completions"):
        return path
    return path + "/chat/completions"


def parse_json(text: str) -> dict:
    value = text.strip()
    if value.startswith("```") and value.endswith("```"):
        value = value.split("\n", 1)[1].rsplit("```",1)[0].strip()
    result = json.loads(value)
    if not isinstance(result, dict):
        raise ValueError("模型输出必须是 JSON 对象")
    return result


class Gateway:
    def __init__(self, store: Store, project: str, config: dict, snapshot: dict,
                 guard: Callable, transport=None):
        self.store, self.project, self.config = store, project, config
        self.snapshot, self.guard, self.transport = snapshot, guard, transport
        self.system = (Path(__file__).parent / "prompts/base.md").read_text(encoding="utf-8")

    def provider(self, role: str) -> dict:
        providers = self.snapshot.get("providers", [])
        wanted = self.snapshot.get("roles", {}).get(role)
        match = next((p for p in providers if p["id"] == wanted), None)
        if match is None:
            match = next(iter(providers), None)
        if match is None:
            raise ModelFailure("尚未配置模型 API。请先在模型设置中保存配置，或建立离线演示任务。")
        return match

    async def ask(self, step: str, task: str, data: dict, schema, role="planner", output_limit=4096,
                  extra_rules: str = "", validate: Callable | None = None):
        await self.guard()
        saved = self.store.artifact(self.project, step)
        if saved is not None:
            result = schema.model_validate(saved)
            if validate:
                validate(result)
            return result
        if self.config["demo"]:
            result = schema.model_validate(demo.generate(schema, data))
            if validate:
                validate(result)
            self.store.put_artifact(self.project, step, result.model_dump())
            self.store.execute("INSERT INTO calls(id,project_id,step,hash,provider,model,state,usage_kind,created) VALUES (?,?,?,?,?,?,?,?,?)",
                               (uid(),self.project,step,step,"demo","offline-fixture","success","demo",time.time()))
            return result
        provider = self.provider(role)
        max_tokens = min(provider["max_output_tokens"], output_limit)
        system = self.system + ("\n\n"+extra_rules if extra_rules else "")
        messages = [{"role":"system","content":system}, {"role":"user","content":dumps({
            "task":task,"schema":schema.model_json_schema(),"data":data})}]
        last_error = ""
        # One schema repair only. Request retries have their own finite, accountable limit.
        for repair in range(2):
            content, call_id = await self._request(step, messages, provider, max_tokens)
            try:
                result = schema.model_validate(parse_json(content))
                if validate:
                    validate(result)
                self.store.execute("UPDATE calls SET state='success' WHERE id=?", (call_id,))
                self.store.put_artifact(self.project, step, result.model_dump())
                return result
            except (ValueError, ValidationError) as exc:
                last_error = str(exc)[:1600]
                self.store.execute("UPDATE calls SET state='invalid',error=? WHERE id=?", ("输出结构或语义校验失败",call_id))
                if repair == 0:
                    messages += [{"role":"assistant","content":content[:10000]},
                                 {"role":"user","content":f"上次输出未通过校验：{last_error}。重新给出完整、正确的 JSON；不要解释。"}]
        raise ModelFailure("模型输出连续两次未通过结构/语义检查："+last_error[:400])

    async def _request(self, step: str, messages: list, provider: dict, max_tokens: int):
        payload = {"model":provider["model"],"messages":messages,"max_tokens":max_tokens,"stream":False}
        if provider.get("temperature") is not None:
            payload["temperature"] = provider["temperature"]
        key = hashlib.sha256(dumps({"payload":payload,"base":provider["base_url"],"version":1}).encode()).hexdigest()
        cached = self.store.one("SELECT id,response FROM calls WHERE project_id=? AND hash=? AND state='success' ORDER BY created DESC LIMIT 1",
                                (self.project,key))
        if cached:
            return cached["response"],cached["id"]
        # UTF-8 bytes plus framing: deliberately conservative when provider tokenizer is unknown.
        input_bound = len(dumps(messages).encode("utf-8")) + 256
        reserve = input_bound + max_tokens
        if reserve > provider["context_tokens"]:
            raise ModelFailure(f"当前步骤保守上下文估计 {reserve} 超过配置的 {provider['context_tokens']}；请换大上下文模型或减少资料。没有静默截断。")
        headers = {"Content-Type":"application/json"}
        if provider.get("api_key"):
            headers["Authorization"] = "Bearer " + provider["api_key"]
        endpoint = provider_endpoint(provider["base_url"])
        for attempt in range(2):
            await self.guard()
            call_id=uid()
            started=time.time()
            with self.store.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                cfg=json.loads(conn.execute("SELECT config FROM projects WHERE id=?",(self.project,)).fetchone()[0])
                spent=conn.execute("SELECT COALESCE(SUM(charged + CASE WHEN state='inflight' THEN reserved ELSE 0 END),0) FROM calls WHERE project_id=?",(self.project,)).fetchone()[0]
                if spent+reserve > cfg["token_budget"]:
                    raise BudgetStop(f"预算保护：已计入 {spent}，本次需要预留 {reserve}，上限 {cfg['token_budget']}。提高上限后可继续。")
                conn.execute("""INSERT INTO calls(id,project_id,step,hash,provider,model,state,reserved,created)
                    VALUES (?,?,?,?,?,?,'inflight',?,?)""",(call_id,self.project,step,key,provider["id"],provider["model"],reserve,started))
            retryable=False
            try:
                async with httpx.AsyncClient(timeout=provider["timeout_seconds"],trust_env=False,
                                             follow_redirects=False,transport=self.transport) as client:
                    async with client.stream("POST",endpoint,json=payload,headers=headers) as response:
                        raw=bytearray()
                        async for chunk in response.aiter_bytes():
                            raw.extend(chunk)
                            if len(raw)>1500000:
                                raise ModelFailure("模型响应超过1.5MB安全上限")
                        status_code=response.status_code
                if status_code != 200:
                    retryable=status_code == 429 or status_code >= 500
                    # No body in exceptions: upstream responses can echo Authorization or URLs.
                    raise ModelFailure(f"模型 API 返回 HTTP {status_code}；请核对地址、Key、模型和供应商状态。")
                body=json.loads(raw)
                usage=body.get("usage") or {}
                inp=usage.get("prompt_tokens")
                out=usage.get("completion_tokens")
                if isinstance(inp,int) and not isinstance(inp,bool) and isinstance(out,int) and not isinstance(out,bool) and inp>=0 and out>=0:
                    charge,kind=inp+out,"reported"
                else:
                    inp,out,charge,kind=0,0,reserve,"uncertain"
                self.store.execute("UPDATE calls SET input_tokens=?,output_tokens=?,charged=?,usage_kind=?,elapsed=? WHERE id=?",
                                   (inp,out,charge,kind,time.time()-started,call_id))
                choices=body.get("choices") or []
                content=(choices[0].get("message") or {}).get("content") if choices else None
                finish=choices[0].get("finish_reason") if choices else None
                if finish in ("length","content_filter") or not isinstance(content,str) or not content.strip():
                    raise ModelFailure("模型输出为空、被截断或被拦截；结果未作为成功保存。可调高单次输出上限。")
                if len(content)>250000:
                    raise ModelFailure("模型返回内容超出单步安全上限")
                self.store.execute("UPDATE calls SET state='received',response=?,elapsed=? WHERE id=?",
                                   (content,time.time()-started,call_id))
                return content,call_id
            except asyncio.CancelledError:
                # Startup recovery will mark the in-flight reservation as uncertain.
                raise
            except (httpx.HTTPError,ValueError,KeyError,TypeError,ModelFailure) as exc:
                is_network=isinstance(exc,httpx.HTTPError)
                message="网络错误或超时，供应商是否计费未知" if is_network else (str(exc) if isinstance(exc,ModelFailure) else "模型 API 响应格式错误")
                row=self.store.one("SELECT usage_kind FROM calls WHERE id=?",(call_id,))
                if row and not row["usage_kind"]:
                    # Conservative for both rejected and uncertain requests, never labels it a real bill.
                    self.store.execute("UPDATE calls SET charged=reserved,usage_kind='uncertain' WHERE id=?",(call_id,))
                self.store.execute("UPDATE calls SET state='error',error=?,elapsed=? WHERE id=?",(message,time.time()-started,call_id))
                self.store.event(self.project,message,"warning",step=step,attempt=attempt+1)
                if attempt==0 and (retryable or is_network):
                    await asyncio.sleep(1.5)
                    continue
                raise ModelFailure(message) from None
        raise ModelFailure("模型调用失败")
