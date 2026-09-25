# BizOrch Linux / Docker 部署操作手册

这套文件用于把 BizOrch 部署到 **2 核 2GB Linux 服务器**。生产环境复用服务器已有的 MySQL 8.0.45、Redis 7.2.12 和 Nginx，只容器化 BizOrch API、模拟企业系统、MCP 与知识索引 Worker；评测 Worker 按需启动。

```text
浏览器 -> 宿主机 Nginx (80/443)
        -> React 静态文件 (/var/www/bizorch/current)
        -> /api/ -> 127.0.0.1:18000 -> BizOrch API
                                           -> MCP -> 模拟企业系统
                                           -> 宿主机 MySQL / Redis
```

只有 API 发布到宿主机回环地址。MCP 与模拟企业系统只存在于内部网络，知识和评测 Worker 无法连接 MCP 写入口。

### 当前线上发布（2026-09-24）

阿里云首尔服务器已运行 `2026.09.24-1`：API、MCP 和知识索引 Worker 使用此版镜像；模拟企业系统沿用 `2026.07.28-1`。实际部署根目录是 `/opt/bizorch`，前端版本目录是 `/www/wwwroot/bizorch.nexmart.tech/releases/2026.09.24-1`，`current` 软链接指向该版本。公开入口为 `https://bizorch.nexmart.tech`。发布前备份保存在 `/opt/bizorch/backups/pre-2026.09.24-1`，包含两套 MySQL 数据库、旧配置与停止写入后归档的 Chroma、checkpoint 和上传文件。

线上 API、MCP、企业模拟系统健康检查通过；前端和公开 API 返回 200；普通员工访问人工核对接口返回 403，运营账号返回 200；容器内挂载目录可读写。空闲观测约有 490 MiB 可用内存，1 GiB swap 已用满。知识入库与完整 Agent 流程的资源峰值、容器重启后的状态恢复、实际切换旧版本回滚仍待验证。该演示站公开展示六个演示身份并自动填入保留的旧统一密码 `123456`，仅承载模拟业务数据。新建或重置演示凭证时，种子脚本仍要求至少 8 位密码；本次发布未重置已有账号。

## 0. 部署前条件

- Linux 已安装 Docker Engine 与 Docker Compose v2；
- MySQL 已创建 `bizorch` 与 `bizorch_enterprise` 数据库和最小权限账号；
- Nginx 可读取 `/var/www/bizorch/current` 并代理到 `127.0.0.1:18000`；
- 构建机或 CI 与服务器 CPU 架构一致。脚本默认构建 `linux/amd64`；
- 三个镜像已推送到服务器可访问的镜像仓库，或已通过 `docker save` / `docker load` 导入服务器。
- Docker 数据目录建议至少保留 **5GB** 可用磁盘空间，用于约 1.6GB 的三张运行镜像、基础层、日志与后续版本回滚；实际值以 `docker system df` 为准。

不要把 `.env`、API Key、数据库密码或 OSS AccessKey 打进镜像、提交到 Git，或发送到聊天记录。

## 1. 准备服务器目录和配置

把 `compose.yaml`、`production.env.example`、`nginx/` 和 `scripts/` 复制到服务器：

```bash
sudo install -d -m 0750 /opt/bizorch
sudo cp compose.yaml /opt/bizorch/compose.yaml
sudo cp production.env.example /opt/bizorch/.env
sudo cp -R scripts /opt/bizorch/scripts
sudo chmod 750 /opt/bizorch/scripts/*.sh
sudo /opt/bizorch/scripts/init-host.sh /opt/bizorch
sudo chmod 600 /opt/bizorch/.env
sudoedit /opt/bizorch/.env
sudo /opt/bizorch/scripts/check-runtime-env.sh /opt/bizorch/.env
```

必须替换两个数据库连接、内部服务令牌、DeepSeek/百炼 Key、OSS 配置和三个不可变镜像标签。不要使用 `latest`。

Compose 读取同一个 `/opt/bizorch/.env` 做变量插值，但 `compose.yaml` 会按进程显式传入最小配置子集：知识 Worker 不获得模型推理、OSS 或内部写令牌；离线评测不获得外部 API Key；API 不获得模拟企业系统内部令牌。

数据库密码中的 `@`、`:`、`/`、`%` 等字符必须先进行 URL 编码。若值含 `$`，还要按 Docker Compose 环境文件语法正确转义。配置检查失败时不要临时放宽文件权限或把密钥打印到日志。

数据库在宿主机时，容器通过 `host.docker.internal` 访问。Linux Compose 已使用 `host-gateway` 显式映射该名称。若 MySQL/Redis 只监听 `127.0.0.1`，需允许 Docker bridge 网段访问或使用宿主机 bridge 网关地址；不要把数据库端口暴露到公网。

### 首次迁移已有本地数据

如果 MySQL 已经是当前 BizOrch 使用的远程数据库，首次上服务器前还要迁移本地的非 MySQL 持久化数据。先停止本地 API 与知识 Worker，复制 `data/chroma/`、`data/uploads/` 与 `data/checkpoints/` 的内容到服务器对应的 `/opt/bizorch/data/` 子目录，再启动 Compose。不要复制 `.env`、日志或 Windows 虚拟环境。

这一步保证“数据库里的知识文档”和“Chroma 里的向量索引”一起迁移，也保留尚未完成的 LangGraph checkpoint。若选择不迁移 Chroma，必须在服务器上以知识运营账号逐份重新建立索引，并在验收中记录这一事实。

## 2. 构建和交付

在有 Docker 的开发机或 CI 中，从仓库根目录运行：

```bash
./deploy/scripts/build-images.sh 2026.07.28-1
./deploy/scripts/publish-frontend.sh /var/www/bizorch/releases 2026.07.28-1
```

第一个脚本构建 API、模拟企业系统和 MCP 三个 Linux 镜像。将镜像推送到私有仓库，或用 `docker save` / `docker load` 传到服务器。生产 Compose 只有 `image`，没有 `build`；`deploy.sh` 使用 `--no-build`，不会在 2GB 服务器临时安装编译依赖。

第二个脚本导出 React 静态文件。把产物放入 `/var/www/bizorch/releases/<release-id>/`，再将 `current` 符号链接切到该版本。生产机不运行 Vite。

正式构建默认不显示演示账号及统一密码。仅在隔离的公开演示环境确实需要时，显式设置 `BIZORCH_PUBLIC_DEMO_MODE=true` 和 `BIZORCH_PUBLIC_DEMO_PASSWORD` 后运行前端发布脚本；该密码会出现在公开页面，不能用于任何真实身份。后端演示账号脚本在非开发环境需要 `--allow-production-demo-seed` 和不少于 8 位的 `BIZORCH_DEMO_PASSWORD`，且只有另加 `--reset-existing-credentials` 才会重置已存在账号及会话。

如果从当前 Windows 开发机通过文件上传交付，而不是使用镜像仓库，在本地镜像和前端产物均验证通过后运行：

```powershell
& '.\deploy\scripts\export-windows-release.ps1' `
  -ReleaseId '2026.07.28-1' `
  -ConfirmDataWritersStopped
```

运行前必须先停止本地 BizOrch API 与知识索引 Worker；该确认开关防止在 SQLite checkpoint 或 Chroma 仍被写入时制作不一致快照。

脚本会自动寻找 Docker Desktop CLI，并在 `.delivery/<release-id>/` 生成：

- `bizorch-images-<release-id>.tar`：三张 `linux/amd64` 运行镜像；
- `bizorch-deploy-<release-id>.tar.gz`：Compose、Nginx、Linux 脚本与 React 静态文件；
- `bizorch-data-<release-id>.tar.gz`：Chroma、知识原文件与 checkpoint；
- `SHA256SUMS`：三个上传文件的完整性校验值。

发布包通过显式白名单复制文件，不包含本地 `.env`、Windows E2E Compose、虚拟环境和日志。`production.env.example` 只是无密钥模板；服务器上的 `/opt/bizorch/.env` 必须在 Linux 内单独创建，不能用开发机 `.env` 覆盖。

上传时将三个归档文件和 `SHA256SUMS` 放到服务器临时目录，例如 `/tmp/bizorch-release/`。校验哈希并执行 `docker load` 后，再把部署包和数据包分别展开到受控目录。未完成本地打包校验前不要提前上传半成品。

### Windows Docker Desktop 本地联调

Windows NTFS 绑定目录不保证 SQLite 所需的文件锁语义。本地 Compose E2E 必须附加 `compose.windows-e2e.yaml`，让 Chroma、checkpoint、上传和日志使用 Docker 命名卷；它只用于本机验证，Linux 生产部署仍只使用 `compose.yaml` 的宿主机持久化目录。

本机完成镜像构建后，使用同一份开发 `.env` 和生产镜像标签启动 E2E；Windows PowerShell 中需额外设置数据目录、日志目录和三张镜像标签，再执行以下等价 Compose 命令：

```powershell
docker compose --env-file .env `
  -f deploy/compose.yaml `
  -f deploy/compose.windows-e2e.yaml up -d --no-build
```

此命令仅发布回环 API 端口 `127.0.0.1:18000`。本机 E2E 结束后可以执行同一组文件的 `down`；不要附加 `--volumes`，除非确认不再需要本机 E2E 数据。

## 3. 启动、查看与停止

```bash
sudo /opt/bizorch/scripts/deploy.sh /opt/bizorch
sudo /opt/bizorch/scripts/show-status.sh /opt/bizorch
sudo /opt/bizorch/scripts/health-check.sh 18000
```

`deploy.sh` 会尝试从镜像仓库拉取；若镜像是提前用 `docker load` 导入的本地标签，拉取失败不会阻止使用本地镜像启动。

离线合约评测按需运行：

```bash
cd /opt/bizorch
docker compose --env-file .env --profile evaluation run --rm evaluation-contract
```

在线只读评测会调用外部模型和嵌入服务，只能在人工确认额度后运行：

```bash
cd /opt/bizorch
docker compose --env-file .env --profile evaluation-live run --rm evaluation-live
```

停止业务容器但保留宿主机数据：

```bash
cd /opt/bizorch
docker compose --env-file .env down
```

不要使用 `down --volumes`，也不要删除 `/opt/bizorch/data` 和 `/opt/bizorch/logs`。

## 4. Nginx 与发布验证

安装 `nginx/bizorch.conf`，将 `server_name _;` 改成服务器 IP 或域名，然后执行：

```bash
sudo nginx -t
sudo systemctl reload nginx
curl -fsS http://127.0.0.1:18000/api/v1/health
```

第一次可使用 HTTP + IP 验收，准备域名后再配置 TLS。最小验收闭环包括：登录、一个完整场景申请与审批、知识检索、OSS 头像、离线评测，以及容器重启后的工作流和索引恢复。

## 5. 回滚

保留上一版三个镜像标签与前端 release。新版本异常时：

1. 备份当前日志与数据库；
2. 将 `/opt/bizorch/.env` 中三个镜像标签改回上一版；
3. 重新执行 `deploy.sh`；
4. 将 Nginx 的 `current` 软链接切回上一前端 release；
5. 完成健康检查和关键只读验证。

镜像回滚不等于数据库降级。默认不自动执行 `alembic downgrade`；有破坏性迁移时必须使用经过验证的数据库恢复方案。

详见 [部署蓝图](../docs/V7_LINUX_DOCKER_DEPLOYMENT_PLAN.md) 与 [容器化学习文档](../MarkWord/deployment/ContainerizedDeployment.md)。
