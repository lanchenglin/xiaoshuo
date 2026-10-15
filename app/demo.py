"""Deterministic offline fixture. NOT a model and NOT a production-writing substitute."""
from __future__ import annotations
from .models import *


def generate(schema, data: dict) -> dict:
    number = data.get("chapter_number", 1)
    target = data.get("target_chars", 800)
    if schema is Analysis:
        return {"summary":"[演示数据] 材料展示了人物面对问题时通过行动获得信息。",
                "mechanisms":[{"name":"目标驱动", "explanation":"让人物选择推进情节", "evidence":"演示素材中的行动描写", "transferable":"建立独立目标和阻碍，不复制原事件"}],
                "uncertainties":["演示模式没有阅读外部网页或调用真实模型"]}
    if schema is Synthesis:
        return {"audience":"[演示数据] 喜欢小人物调查的读者", "methods":["行动获得信息", "阶段回报引出新的目标"],
                "avoid_copying":["不搬用参考文本的人物和事件链"],"limitations":["演示结果不代表市场分析"]}
    if schema is Concepts:
        return {"candidates":[{"id":str(i),"title":f"[演示] 旧城修补局·方案{i}",
                "premise":f"方案{i}：旧城里一名修理工在检修公共钟表时发现计时记录与街区失踪案有关，他必须在居民搬迁前查明旧档案中的真相。",
                "difference":"自行建立修理工的目标、能力限制和调查事件，未使用原作专名。",
                "conflict":"主角缺少档案权限，必须以修缮换取信任，同时面对错误线索。",
                "ending":"主角修复报时系统并公开经过核实的档案。"} for i in range(1,4)]}
    if schema is Selection:
        return {"ratings":[{"id":str(i),"score":91-i,"reason":"[演示评分] 用于验证自动选题，不代表真实质量"} for i in range(1,4)]}
    if schema is Bible:
        count=data.get("chapter_count",2)
        return {"title":"[演示] 旧城修补局", "premise":"修理工在修缮中调查旧城记录。",
                "rules":["主角只能修理设备，不能读取物品记忆", "全部线索须通过行动验证"],
                "characters":[{"id":"c1","name":"陆行","goal":"查清错乱的记录","voice":"短句，直说","constraints":"没有执法权限"}],
                "arcs":[{"start":1,"end":count,"goal":"发现问题并解决","turning_point":"核对两份不一致的工单"}],
                "ending":"档案得到核实。", "style_guide":"[演示] 用具体行动推动调查。"}
    if schema is ChapterPlan:
        return {"title":f"[演示] 第{number}份工单", "objective":f"查验编号{number}的设备",
                "scenes":["到场检查", "发现记录不同", "核实线索"],"must_preserve":["主角无超能力"],"ending_state":"记录下下一处维修地址"}
    if schema is Draft:
        # Every sentence contains a distinct work-order ID, to exercise duplicate diagnostics.
        parts = [f"【演示数据·第{number}章】以下文字仅用于检验系统流程，不是真实模型创作。"]
        i=0
        while len(''.join(parts)) < target:
            i+=1
            parts.append(f"陆行翻开第{number}册第{i}页工单，把第{number}-{i}号齿轮放在布上。他对照栏内的刻度检查接缝，窗边传来第{number*100+i}次试钟的轻响。")
        return {"title":f"[演示] 第{number}份工单", "text":"\n\n".join(parts)}
    if schema is Review:
        return {"score":90,"issues":[],"repair_brief":"[演示评审] 无真实文学质量判断"}
    if schema is Fidelity:
        return {"preserved":True,"issues":[]}
    if schema is Memory:
        return {"summary":f"[演示] 第{number}章核查设备并记录线索。",
                "updates":[{"key":"c1-location","kind":"character","content":f"第{number}处维修现场","closed":False}]}
    if schema is BookAudit:
        return {"summary":"[演示] 全流程已完成；此结果不代表真实模型连载质量。","issues":[]}
    if schema is CrawlChoice:
        return {"ids":[],"reason":"演示模式不抓取外部网站"}
    raise ValueError(f"Missing demo fixture: {schema.__name__}")
