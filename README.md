# Adobe2API Manager

用于集中监控和管理多个 Adobe2API 实例的独立控制面。

## 功能

- 30 秒并发采集实例状态，SQLite 保留 7 天指标。
- 统一查看实例、Token、积分、刷新配置、运行配置和请求日志。
- 离线、延迟、错误率、Token、积分和刷新失败告警。
- 通用 JSON Webhook、SMTP 邮件、告警静默和操作审计。
- 详细日志按需读取，不复制 Prompt、Token 或 Cookie 到中心数据库。

## 1. 升级 Adobe2API 实例

在每台 Adobe2API 服务上设置相同的运维密钥：

```env
ADOBE2API_OPS_KEY=replace-with-a-long-operations-key
```

实例必须通过 HTTPS 提供服务。验证接口：

```bash
curl https://adobe-instance.example.com/api/v1/ops/snapshot \
  -H "X-Adobe2API-Ops-Key: replace-with-a-long-operations-key"
```

## 2. Docker Compose 部署中心

```bash
cp .env.example .env
# 编辑 .env，至少填写 MANAGER_ACCESS_KEY 和 ADOBE2API_OPS_KEY
docker compose up -d --build
```

打开 `http://SERVER:8000`，输入 `MANAGER_ACCESS_KEY`。公网部署使用 Caddy 或 Nginx 提供 HTTPS；Caddy 示例位于 `deploy/Caddyfile.example`。

## 3. 登记实例

进入“实例”，依次填写三台服务的名称、位置和 HTTPS 地址。添加后使用烧瓶图标测试 Ops API，再点击“立即采集”。

实例登记表单不需要填写子平台管理员账号或密码。中心使用环境变量 `ADOBE2API_OPS_KEY` 自动访问三台实例；该值必须与每台 Adobe2API 上配置的值完全一致。修改共享密钥时，需要同时更新中心和三台实例并重启服务。

总览的每个实例会分别显示当前进行中任务、今日成功请求和今日失败请求；错误率与 P95 耗时使用最近 5 分钟窗口。

## 通知配置

通用 Webhook 请求体：

```json
{
  "event": "alert",
  "state": "firing",
  "severity": "critical",
  "rule_id": "instance_offline",
  "rule_name": "Instance offline",
  "message": "Instance is unreachable",
  "timestamp": 1784736000,
  "instance": {
    "id": "INSTANCE_ID",
    "name": "East",
    "location": "Tokyo",
    "base_url": "https://east.example.com"
  }
}
```

Webhook 地址通过 `ALERT_WEBHOOK_URLS` 配置，多个地址使用逗号分隔。SMTP 使用 `ALERT_SMTP_*` 环境变量配置。控制台“系统设置”可发送测试通知。

## 本地开发

后端：

```bash
python -m venv .venv
.venv/Scripts/pip install -r backend/requirements-dev.txt
cd backend
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

前端：

```bash
cd frontend
npm install
npm run dev
```

本地 HTTP 调试时设置 `MANAGER_COOKIE_SECURE=false`。

## 测试

```bash
cd backend
../../adobe2api-manager/.venv/Scripts/python -m pytest -q

cd ../frontend
npm run build
```

## 数据与认证

- `MANAGER_ACCESS_KEY` 只用于登录中心页面。
- `ADOBE2API_OPS_KEY` 只用于中心访问实例，三台实例可共用。
- 密钥和通知凭据只从环境变量读取，不写入 SQLite。
- SQLite 数据位于 `data/manager.db`，包括实例、指标、告警、静默和审计。
