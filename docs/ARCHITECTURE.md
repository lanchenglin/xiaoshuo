> v0.2补充：搜索、整本下载、独立下载器和全文分析已经新增。当前方案/部署/API见 [SEARCH_DOWNLOAD_PLAN.md](SEARCH_DOWNLOAD_PLAN.md)，实际完成范围以 [STATUS.md](STATUS.md) 为准。下面保留原有模块说明，旧版待办不作为新增模块的验收结论。

# 架构与开发接口

## 实际技术栈

FastAPI + 标准SQLite + httpx + 原生HTML/JS/CSS。单用户登录、HttpOnly/SameSite会话；供应商配置和任务模型快照用Fernet加密。数据与任务在本地SQLite持久化，不需要Redis、前端构建或向量库。

目录：
- `app/main.py`：HTTP API、鉴权、项目与文件管理、导出。
- `app/store.py`：SQLite事务、检查点、调用账本。
- `app/sources.py`：公开网页安全读取、提取、同站候选链接与采样区间。
- `app/llm.py`：兼容模型协议、预算预留、有限重试、JSON修复、响应缓存。
- `app/engine.py`：研究/选题/试写/建书/分章生产/复核/记忆与完结检查。
- `app/models.py`：输入及模型输出Pydantic结构约束。
- `app/quality.py`：统一字数、长重复、参考文本长连续重合、模板表达线索。
- `app/prompts`：原创生产提示与小说润色约束。
- `third_party/humanizer-zh`：真实内置并固定版本的外部编辑规则。
- `app/demo.py`：明确标注的离线测试输出，绝不伪装模型。

## 正常流程与数据隔离

1. 抓取/导入资料：正文、来源、错误和覆盖情况分开保存。
2. 分段研究：片段有明确字符区间；证据必须是该片段中不超过160字符的连续原文。结构或证据错误允许一次修复。
3. 分层归并：每组最多4份分析，递归归并防止把整书粗暴塞进上下文。
4. 新书规划只使用抽象机制，而不是原文或事件缩写。保留三个方案、三个开篇样稿和自动评分理由。
5. 独立设定一次确立，卷纲必须连续完整覆盖目标章节，人物ID唯一。
6. 每章：细纲→正文→机器检查/模型审稿→有限修订→可选润色→事实对照/再次审稿→验收→原子提交章节和记忆。
7. 每10章摘要级复盘。全书结束分层汇总，输出未回收伏笔和未解决问题。

所谓“角色分工”是不同任务提示与可配置模型，并非多个可随意执行shell的代理。网页和小说中的指令均作为不可信资料，不赋予它们工具权限。

## 状态与恢复

项目状态：`draft → queued → running → completed / completed_with_warnings`。
异常分支：`paused`（预算/人工），`blocked`（质量门），`failed`（模型/数据/内部错误），`cancelled`（用户取消）。

只有 `chapters` 表里的正文是权威验收版本。`revisions` 保留draft/revision-N/polished/polished-rejected；无质量保证的版本不会写入权威正文。

每个步骤的结构化结果使用稳定artifact key保存。新任务从已有artifact继续。更新模型后，已完成步骤仍复用，后续步骤使用新快照；不在运行中悄悄换模型。重启会将运行中任务重新排队。进程锁防止同一数据目录双worker。

调用状态 `inflight → received → success / invalid / error`。收到但未完成验证的响应不算成功缓存。崩溃中不确定调用会保守记账，可能需要重试；不能跨远程供应商保证exactly-once计费。已有验证结果不会因为浏览器刷新而重复生成。

## 上下文与记忆

本版不是完整向量RAG：固定书设定＋当前卷＋最近5章摘要＋上一章结尾＋角色/开放伏笔优先的相关词与近因检索。选用的事实ID和数量保存为 `context/N`，不声称每章重读所有历史正文。未提及的旧事实不删除；同一事实key更新状态。

篇幅增长后仍可能出现上下文遗漏，需实测。本版不会为满足token限制静默截断完整请求；超过保守上下文估计时暂停报错。资料研究的采样属于显式配置与可见记录，与模型请求静默截断不同。

## 预算

每次真实请求以 `输入UTF-8字节数+256+max_output_tokens` 预留。未知tokenizer时这是保守估算，不是供应商官方token计数。供应商报告用量优先；无用量或不确定失败则保守记账并标记unknown。上下文检查也使用该估计，可能提前拦截实际仍可容纳的请求。

对429/5xx/网络异常最多重试一次；结构错误最多修复一次。认证等不可重试错误直接停止。每次尝试分别记账，不以缓存命中来虚构费用。连接测试单独记录最近一次用量，不属于某书预算。

## API（需登录Cookie）

写请求必须带 `X-Requested-With: MoLiu`，且Origin须同源或等于管理员配置的PUBLIC_ORIGIN。无CORS放行。页面调用示例可直接参看`app/static/app.js`。

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/api/auth/login` | 密码登录 |
| GET/PUT | `/api/settings` | 读取脱敏配置/加密保存 |
| POST | `/api/settings/test` | 用户手动发起模型连接测试 |
| GET/POST | `/api/projects` | 列出/创建项目 |
| GET | `/api/projects/{id}` | 进度、资料元数据、章节、版本、事件、用量 |
| POST | `/api/projects/{id}/sources/text` | 导入文本 |
| POST | `/api/projects/{id}/sources/file` | 上传TXT/Markdown |
| POST | `/api/projects/{id}/start` | 开始/恢复 |
| POST | `/api/projects/{id}/stop/pause` | 安全点暂停 |
| POST | `/api/projects/{id}/stop/cancel` | 取消但保留产物 |
| PUT | `/api/projects/{id}/budget` | 调整预算与修订次数 |
| GET | `/api/projects/{id}/chapters/{n}` | 已验收正文 |
| GET | `/api/projects/{id}/revisions/{revision_id}` | 特定版本 |
| GET | `/api/projects/{id}/artifacts/{key}` | 阶段JSON |
| GET | `/api/projects/{id}/calls` | 最近300次脱敏调用记录 |
| GET | `/api/projects/{id}/export/txt` | 整本或当前已完成章节 |
| GET | `/api/projects/{id}/export/zip` | 正文、各版、规划、报告（不含Key与参考全文） |

## 安全边界

采集器只允许公开HTTP(S)80/443；解析全部DNS结果并阻断私网、回环、链路本地、多播等地址，实际请求固定到已校验IP并保留TLS SNI/Host，重定向重新校验。正文下载限3MB，robots限200KB；robots受限或不可读取时保守跳过，404视为无该文件。默认串行且页面间隔1秒，未实现专有站点限速配置。

模型API是管理员显式信任的地址，可使用本地网关；它与公共来源采集器权限不同。Key只随Authorization发给该模型地址。上传仅文本，不执行任何用户脚本。页面用textContent而非不可信HTML拼接。所有导出路径由平台生成，不使用模型生成的任意文件路径。

仍需公网加固、完整威胁建模和负载验收；当前定位单用户本机/可信网络。
