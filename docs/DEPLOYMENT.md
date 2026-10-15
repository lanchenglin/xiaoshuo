# 部署、更新与备份

## 运行方式

推荐先在 Windows / WSL / Linux 原生 Python 环境验证模型。生产常驻可以用 Docker Compose 或 systemd，单进程即可。只需 CPU/内存运行服务，实际文本生成走配置的模型 API；不需要本地训练。

`run.py` 默认 `127.0.0.1:8765`。原生启动**不会自动加载 .env 文件**，`.env` 默认仅由 Docker Compose读取。原生模式可设置环境变量：

PowerShell：
```powershell
$env:APP_PASSWORD = '你自己的至少12位密码'
$env:HOST = '127.0.0.1'
$env:PORT = '8765'
python run.py
```

Linux：
```bash
export APP_PASSWORD='你自己的至少12位密码'
export HOST=127.0.0.1
export PORT=8765
python run.py
```

不指定密码时，读取或首次创建 `data/initial-password.txt`。更换 `APP_PASSWORD` 后重启会使旧会话失效；该密码不是模型Key。

## 远程访问

本版是单用户工具。建议用 SSH 隧道或可信私网，**不要直接把默认HTTP口开放到互联网**：

```bash
ssh -L 8765:127.0.0.1:8765 user@server
```

确需反向代理时，使用 HTTPS，在部署环境设置：

```text
COOKIE_SECURE=1
PUBLIC_ORIGIN=https://你的工作台域名
```

代理必须保留 Host，添加普通HTTP超时和请求体限制；API不信任任意转发头，不启用跨域。请先自行做公网加固和访问控制验收，本次未完成真实域名、TLS或多人渗透测试。

如果你将服务绑定到局域网IP，请保证路由器和防火墙仅放行可信设备。模型 API 的 localhost 是**服务运行所在环境**：容器的 localhost 不等于 Windows 宿主。Docker Desktop 可按实际网络使用宿主网关；具体连通性需要你的机器测试。

## 单实例约束

不要设置 uvicorn `--workers 2`，不要两个容器共用一个数据卷。跨平台文件锁会阻止第二个worker使用同一数据目录。多进程扩展需要独立队列、租约与数据库迁移，本版未实现。

## 更新

原生：停止服务，备份 `data/`，`git pull --ff-only`，安装依赖后重新启动。

Docker：
```bash
git pull --ff-only
docker compose up -d --build
```

不使用 `down -v`。此命令会删除数据卷。

## 备份和恢复

**最简单可靠的方式是停止服务后完整备份数据目录/卷**，不是只拷贝正在写入的SQLite主文件。SQLite还有WAL/SHM；不要遗漏。

原生 Linux示例（服务已停止）：
```bash
tar -czf novel-data-backup.tar.gz data
```

至少保留：`studio.sqlite3`及相关WAL（如存在）、`master.key`、需要的初始密码文件。备份包含可解密的Key，必须按敏感资料管理。

Docker停止后，示例备份命名卷（用实际卷名替换；`docker volume ls`查看）：
```bash
docker compose stop
mkdir -p backups
docker run --rm -v xiaoshuo_novel_data:/data:ro -v "$PWD/backups:/backup" alpine sh -c 'tar -czf /backup/data.tar.gz -C /data .'
docker compose start
```

该辅助容器镜像需要能拉取。请不要将密码、master.key或数据库上传到公开仓库。

恢复时停止服务，把完整数据放回原目录/卷，确保UID10001有权限，再启动。不要删除master.key来“修复”解密错误；这会使旧密钥不可恢复。

## 常见问题

- **API 401/403**：检查Key、模型权限、基地址，不把供应商错误体贴到公开仓库。
- **404**：通常是接口路径或模型不符。基地址会拼接 `/chat/completions`。
- **输出被截断**：提高单次输出上限、减小每章目标，或使用支持足够输出长度的模型。当前章节未验收，已有稿件保留。
- **预算暂停**：查看计入量/未知计费量，提高总上限后继续。保守估计可能高于账单。
- **质量阻塞**：查看草稿报告，提高有限修订次数后继续；最高8轮，不能无限刷。
- **抓取失败**：参考资料页显示原因。本版不绕过验证码、登录和付费限制；可新建项目导入已取得的TXT。
- **模型支持Messages/Responses而非Chat Completions**：通过已验证的兼容网关接入，原生协议适配尚未实现。
- **规则文件缺失**：从完整Git仓库获取，运行 `python scripts/verify_vendor.py`；启用Humanizer但文件缺失时会明确报错，而不是假装已运行。
