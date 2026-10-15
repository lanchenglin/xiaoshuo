# 墨流 v0.2：搜索 → 整本下载 → 原创小说的实施方案

## 1. 结论与定位

有可以输入作品网址、自动下载整本的开源项目。Lightnovel Crawler 和 404 小说下载器都属于这一类。所谓“一次下载整本”，通常并不是网站提供一个现成完整文件，而是软件自动获取目录、依次抓取章节，再合成 TXT / EPUB。用户只提交一次任务，下载器承担重复操作。

墨流本次采用：**SearXNG 搜索入口 + 可选 Lightnovel Crawler 下载后端 + 内置公开 HTML 目录下载 + 墨流自己的完整性与断点管理**。不把 Browser-use、Crawl4AI 等相互重叠的工具全部安装一遍；也不让模型逐章操作浏览器。

本方案只处理参考资料的发现、下载、研究和现有原创流程衔接，不涉及投稿、发布。

## 2. 用户怎么使用

在“新建原创小说”中选择“按目录下载整本参考书”，输入作品网址、榜单网址或者几条搜索词，设置需要的参考书数量、篇幅和模型预算，然后启动。

可使用两种模式：

- **全自动创作**：搜索 / 识别作品 → 下载 → 完整性门槛 → 拆书 → 原创方向 / 试写选优 → 人物与卷纲 → 逐章写作 / 审稿 / 润色 → 成书。
- **仅搜索下载**：不需要配置模型。先取得 TXT 和完整性清单；完成后点击“用这些参考开始写书”。此模式的页面入口筛选采用确定性规则，不调用 AI。

下载完成后，“全文分段分析”会把实际取得的全文按段依次研究，并分层归纳；“采样”则只研究开头 / 中间 / 结尾的显式范围。网页新建界面默认全文分析；后端老项目仍保留旧模式和旧默认值。

**整本下载、目录核对通过、作者已经完结、模型已经读完，是四个不同状态。** 页面和报告不能把它们混为一谈。

## 3. 完整数据流

```text
作品网址 ─────────────────────┐
榜单网址 → 读取页面 → 选择实际作品链接 ─┤
搜索词 → SearXNG → 搜索结果 / 榜单入口 ─┤
                                       ↓
                              候选作品与来源依据
                                       ↓
                       识别下载后端、取得目录快照
                                       ↓
                         逐章下载、按章节存储检查点
                                       ↓
                    缺章 / 重复 / 乱码 / 提示页 / 配额检查
                           ↓通过              ↓未通过
                      合并并入库         保存缺章、尝试下一候选
                           ↓
                达到所需参考书数量后，才进入后续研究
                           ↓
                    全文分段分析或显式采样分析
                           ↓
               提炼可借鉴的机制，不把原作梗概拼接成新书
                           ↓
                   现有原创、审稿、润色、完结与导出
```

### 搜索和“热门”的边界

SearXNG 返回搜索结果，不是跨网站统一热度榜。搜索排序、模型选择和真实榜单名次不能等同。

本版保存搜索词、返回网址、标题、页面片段、发现来源与采集时间；全自动创作模式可让已配置的研究模型从实际链接 ID 中挑选作品入口，不允许模型自行编造网址。

当前没有完成“不同网站真实阅读量统一排名”或“一定找出最热门几本”的功能。此类能力需要各榜单的专用解析与校验。本版默认 `heat_verified=false`，不会用搜索第一名冒充热度第一名。

### 优先级

1. 已给具体作品 / 目录网址时，不强制再搜索。
2. 管理员已为该域名启用 Lightnovel Crawler 时，调用固定版本的独立下载进程。
3. 其他来源尝试内置公开 HTML 目录与正文解析。
4. 不支持或不完整时保留原因，尝试其他已发现候选；候选数量不足则停止，不编造书籍或缺失章节。

404 小说下载器在本版作为可手动下载 TXT 后导入的备用工具，**没有包装成墨流自动运行的浏览器扩展**。

## 4. 本次实际实现

| 部分 | 代码 | 作用 |
|---|---|---|
| 采集配置 | `app/acquisition_models.py` | 获取方式、搜索词、所需书数、目录/章节/字符上限、完整性策略、全文/采样、仅下载 |
| 搜索 / 目录 / 下载 | `app/acquisition.py` | SearXNG JSON、候选入口、目录分页、正文分页、下载及持久化、检查和入库 |
| 开源下载器桥接 | `scripts/lncrawl_worker.py` | 固定脚本的独立进程、JSONL 目录与正文协议、有限重试、逐章回传 |
| 可选依赖 | `requirements-lncrawl.txt` | 指定独立环境内安装的上游版本 |
| 工作流 | `app/engine.py` | 下载前置门槛、仅下载结束点、全文分段研究 |
| API | `app/main.py` | 获取进度、导出参考TXT/清单、调整采集、继续写作 |
| 网页 | `app/static/acquisition.js` | 新建配置、参考书卡片、缺章记录、导出与继续按钮 |
| 搜索服务 | `compose.search.yaml`、`scripts/init_search.py` | 可选本机 SearXNG 的配置生成和容器启动示例 |
| 回归测试 | `tests/test_acquisition.py` | 自编离线内容测试，无真实小说、无模型费用 |

该升级不需要把原有数据库清空。新增两张表通过 `CREATE TABLE IF NOT EXISTS` 建立；原来的网页采样、TXT 导入、正文写作和 Humanizer-zh 润色继续保留。

## 5. 下载与完整性规则

### 目录快照

先取得可观察到的目录页及分页，保存章节顺序和网址。内置下载器不会沿用旧网页采样的少量链接上限来冒充整书。

目录达到章节/页数上限时，不静默截断后宣称完整。数字章节跳跃、倒序、重复编号、目录分页循环、无法确认目录容器等情况会记为问题。分卷重新从第一章编号可能触发保守告警，不等于作品一定有缺章。

续传时，目录不能被不兼容地重排或替换。内置解析允许保持原顺序的目录尾部追加；外部下载器桥接当前要求保存的目录与重新取得的目录完全一致，否则要求新建采集，以免混合不同版本。

### 正文

按目录顺序保存章节；支持有限的章内分页，不把“下一章”自动并进同一章。检查正文容器、字符长度、编码、完全重复正文和常见登录 / 购买 / 验证提示。

短作者通知、没有可识别数字编号的特殊排版或非通用容器可能被保守判为不可确认，需站点适配。本版不是通用视觉浏览器，不保证识别所有网站排版。

只使用可合法取得的内容；未加入自动登录、Cookie 导入、付费购买、验证码处理或访问限制绕过模块。

### 成功状态

- `complete_snapshot`：本次已发现目录的章节全部取得，并通过当前检查；不证明目录没有隐藏条目，也不证明作者已经完结。
- `partial`：部分章节成功，或目录存在未解决问题。
- `failed`：没有可用章节。
- `downloading`：任务正在处理，计数从已保存章节读取。

默认 `allow_partial=false`。未通过的书不会进入创作资料池。选择明确允许部分资料后，入库元数据仍标注不是整本，缺失正文不会由 AI 补造。

下载器“退出码为0”不是成功依据。墨流校验回传目录、每章数据、缓存哈希、总量和结束消息。损坏缓存会重新下载，不会当作成功章节导入。

## 6. 数据、断点与预算

目录、每章状态、正文、哈希、错误、重试次数保存在本机 SQLite 的 `reference_books` / `reference_chapters`。资料文件保存在：

```text
data/reference-books/<项目ID>/<参考书ID>/
    book.txt
    manifest.json
```

合并文件使用临时文件原子替换。缺章版本的 TXT 带有明确缺失标记；完整性清单保留全部缺章网址和错误。网页轮询只显示前20条缺章摘要，完整清单仍可下载，避免大型目录反复传输。

同一项目续传跳过已校验成功的章节；参考全文不会因为写作每次重启就重新抓取。当前参考库是项目级，不是全账户跨项目共享书库。

整本下载使用独立上限：默认每本800万字符、全部参考书2000万字符、每本3000章。达到限制时保留已取得章节并停止，不截断半章算成功。这与旧版网页/上传资料的字符上限分开。

网页解析和普通下载不需要 LLM。完整自动模式的入口选择以及下载后的分析/创作使用模型，并受现有 token 预算、上下文检查和检查点约束。全文分析可能比采样贵得多：下载完不等于一次把几百万字放入提示词。

## 7. 部署：先让核心跑起来

在原有仓库目录执行：

```bat
git pull origin main
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe run.py
```

核心内置下载不需要新增 Node、Redis、GPU 或模型。Node 只用于开发检查，不是运行平台的必需依赖。网页仍为 `http://127.0.0.1:8765`。

已有本地 TXT 仍可创建“原有网页采样 / 仅导入本地TXT”模式后上传。新采集界面的“参考书数量”要与实际目标相符；只输入一本且只要一本时设为1，不能维持默认3本后期待立即进入写作。

## 8. 可选：开启搜索

不使用搜索词、仅提供具体目录网址时可以跳过这一节。

已有 SearXNG：开启 `search.formats` 中的 `json`，并在启动墨流前设置 `SEARXNG_URL`。JSON 没开启时，服务可能返回403，程序会明确报错，不把它当作零搜索结果。

需要新部署且已安装 Docker 时：

```bat
.venv\Scripts\python.exe scripts\init_search.py
docker compose -f compose.search.yaml up -d
set SEARXNG_URL=http://127.0.0.1:8080
.venv\Scripts\python.exe run.py
```

`init_search.py` 仅在配置不存在时创建随机 secret，不打印密钥，也不覆盖已有配置。容器只绑定本机 `127.0.0.1:8080`。示例使用 `searxng/searxng:latest` 作为安装入口，可通过 `SEARXNG_IMAGE` 指定自行验收过的标签/摘要；本次没有完成镜像构建与真实搜索引擎出网验收。

Linux 对应使用：

```sh
.venv/bin/python scripts/init_search.py
docker compose -f compose.search.yaml up -d
export SEARXNG_URL=http://127.0.0.1:8080
.venv/bin/python run.py
```

若墨流也在容器中，`127.0.0.1` 指容器自己，不能照搬宿主机地址；需把两个服务放在同一 Docker 网络，配置能从墨流容器访问的搜索服务地址。

## 9. 可选：接入真正的开源整本下载器

### 选择与固定版本

项目：`https://github.com/lncrawl/lightnovel-crawler`

桥接接口对照上游提交 `59b0382d51927953aa8120c5de62dab23ce3f731` 的 `context.py`、`commands/crawl/crawl.py`、章节与文件服务检查。它是本次固定的接口版本，不宣称是稳定发布版。

上游当前 `pyproject.toml` 声明 GPL-3.0-or-later；不是把该依赖重新声明为本平台的许可证。安装、修改或分发上游组件时，应保留并遵守其条款。独立进程用于减少依赖冲突，不是“自动豁免开源许可证”的法律结论。本平台未复制其爬虫源码。

### Windows CMD

在仓库根目录安装独立环境（需本机 Git 能正常联网）：

```bat
python -m venv .venv-lncrawl
.venv-lncrawl\Scripts\python.exe -m pip install -r requirements-lncrawl.txt
.venv-lncrawl\Scripts\python.exe -m lncrawl --help
```

确认上游支持你的来源域名后，使用**真实绝对路径**和**真实域名**设置：

```bat
set LNCRAWL_PYTHON=C:\你的路径\xiaoshuo\.venv-lncrawl\Scripts\python.exe
set LNCRAWL_ALLOWED_HOSTS=你已核对的来源域名,另一个已核对的来源域名
.venv\Scripts\python.exe run.py
```

上面的中文是需要替换的说明，不是可用域名。允许列表采用精确主机名，不支持 `*`。不配置这些变量时，核心不会悄悄调用外部下载器。

Linux：

```sh
python3 -m venv .venv-lncrawl
.venv-lncrawl/bin/python -m pip install -r requirements-lncrawl.txt
export LNCRAWL_PYTHON="$PWD/.venv-lncrawl/bin/python"
export LNCRAWL_ALLOWED_HOSTS='替换为实际来源域名'
.venv/bin/python run.py
```

外部下载器可能依据站点额外需要浏览器和系统依赖；固定接口版本并不保证某个站点当天仍可访问。本次安装尝试已解析到固定提交，但依赖安装在当前执行时限内没有完成，不能声称已安装并对真实小说站完成了端到端测试。

### 进程与网络边界

主程序仅调用仓库内固定桥接脚本，使用参数数组而非 shell 字符串。网页、搜索结果和模型都不能指定可执行文件。外部子进程不继承模型 Key、数据库变量等无关环境变量，数据目录与墨流分开；单次运行上限2小时，暂停/取消会终止该进程并保留已经收到的章节。

内置 HTTP 下载保留原有 DNS 固定、公网地址验证、重定向和 robots 检查。外部引擎由第三方自己负责请求；本版只有域名启用门槛与 URL 公网预检，**不能宣称它获得了与内置请求层相同的逐请求 DNS 固定 / robots 保证**。它是独立进程而非网络沙箱，建议仅在本机可信环境启用；公网部署前需要额外容器化与出口限制。

## 10. API 对接

所有 API 沿用原登录 Cookie；写请求带 `X-Requested-With: MoLiu`。

创建任务的关键配置：

```json
{
  "title": "我的新小说",
  "genre": "悬疑",
  "urls": ["https://你的来源网站/作品目录"],
  "chapter_count": 30,
  "chapter_chars": 2000,
  "token_budget": 2000000,
  "acquisition": {
    "mode": "books",
    "queries": [],
    "book_count": 1,
    "backend": "auto",
    "analysis": "full",
    "acquire_only": true,
    "allow_partial": false,
    "max_chapters": 3000,
    "max_book_chars": 8000000,
    "max_total_chars": 20000000
  }
}
```

这是结构示例，网址要替换为真实来源。只输入搜索词时 `urls` 可为空；`acquisition.mode` 必须为 `books`。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/acquisition/status` | 是否配置搜索/外部下载器，不返回凭据与可执行文件路径 |
| GET | `/api/projects/{pid}` | 附带 `reference_books` 进度及完整性摘要 |
| PUT | `/api/projects/{pid}/acquisition` | 尚未拆书且没有活动任务时修改采集要求，保留已下载章节 |
| GET | `/api/projects/{pid}/books/{bid}/chapters` | 目录、状态、错误、重试次数 |
| GET | `/api/projects/{pid}/books/{bid}/export/txt` | 下载带状态标记的参考TXT |
| GET | `/api/projects/{pid}/books/{bid}/export/manifest` | 下载完整性清单 |
| POST | `/api/projects/{pid}/continue-writing` | 仅下载任务完成后，配置模型并继续原有创作流程 |

开始 / 暂停 / 取消仍使用原有接口。任务进入拆书后，不允许随意更换参考材料，以免现有大纲和正文与新资料混淆。

## 11. 验收与尚未完成

本次测试使用自编章节与模拟 HTTP，验证顺序、超过60章的目录、分页、缺章、重复、乱码/提示页、字符上限、断点、缓存损坏、搜索 JSON、权限和上游桥接协议。不是用演示数据声称读到了真实热门小说。

尚未验收：真实 SearXNG 出网、真实小说站成功率、外部下载器完整依赖安装与实站运行、更新后网页的完整浏览器交互、Windows 真机、Docker运行、数百万字连续下载和长篇模型成本。已完成的测试项目和具体数量见 `TEST_REPORT.md`，不要把历史 v0.1.0 测试记录当成本版新增功能实测。

后续优先事项：先针对实际需要的几个来源做下载样本验收，再增加该站规则、榜单字段映射和必要的隔离浏览器。默认不先扩张到几十个未验证的站点。

## 12. 核对来源

- Lightnovel Crawler 官方仓库与使用说明：https://github.com/lncrawl/lightnovel-crawler
- 固定接口源码：https://github.com/lncrawl/lightnovel-crawler/tree/59b0382d51927953aa8120c5de62dab23ce3f731
- SearXNG Search API：https://docs.searxng.org/dev/search_api.html
- 404 小说下载器：https://github.com/404-novel-project/novel-downloader

核对日期：2026-10-15。第三方支持站点会变化；上述链接与功能说明不能替代目标网址的实测。
