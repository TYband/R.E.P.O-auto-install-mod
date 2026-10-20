#!/usr/bin/env bash
#
# R.E.P.O. Mod 云端同步 —— 服务器一键部署
#
#   sudo bash deploy.sh
#
# 部署形态：
#   uvicorn 只听 127.0.0.1:8000（不直接对外）
#   nginx 在已有的 8080 server 块里加一个 location /api/ 反代过去
#   对外地址：http://<公网IP>:8080/api/
#
# 为什么用 8080：这台机器 80/443 已经被 docker 里的 openresty 占了，
# 8080 是宿主机 nginx，且云安全组已经放行，复用它最省事。
#
# 反复执行安全（幂等）。
#
set -euo pipefail

APP_DIR=/opt/repo-mod-api
APP_USER="${SUDO_USER:-ubuntu}"
APP_PORT=8000            # uvicorn 本地端口
API_PORT="${API_PORT:-8080}"   # 对外端口
PUBLIC_IP="${PUBLIC_IP:-150.230.169.157}"
SNIPPET=/etc/nginx/snippets/repo-mod-api.conf

say()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m    ✓ %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m    ! %s\033[0m\n' "$*"; }

[ "$(id -u)" = "0" ] || { echo "请用 sudo 运行"; exit 1; }
HERE="$(cd "$(dirname "$0")" && pwd)"

# ------------------------------------------------------------
say "1/6 准备目录与 Python 环境"
# ------------------------------------------------------------
mkdir -p "$APP_DIR/data"
cp -f "$HERE/app.py"           "$APP_DIR/app.py"
cp -f "$HERE/requirements.txt" "$APP_DIR/requirements.txt"

if [ ! -d "$APP_DIR/venv" ]; then
    python3 -m venv "$APP_DIR/venv"
fi
"$APP_DIR/venv/bin/pip" install -q --upgrade pip
"$APP_DIR/venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"
ok "$("$APP_DIR/venv/bin/python" -c 'import fastapi,uvicorn;print("fastapi",fastapi.__version__,"uvicorn",uvicorn.__version__)')"

chown -R "$APP_USER:$APP_USER" "$APP_DIR"

# ------------------------------------------------------------
say "2/6 安装 systemd 服务"
# ------------------------------------------------------------
sed -e "s/^User=.*/User=$APP_USER/" \
    -e "s/^Group=.*/Group=$APP_USER/" \
    -e "s#--port 8000#--port $APP_PORT#" \
    "$HERE/repo-mod-api.service" > /etc/systemd/system/repo-mod-api.service

touch /var/log/repo-mod-api.log
chown "$APP_USER:$APP_USER" /var/log/repo-mod-api.log

systemctl daemon-reload
systemctl enable repo-mod-api >/dev/null 2>&1 || true
systemctl restart repo-mod-api
sleep 3

if systemctl is-active --quiet repo-mod-api; then
    ok "服务已启动（127.0.0.1:$APP_PORT）"
else
    warn "服务没起来，最近日志："
    tail -25 /var/log/repo-mod-api.log || true
    exit 1
fi

# ------------------------------------------------------------
say "3/6 本机自检"
# ------------------------------------------------------------
for i in $(seq 1 20); do
    if curl -fsS "http://127.0.0.1:$APP_PORT/api/health" >/tmp/_h.json 2>/dev/null; then
        ok "健康检查：$(cat /tmp/_h.json)"
        break
    fi
    if [ "$i" = "20" ]; then
        warn "127.0.0.1:$APP_PORT 无响应"
        tail -30 /var/log/repo-mod-api.log
        exit 1
    fi
    sleep 1
done

# ------------------------------------------------------------
say "4/6 配置反向代理（nginx $API_PORT → $APP_PORT）"
# ------------------------------------------------------------
mkdir -p /etc/nginx/snippets
cat > "$SNIPPET" <<NGINX
# R.E.P.O. Mod 云端同步 API（由 deploy.sh 生成，可重复覆盖）
location /api/ {
    proxy_pass         http://127.0.0.1:$APP_PORT;
    proxy_http_version 1.1;
    proxy_set_header   Host              \$host;
    proxy_set_header   X-Real-IP         \$remote_addr;
    proxy_set_header   X-Forwarded-For   \$proxy_add_x_forwarded_for;
    proxy_set_header   X-Forwarded-Proto \$scheme;
    proxy_read_timeout 30s;
    client_max_body_size 4m;
}
NGINX
ok "已写入 $SNIPPET"

# 找到监听 $API_PORT 的站点文件，把 include 塞进它的 server 块
SITE=""
for f in /etc/nginx/sites-enabled/* /etc/nginx/conf.d/*.conf; do
    [ -f "$f" ] || continue
    if grep -qE "^[[:space:]]*listen[[:space:]]+(\[::\]:)?$API_PORT[[:space:]]*;" "$f"; then
        SITE="$f"; break
    fi
done

if [ -z "$SITE" ]; then
    warn "没找到监听 $API_PORT 的站点，新建一个"
    cat > /etc/nginx/conf.d/repo-mod-api.conf <<NGINX
server {
    listen $API_PORT;
    server_name $PUBLIC_IP;
    include /etc/nginx/snippets/repo-mod-api.conf;
}
NGINX
    ok "已新建 /etc/nginx/conf.d/repo-mod-api.conf"
else
    REAL=$(readlink -f "$SITE")
    ok "目标站点：$SITE → $REAL"
    if grep -q "repo-mod-api.conf" "$REAL"; then
        ok "已经 include 过，跳过"
    else
        cp -f "$REAL" "$REAL.bak.$(date +%s)"
        # 只在「第一条」listen $API_PORT; 之后插一次。
        # 不能直接用 sed /a：默认站点里还有 listen [::]:8080;，会插两遍，
        # 而同一个 server 块里出现两个 location /api/ 会让 nginx 直接报 duplicate。
        awk -v port="$API_PORT" -v inc="    include $SNIPPET;" '
            !done && $0 ~ ("^[[:space:]]*listen[[:space:]]+" port "[[:space:]]*;") {
                print; print inc; done = 1; next
            }
            { print }
        ' "$REAL" > "$REAL.new" && mv "$REAL.new" "$REAL"
        chmod 644 "$REAL"
        ok "已插入 include（原文件已备份）"
    fi
fi

if nginx -t 2>/tmp/_ngx.log; then
    systemctl reload nginx
    ok "nginx 配置通过并已重载"
else
    warn "nginx 配置校验失败，正在回滚："
    cat /tmp/_ngx.log
    set +e
    if [ -n "${REAL:-}" ]; then
        LATEST=$(ls -t "$REAL".bak.* 2>/dev/null | head -1)
        if [ -n "$LATEST" ]; then
            cp -f "$LATEST" "$REAL"
            ok "已还原 $REAL"
        fi
    fi
    nginx -t >/dev/null 2>&1 && systemctl reload nginx
    set -e
    exit 1
fi

# ------------------------------------------------------------
say "5/6 防火墙"
# ------------------------------------------------------------
if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then
    ufw allow "$API_PORT"/tcp >/dev/null 2>&1 || true
    ok "ufw 已放行 $API_PORT"
else
    ok "ufw 未启用，跳过（云安全组已放行 $API_PORT）"
fi

# ------------------------------------------------------------
say "6/6 公网自检"
# ------------------------------------------------------------
URL="http://$PUBLIC_IP:$API_PORT/api/health"
sleep 1
if curl -fsS --max-time 12 "$URL"; then
    echo
    ok "部署完成"
    echo "    接口地址：http://$PUBLIC_IP:$API_PORT/api/"
    echo "    接口文档：http://$PUBLIC_IP:$API_PORT/api/docs"
else
    warn "公网地址暂时调不通：$URL"
    warn "若本机 curl 能通，则是云安全组没放行 TCP $API_PORT"
fi

echo
echo "常用命令："
echo "  systemctl status repo-mod-api"
echo "  journalctl -u repo-mod-api -f"
echo "  tail -f /var/log/repo-mod-api.log"
echo "  nginx -t && systemctl reload nginx"
