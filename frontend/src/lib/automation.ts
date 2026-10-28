import type { SubscriptionInput } from "./api";

export function normalizeUsername(value: string): string {
  let name = value.trim();
  if (/^(https?:\/\/|www\.)/i.test(name)) {
    let url: URL;
    try { url = new URL(/^www\./i.test(name) ? `https://${name}` : name); }
    catch { throw new Error("请输入有效的 Instagram 用户名或主页链接。"); }
    if (!["instagram.com", "www.instagram.com"].includes(url.hostname.toLowerCase()) || url.port || url.username || url.password) {
      throw new Error("仅支持 Instagram 主页链接。");
    }
    const segments = url.pathname.split("/").filter(Boolean);
    if (segments.length !== 1 || ["p", "reel", "reels", "stories", "explore", "accounts", "direct"].includes(segments[0].toLowerCase())) {
      throw new Error("请填写用户主页，不是帖子或其他页面的链接。");
    }
    name = segments[0];
  }
  name = name.replace(/^@/, "");
  if (!/^[a-zA-Z0-9._]{1,30}$/.test(name)) throw new Error("用户名应为 1–30 位字母、数字、点或下划线。");
  return name.toLowerCase();
}

export function subscriptionInput(input: SubscriptionInput): SubscriptionInput {
  for (const limit of [input.refresh_limit, input.history_limit]) {
    if (!Number.isSafeInteger(limit) || limit < 1) throw new Error("帖子数量上限必须是正整数。");
  }
  // Editing receives a full Subscription. Never leak server-only fields into the
  // strict create/patch schema (id, history_status, last_result, ...).
  return { username: normalizeUsername(input.username), enabled: input.enabled,
    refresh_enabled: input.refresh_enabled, refresh_limit: input.refresh_limit,
    history_enabled: input.history_enabled, history_limit: input.history_limit };
}

const labels: Record<string, string> = {
  pending: "等待中", queued: "排队中", running: "运行中", completed: "已完成", complete: "已完成",
  succeeded: "已完成", success: "成功", failed: "失败", error: "检查出错", cancelled: "已取消",
  canceled: "已取消", skipped: "已跳过", paused: "已暂停", idle: "未开始", unknown: "未知",
  valid: "有效", invalid: "无效", expired: "已过期", missing: "未配置", stale: "已失效，需重新检查",
  not_checked: "未检查", unchecked: "未检查", not_tested: "未测试", untested: "未测试", not_configured: "未配置",
  authenticated: "已登录", logged_in: "已登录", unauthenticated: "未登录", logged_out: "未登录",
  accessible: "可访问", available: "可访问", ok: "正常", restricted: "访问受限", rate_limited: "请求受限",
  blocked: "访问被阻止", checkpoint: "需要安全验证", login_required: "需要登录", incomplete: "未完成",
  exhausted: "历史已遍历", partial: "部分完成", never: "尚未运行", not_run: "尚未运行",
  ready: "等待补全", needs_reposition: "需重新定位历史进度", challenge: "需要安全验证", stopped: "已停止", interrupted: "已中断",
};
const reasons: Record<string, string> = {
  global_post_limit: "已达到跨博主本轮新增帖子总限额，未处理帖子延期至下轮（不是风控）",
  redirect: "收到无法确认的重定向（不代表 Cookie 无效）",
  redirect_home: "请求被重定向到首页，未取得所需数据",
  redirect_login: "请求被重定向到登录页，尚不能确认会话有效性",
  redirect_challenge: "请求被重定向到安全验证页，请在 Instagram 检查",
  redirect_external: "已阻止跨域或非受信目标重定向",
  redirect_insecure: "已阻止非 HTTPS 重定向",
  redirect_invalid: "重定向地址格式异常，已停止请求",
  redirect_missing_location: "重定向响应缺少目标地址",
  redirect_unsupported: "重定向不是受支持的同路径规范化，已停止请求",
  redirect_loop: "检测到重定向循环，已停止请求",
  redirect_limit: "已达到重定向跳转上限",
  query_error: "帖子查询响应无法解析，尚不能判断 Cookie 是否有效",
  scan_budget: "已达到本轮扫描上限，可在下一轮继续",
  unsupported_post: "帖子元数据缺少标识，已停止本轮",
  unsupported_media_id: "媒体标识不受支持，已停止本轮",
  unsafe_output_path: "输出路径安全检查未通过",
  cookies_changed_during_check: "检查期间 Cookie 已更改，请重新检查",
  operator: "用户手动操作", invalid_session: "登录会话无效，请更新 Cookie", challenge: "Instagram 要求安全验证",
  rate_limited: "Instagram 请求频率受限", request_budget: "已达到本轮请求上限", time_budget: "已达到本轮时间上限",
  post_limit: "已达到本轮帖子上限", needs_reposition: "历史游标已失效，需要重新定位", exhausted: "历史帖子已遍历完毕",
  local_session_missing_or_malformed: "未配置会话或会话格式错误", identity_rejected: "登录身份验证请求被拒绝",
  identity_unknown: "无法确认登录身份", check_error: "检查时发生错误", worker_error: "下载进程出错", runner_error: "运行服务出错",
  server_shutdown: "服务关闭，任务已中断", server_restarted: "服务重启，任务已中断",
  subscription_disabled_or_deleted: "订阅已停用或删除", cookies_changed: "Cookie 已更改，请重新检查",
};
export function reasonLabel(value: string): string { return reasons[value] ?? (/^[a-z][a-z0-9_]*$/.test(value) ? "服务返回未识别的原因，请查看服务日志" : value); }
export function statusLabel(value?: string | null): string { return value ? labels[value] ?? "未知状态" : "尚无结果"; }
export function isActiveRun(status: string): boolean { return ["queued", "pending", "running"].includes(status); }
export function formatTime(value?: string | number | null): string {
  if (!value) return "尚无记录";
  const date = new Date(typeof value === "number" ? value * 1000 : value);
  return Number.isNaN(date.getTime()) ? "时间未知" : date.toLocaleString("zh-CN");
}
