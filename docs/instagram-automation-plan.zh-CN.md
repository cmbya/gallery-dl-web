# Instagram 自动归档开发约定

基线 5d3bc6b。用户已授权先开发，真机游标与实际下载验收延后；不启停 NAS 容器、不读取真实 Cookie、不移动清理旧媒体。不以模拟通过宣称真实平台可用。

## 功能范围
- 中文网页维护用户名/主页链接，启用、追更开关/检查最新N条、补历史开关/新增完成N条；同一博主两种模式可并用。
- 两条飞牛外部任务分别触发 refresh/history；应用无额外定时器，触发成功不等于下载成功。
- posts列表包含其视频，不单独遍历Reels/快拍/精选。
- 新命名空间保存SQLite任务、媒体及帖子完成记录、当前页起点、未完成清单、暂停与Cookie检测状态，均在 DATA_DIR；旧Cookie兼容，旧归档/旧文件不导入不修改。
- 保存 instagram/用户名/文案首非空行_帖子ID/稳定媒体名；清洗截短，路径逃逸防护，首次目录固定，标题改变不重复。
- 完成记录去重，主动删除后不补；部分多图失败只补失败项；显式重新下载不自动删除原文件。
- 全局串行涵盖原手动任务；重复触发幂等，持久化执行结果与中断状态。
- 游标当前页边界重读+去重，未完页不可直接跳下一页；无效游标停止提示需重新定位，不允许无限回扫。前置用户查询仍产生请求，跨时有效性不保证。
- 置顶不作为时间序，不能用首次命中归档就停；如果API无法证明严格最新顺序要披露，设可见扫描预算。
- 429/明确Cookie失效/验证要求全局持久化暂停，后续触发不请求平台，手动恢复。模糊302停止本轮但不冒称账号失效。
- Cookie格式本地校验；手动低预算在线检查有冷却，登录和帖子查询分离，不以200当登录通过，不定时巡检，更新Cookie使旧结果失效，成功不自动恢复任务。

## 接口约定
GET /api/automation 返回 subscriptions, paused, pause_reason, runs, cookie_check。
POST/PATCH/DELETE /api/automation/subscriptions[/{id}] 管理配置，不删除文件记录。
POST /api/automation/trigger {mode,subscription_id?} 返回 accepted/skipped/paused。
POST /api/automation/pause、resume、runs/{id}/cancel。
POST /api/automation/cookies/check {username?} 返回 login_status/posts_status/checked_at/reason。
POST /api/automation/subscriptions/{id}/redownload {post_id} 显式重下。
CLI python -m gallery_dl_web.automation trigger refresh|history 走容器loopback API，失败/暂停非成功退出。

## 验收
离线：限量含义、两轮历史、页内边界/游标失效/预算、置顶、部分失败、删文件/标题变化、并发去重、重启恢复、全局暂停、Cookie冷却与版本作废、目录清洗、图库递归/ZIP/删除兼容，ruff format/check/mypy/pytest与覆盖率阈值，前端typecheck/lint/build。
真机延后：低请求分页跨进程/跨时间恢复、登录与帖子查询判定、小批实际下载、重复调度与风控暂停。未验证项明确列出。

## 部署边界
用户手动重新部署。Compose 保留默认 bridge 与 links，DATA_DIR绑定 /vol2/1000/docker/INS-NET/data，媒体 /vol2/1000/图库/download，端口3089。需备份应用数据，尤其凭证和SQLite状态；不在聊天或仓库包含真实备份数据。
