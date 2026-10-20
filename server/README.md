# 云同步后端

给 R.E.P.O. 一键装 Mod 提供账号和 Mod 列表同步。单文件 FastAPI + SQLite，
1 核 1G 的小机器就能跑。

```
客户端 ──HTTP──> nginx :8080 ──> uvicorn 127.0.0.1:8000 ──> SQLite
```

## 部署

```bash
# 本地：把 server/ 传上去
scp -r server/ ubuntu@<你的服务器>:/tmp/repo-deploy/

# 服务器：一键部署（可重复执行）
ssh ubuntu@<你的服务器> "cd /tmp/repo-deploy && sudo bash deploy.sh"
```

`deploy.sh` 会做这些事：

1. 建 `/opt/repo-mod-api` 和 venv，装 `fastapi` / `uvicorn`
2. 写 systemd 服务 `repo-mod-api`，只监听 `127.0.0.1:8000`
3. 在监听 8080 的 nginx 站点里插入 `location /api/` 反代
4. 探测 ufw，必要时放行
5. 从公网地址调一次 `/api/health` 自检

想换对外端口：`sudo API_PORT=8090 bash deploy.sh`。

## 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | 健康检查，不需要登录 |
| POST | `/api/register` | `{username, password}` → `{token, user}` |
| POST | `/api/login` | 同上 |
| POST | `/api/logout` | 吊销当前令牌 |
| GET | `/api/me` | 当前账号 + 云端 Mod 数 |
| POST | `/api/privacy` | `{public: bool}` 公开 / 私密 |
| PUT | `/api/mods` | `{mods: [{full_name, version, name}], note}` |
| GET | `/api/mods` | 拉自己的列表 |
| DELETE | `/api/mods` | 清空 |
| GET | `/api/users/search?q=&limit=` | 按用户名搜玩家 |
| GET | `/api/users/{username}` | 看某个玩家的列表 |

除 `/api/health`、`/api/register`、`/api/login` 外都要带
`Authorization: Bearer <token>`。

交互式文档：`http://<服务器>:8080/api/docs`

## 数据

SQLite 在 `/opt/repo-mod-api/data/app.db`，三张表：`users` / `tokens` / `modlists`。

```bash
# 备份
sudo cp /opt/repo-mod-api/data/app.db ~/app-$(date +%F).db

# 看有哪些账号
sqlite3 /opt/repo-mod-api/data/app.db "select username, created_at from users;"

# 删掉某个账号（令牌和列表会一起级联删除）
sqlite3 /opt/repo-mod-api/data/app.db "delete from users where username='xxx';"
```

## 安全说明

- 密码：PBKDF2-HMAC-SHA256，20 万轮，每个账号独立盐
- 令牌：32 字节随机串，存库可吊销，180 天不用自动失效
- 限流：注册 12 次/小时/IP，登录 60 次/小时/IP（网吧/宿舍常共用出口 IP，登录放宽了些）
- 令牌：每个账号只保留最近 10 个，多的自动清掉
- 服务只监听 `127.0.0.1`，对外只经 nginx
- 当前是 **HTTP**，密码在网络上是明文传输的。要长期公开使用，建议加一层
  TLS（`certbot --nginx` 或 Cloudflare 代理）

## 维护

```bash
systemctl status repo-mod-api
journalctl -u repo-mod-api -f
tail -f /var/log/repo-mod-api.log
nginx -t && systemctl reload nginx
```

改了 `app.py` 之后重新执行一次 `deploy.sh` 即可（会覆盖 `/opt/repo-mod-api/app.py`
并重启服务，数据库不动）。
