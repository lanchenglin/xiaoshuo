> v0.2补充：搜索、整本下载、独立下载器和全文分析已经新增。当前方案/部署/API见 [SEARCH_DOWNLOAD_PLAN.md](SEARCH_DOWNLOAD_PLAN.md)，实际完成范围以 [STATUS.md](STATUS.md) 为准。下面保留原有模块说明，旧版待办不作为新增模块的验收结论。

# 实际集成情况与第三方来源

核对日期：2026-10-15。不能把“参考了项目设计”和“完整接入项目运行时”混为一谈。

## Humanizer-zh：真实内置

- 仓库：https://github.com/op7418/Humanizer-zh
- 固定提交：`f4518a8eab97b8bfebc66a89d34320a89bef6930`
- 内置文件：`third_party/humanizer-zh/SKILL.md` 与 `LICENSE`。
- 许可证：MIT，原版权标识保留。
- 调用位置：`NovelEngine.write_chapter()` 的润色步骤；勾选Humanizer时加载原规则，作为小说专用保真约束之后的编辑参考。
- 不执行SKILL中的文件操作工具；平台模型节点只有文本输入输出，没有文件/浏览器工具能力。
- 上游本身明确是编辑指导，不能证明作者身份或保证检测结果。本平台不以“绕过检测”为验收标准。
- 默认固定文件，不启动时在线自动更新。更新必须审查差异、保留LICENSE、更新锁定哈希并重跑测试。

## Oh Story：参考方法，未接入其Agent运行时

- 仓库：https://github.com/zenstory-ai/oh-story-claudecode
- 参考方向：分段拆书、按证据分析、跨书抽象机制聚合、原创规划、写作审稿分工。
- 本仓库独立实现这些流程，没有复制或运行其完整skill工作区、hook与CLI工具链，也没有假称官方adapter已完成。
- 后续如需真正把它接为研究后端，应采用显式adapter：平台给输入文件及任务manifest，上游在隔离工作目录输出结构化分析，再经过证据/范围校验进入平台；不得同时维护两份小说权威设定。

## Biz Novel Studio：参考产品分工，未整库合并

- 仓库：https://github.com/ExplosiveCoderflome/AI-Novel-Writing-Assistant
- 参考方向：参考创作而非续写原作、自动导演、章节进度、审稿与版本管理。
- 本仓库没有复制其源码、打包其服务或调用未经核实的API。
- 这与前期“以Biz主程序整合”的设想不同：实际初版采用独立轻量网页平台，避免同时运行两套任务队列、资料库、权限和编辑器。用户所需自动工作流在本仓库原生实现，但不是上游能力的完整替代品。
- 若后续决定迁入其主程序，需要先核对上游当时的许可证、架构和接口，不能把AGPL或附加声明的代码直接标为MIT。

## 本项目自编部分

`app/prompts/base.md`、`app/prompts/polish.md`以及所有Python/前端业务代码为本次开发。去模板化检查只提供可审阅线索，不因为一个词、标点或句式出现就强行判定为AI文本。
