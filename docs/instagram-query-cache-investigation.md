# Instagram 公共查询元数据缓存调查与接线说明

## 实际上游（gallery-dl 1.32.18）

已检查安装库 `gallery_dl/cache.py`、`extractor/common.py` 和 `extractor/instagram.py`，没有发送 Instagram 请求。

- `_prepare_graphql` 每次先 `_extract_fb_tokens(path)`，再 `extr.cache(_extract_opname_info, opname, path, _exp=86400, _mem=False)`。
- `_extract_opname_info` 调用 `_extract_opname_map` 下载目标主页引用的全部 `.js`，并将各操作 `(docid, relayprovider 参数名称)` 写回上游缓存。
- `_webpage` 的 HTML 通过默认内存缓存使用；HTML 包含 LSD、fb_dtsg 等当前任务请求令牌，不可持久化。
- 上游通用磁盘缓存是 pickle，且登录路径 `_login_impl` 使用 `_mem=False` 缓存 Cookie（90 天）。因此绝不直接启用上游磁盘缓存。

## 实现边界

`automation/query_cache.py` 只拦截当前 extractor API 实例的 `_extract_opname_info` 缓存调用，不改全局 InstagramAPI 方法，不复用上游进程内缓存命中。

持久化 JSON 只有 schema/library version、固定过期时间和 operations：

- 操作名称必须属于安装版本的 `instagram.QUERIES` 闭集；docid 为 1–32 位数字。
- relayprovider 参数仅接收 `Polaris[A-Za-z0-9_]{1,160}` 模块名，每操作至多 64 项。不符合此保守命名规则时继续原始发现结果但不落盘，可能降低未来上游新命名的命中率，不能放宽为任意字符串。
- 不保存路径、用户名、Cookie、token、HTML、raw JS、媒体数据或签名 URL；不用 pickle。
- TTL 沿用上游 86400 秒，命中不续期。schema 与 gallery-dl 版本隔离；过期、损坏、版本变化均回到一次正常上游发现。
- 锁文件非阻塞 flock；占用时停止当前轮次 `query_cache_busy`，不无限等待、不并发重复下载。不因查询错误清缓存重试，不提供过期回退，不绕风控。
- 缓存不可用时仅运行一次原始发现；诊断日志为固定分类，不输出缓存内容或路径。原始 StopRound（例如 rate_limited）原样传播。
- JSON 同目录临时文件写入后原子替换；JSON/临时文件/锁都明确 `fchmod(0600)`（处理 NAS 默认 ACL）；读已有文件亦修正权限；最终路径和锁拒绝符号链接。目录必须为服务掌控的应用状态目录。

## 父任务必须完成的 worker 接线

`GalleryBackend(..., query_cache_path: Path | None = None)` 新增可选参数；None 维持旧行为，不推导下载目录或操作 NAS。**本子任务不修改 worker/service。**

建议 worker 从既有可信 `payload["database"]` 的 parent 派生 `instagram-query-cache.json` 并显式传入；所有博主/任务采用同一路径（不按账户或用户名分片），且应用状态目录不受用户任意输入控制。不新增 API 自定义磁盘路径入口。仅接线后生产任务才默认共享。

## 离线证据 / TDD

- TDD Skill 当前日常档位明确拒绝，已停止读取，不切组、不绕过；实际仍采用 RED→GREEN。
- 首轮新测试先失败 4 项（GalleryBackend 尚无 query_cache_path）；实现后 4 项通过。
- 第二轮先加安全测试，发现忙锁泄漏 3 个 fd 的真实失败；修复关闭 fd 后通过。
- `test_instagram_query_cache.py` 用真实 gallery-dl `_prepare_graphql`、令牌解析、主页解析和 JS 正则提取，仅 mock Requests transport；先清上游 CACHE_MEMORY 再创建每个 backend，排除内存缓存假阳性。
- 同一 Python 进程 alice/bob 两 backend：主页 2 次、JS 1 次；新 Python 进程 charlie：JS 0 次，但仍重新解析本任务主页令牌。
- 过期、版本不同、损坏 JSON 各首次 JS 1 次，随后的新进程 JS 0 次。
- 确认模拟 COOKIE/LSD/DTSG/rawJS/私有 HTML/签名 URL 秘密均未落盘，JSON 和锁文件 mode 0600。
- 定向回归命令（从 backend 目录，所有临时测试目录位于 scratch）：

```sh
.venv/bin/python -m pytest tests/test_instagram_query_cache.py tests/test_query_cache_safety.py tests/test_automation_adapter.py tests/test_instagram_check_totals.py -q --no-cov --tb=short --basetemp=/vol2/@appshare/Ekko/.hermes/cache/scratch/query-cache-regression
.venv/bin/ruff check src/gallery_dl_web/automation/query_cache.py src/gallery_dl_web/automation/adapter.py tests/test_instagram_query_cache.py tests/test_query_cache_safety.py
.venv/bin/mypy src/gallery_dl_web/automation/query_cache.py src/gallery_dl_web/automation/adapter.py
```

全量后端/frontend 验证由父任务统一执行。未提交、推送、部署或操作服务。
