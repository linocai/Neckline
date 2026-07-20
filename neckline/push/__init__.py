"""APNs 推送(plan 4B.5)。复用 LinoN `push/apns.py` 的 token-based JWT(ES256) +
HTTP/2 直连姿势(账号级 .p8 密钥,topic 换成 `top.linotsai.neckline`)。**只推两类**
(§2.4 拍板):16:00 盘后报告就绪、退潮红色刹车;其余哨兵事件只进看板。
"""
