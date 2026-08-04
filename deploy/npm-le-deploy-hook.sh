#!/bin/bash
# ══════════════════════════════════════════════════════════════════════════
# Neckline V2-⑯-G:把 nk.linotsai.top 的 LE 证书投递进 nginx-proxy-manager 容器
# 仓库副本:Neckline/deploy/npm-le-deploy-hook.sh
# ══════════════════════════════════════════════════════════════════════════
# 为什么要拷而不是挂载:容器的 /etc/letsencrypt 已被 bind 到 /opt/npm/letsencrypt
# (NPM 自己的 LE 库),宿主 certbot 的 /etc/letsencrypt 容器里看不见。拷进
# /opt/npm/data/custom-certs/nk/(= 容器 /data/custom-certs/nk/)是最小侵入:
# ⛔ 不写 NPM 的 LE 库、⛔ 不碰 NPM 的 database.sqlite、⛔ 不动它 custom_ssl/npm-*。
#
# certbot 的 deploy hook 是**全局**的(每张续期证书各跑一次),故必须按
# RENEWED_LINEAGE 判本域再动手;手动执行时该变量为空,回落到本域路径。
set -euo pipefail

DOMAIN="nk.linotsai.top"
LIVE="/etc/letsencrypt/live/${DOMAIN}"
DEST="/opt/npm/data/custom-certs/nk"
CONTAINER="nginx-proxy-manager"
LOG="/var/log/neckline-nk-cert-deploy.log"

log() { echo "[$(date -Is)] $*" | tee -a "$LOG"; }

if [ "${RENEWED_LINEAGE:-$LIVE}" != "$LIVE" ]; then
    exit 0   # 别的证书续期,与我无关
fi

[ -r "${LIVE}/fullchain.pem" ] || { log "ERROR 找不到 ${LIVE}/fullchain.pem"; exit 1; }
[ -r "${LIVE}/privkey.pem" ]   || { log "ERROR 找不到 ${LIVE}/privkey.pem"; exit 1; }

install -d -m 755 "$DEST"
install -m 644 "${LIVE}/fullchain.pem" "${DEST}/.fullchain.pem.new"
install -m 600 "${LIVE}/privkey.pem"   "${DEST}/.privkey.pem.new"
mv -f "${DEST}/.fullchain.pem.new" "${DEST}/fullchain.pem"   # 同盘 rename,原子
mv -f "${DEST}/.privkey.pem.new"   "${DEST}/privkey.pem"
log "证书已投递 → ${DEST} ($(openssl x509 -in "${DEST}/fullchain.pem" -noout -enddate))"

# 先测后 reload:测不过就不 reload —— 容器里仍跑着上一份配置与旧证书,不断线。
if docker exec "$CONTAINER" nginx -t >>"$LOG" 2>&1; then
    docker exec "$CONTAINER" nginx -s reload >>"$LOG" 2>&1
    log "nginx -t 通过并已 reload"
else
    log "ERROR nginx -t 未通过,已跳过 reload(容器仍跑旧配置)"
    exit 1
fi
