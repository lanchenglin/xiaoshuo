# 开发约束

本项目是独立的AI小说平台，与用户的视频/M16/M17项目分开。
当前不做投稿、自动发布或网站政策分析。
阅读 docs/STATUS.md 与 docs/ARCHITECTURE.md 后再开发。

- 保持单书权威设定和已验收章节，保留所有修订版本。
- 只迁移参考作品的抽象写作机制，不做逐章换名拼接。
- 去模板化以文笔与保真为目标，不承诺AI检测器结果。
- 不把模拟模式误标为真实模型成功。
- 禁止提交data、.env、密钥、数据库、真实参考小说全文。
- 提交前运行 `python -m pytest`、`python -m compileall -q app`、`node --check app/static/app.js`。
- 不删除/静默替换 third_party 许可证；更新固定规则须审查并更新hash。
- 新增付费API调用必须有预算、失败状态、有限重试和日志脱敏。
- 未完成/未测试功能写进docs/STATUS.md，不用README空口宣称已支持。
