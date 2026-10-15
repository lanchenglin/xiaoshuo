# 墨流 · 全流程 AI 小说工作台

给参考网址或 TXT / Markdown，设置目标章节和 token 预算，自动完成：

**资料采集 → 分段研究 → 跨资料机制归纳 → 3 个原创新方向 → 开篇试写 → 自动选优 → 人物与分卷设定 → 逐章细纲/写作/审稿/修订 → 润色与保真检查 → 更新记忆 → 完结检查 → TXT / 项目包导出。**

这是可运行的 **v0.1.0 单用户初版**，不是只有需求文档，也不是“输入网址必出爆款”的承诺。服务在你自己的电脑或服务器持续运行，不依赖聊天窗口保持打开。

## 先看这几个边界

- 本仓库是**独立实现的统一编排平台**。没有把 Oh Story 与 Biz Novel Studio 两套源码原样合并，也没有冒充它们的官方联动插件；具体分工见 [开源项目集成说明](docs/UPSTREAM.md)。
- **真正内置 Humanizer-zh 固定版本的编辑规则**，外加本项目的小说保真约束。它是交给模型执行的编辑指导，不是AI身份检测器，不保证“去AI味”效果或检测结果。
- 当前网站读取是**限额内的通用公开 HTML/文本采集与同站链接选择**。支持发现链接，不意味着支持任意小说站、登录、付费章节、JS动态渲染，或能可靠找到所有站点的“最高浏览量小说”。
- 研究范围会记录**取得多少、分析多少、是否采样、实际字符区间**；未取得的内容不假装读过。
- 本版不包含投稿、自动发布、多用户、收费平台。默认只监听本机。
- 已有离线和模拟HTTP接口自动化测试。**尚未用你的真实Key跑完整本长篇，也没有完成真实多站点与大规模连载验收。**

## 1. 启动（Windows / Linux / WSL）

需要 Python 3.11–3.13；无需显卡、Node、Redis或数据库服务。Python 3.11.9 可作为本地安装起点；安装依赖仍需可访问软件源。测试依赖与运行依赖分开。

```bash
git clone https://github.com/lanchenglin/xiaoshuo.git
cd xiaoshuo
python -m venv .venv
```

Windows PowerShell：

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python run.py
```

Windows CMD：

```bat
.venv\Scripts\activate.bat
python -m pip install -r requirements.txt
python run.py
```

Linux / WSL：

```bash
source .venv/bin/activate
python -m pip install -r requirements.txt
python run.py
```

浏览器打开 **http://127.0.0.1:8765**。未设置 `APP_PASSWORD` 时，首次随机密码写入 `data/initial-password.txt`，在本机查看。模型密钥不写入这个文件。

PowerShell 查看密码：

```powershell
Get-Content .\data\initial-password.txt
```

Linux / WSL：

```bash
cat data/initial-password.txt
```

**先试“离线演示”**：两章演示可验证页面、队列、版本、记忆和导出。演示正文显著标注，不会消耗模型 token，也不代表模型质量。

## 2. Docker

```bash
cp .env.example .env
# 编辑 .env，把 APP_PASSWORD 换成你自己的至少12位密码
docker compose up -d --build
```

默认访问 `http://127.0.0.1:8765`。容器中运行非 root 用户，数据放在 `novel_data` 卷。不要用 `docker compose down -v` 更新，否则会删除卷。

更多启动、备份和远程访问说明：[DEPLOYMENT.md](docs/DEPLOYMENT.md)。

## 3. 配置模型与开始创作

1. 在“模型与角色设置”添加 API 基地址、模型名和 Key。通常基地址形如 `https://你的服务/v1`；程序拼接 `/chat/completions`。完整 `/chat/completions` 地址也接受。
2. 保存并手动点击“测试连接”。测试会产生少量真实调用，不算进某本书的预算；返回有效文字才显示成功。
3. 可把研究、规划、正文、审稿、润色分别分配给不同配置。一个模型也可全包；不同角色不意味着用了不同模型，取决于你的选择。
4. 新建小说，粘贴网址、设置篇幅/预算，自动进入队列。没有网址时先创建，再导入参考文件并点“开始全自动”。
5. 不需要手动确认候选、逐章点继续；正常情况下跑到目标章节结束。遇到预算、严重质量问题、持续网络错误或用户暂停才停止。

当前只实现**兼容 Chat Completions 的非流式文本接口**。不是所有标注“兼容”的服务都会接受相同参数：某些模型不接受 `temperature`，可留空不发送；如果服务只支持原生 Messages / Responses，应先使用你自己的兼容网关。本版不自动处理网页订阅账号、Cookie、厂商原生多种协议。

## 4. 润色怎样运行

不是先机械删词，而是：

`正文 → 剧情/连续性审稿 → 有限修订 → 原稿留档 → 小说风格润色＋可选Humanizer规则 → 前后事实对照 → 再审稿＋确定性检查 → 接受或回退 → 更新小说记忆。`

润色检查人物声音、模板式铺垫、重复总结、空泛比喻与机械句式；不盲删正常排比、文言、停顿或人物口癖。不乱加错字，不新增关键线索来“显得具体”。如果保真或质量门未通过，润色稿作为 `polished-rejected` 留档，正文继续采用上一版过关稿。

完整版本可在“正文与版本”查看。没有通过内容质量门的章节不会伪装成已完成。

## 5. 预算、暂停与恢复

- 预算按 **token** 设置，不冒充人民币费用。输入、输出价格取决于你的模型供应商。
- 有供应商 `usage` 时记录其输入/输出；缺失、超时或请求中断时保守按请求预留量记账，并明确标记 `uncertain`。
- 未知 tokenizer 时用 UTF-8 字节数加封装余量作为保守输入估计，因此可能比真实 token 数明显高。可在预算页区分实际报告值和保守计入值。
- 每次网络请求先预留额度。接口错误的响应文本不写入页面，避免供应商回显密钥。
- 已验证步骤有持久化产物和成功响应缓存；服务重启会恢复队列。不确定请求可能被供应商处理过，重试可能再次收费，系统不会宣称“完全不重复计费”。
- 修改模型配置后，正在运行的任务保持旧快照。暂停后重新开始使用新模型配置，已完成阶段不重写。
- 增加预算不会把已有消耗清零。增加修订上限后可继续处理被质量门拦下的章节。
- 暂停/取消在当前请求结束后的安全点生效，不会扔掉已收到的有效结果。需要保持服务和电脑运行；关闭电脑不是云端继续执行。

## 6. 部署与开发文档

- [部署与备份](docs/DEPLOYMENT.md)
- [架构、数据流、API与恢复语义](docs/ARCHITECTURE.md)
- [上游项目集成与许可证](docs/UPSTREAM.md)
- [已实现 / 未完成 / 未验证](docs/STATUS.md)
- [后续本地开发任务](docs/DEVELOPMENT.md)
- [测试记录](docs/TEST_REPORT.md)

```bash
python -m pip install -r requirements-dev.txt
python -m pytest
node --check app/static/app.js  # 可选，仅开发时需要Node
```

## 数据与隐私

所有数据默认放在 `data/`。`master.key` 用于加密 API 密钥，必须和数据库一起备份；遗失后不能解密已有Key。服务端会向所配置的模型发送研究资料、草稿和必要上下文。不要把API地址指向不可信服务。

项目导出的 ZIP 不包含模型Key、会话或完整参考原文。仓库忽略 `.env`、数据目录、数据库和密钥。当前定位为单用户自用，不能直接当多人公网商业服务部署。

## 许可证

本仓库原创代码采用 MIT。第三方规则文件保留原作者的 MIT 许可证与固定提交信息。它们的软件许可证不等于参考作品内容的使用许可；本版不处理投稿规则。
