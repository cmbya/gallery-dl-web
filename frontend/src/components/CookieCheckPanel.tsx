"use client";

import { AutomationDiagnostic } from "@/components/AutomationDiagnostic";
import { useRef, useState } from "react";
import { ApiError, checkAutomationCookies, type CookieCheck } from "@/lib/api";
import { formatTime, normalizeUsername, reasonLabel, statusLabel } from "@/lib/automation";

export function CookieCheckPanel({ check, disabled = false, onChecked }: {
  check: CookieCheck | null;
  disabled?: boolean;
  onChecked: () => Promise<boolean | void>;
}) {
  const [username, setUsername] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const checking = useRef(false);
  return <section className="card bg-base-100 border border-base-300" aria-labelledby="cookie-check-title">
    <div className="card-body gap-3">
      <h2 id="cookie-check-title" className="card-title">Instagram Cookie 检查</h2>
      <p className="text-sm text-base-content/70">仅点击时测试，不会定时额外检查 Cookie。登录有效不代表能访问帖子；保存新 Cookie 后旧检查结果失效。<a className="link" href="/settings">前往设置保存 Cookie</a>。</p>
      <dl className="grid gap-2 sm:grid-cols-2">
        <div><dt className="font-medium">登录状态</dt><dd>{statusLabel(check?.login_status)}</dd></div>
        <div><dt className="font-medium">帖子访问状态</dt><dd>{statusLabel(check?.posts_status)}</dd></div>
      </dl>
      <p className="text-sm">最近检查：{formatTime(check?.checked_at)}</p>
      {check?.tested_username && <p className="text-sm">测试目标：@{check.tested_username}</p>}
      <p className="text-sm text-base-content/70">留空选择一个已启用订阅；无已启用订阅时不测试帖子，只检查登录身份。身份未知不代表帖子无法访问。</p>
      <AutomationDiagnostic diagnostic={check?.diagnostic} />
      {check?.reason && <p className="text-sm break-words">说明：{reasonLabel(check.reason)}</p>}
      <form className="flex flex-col gap-2" onSubmit={async (event) => {
        event.preventDefault();
        if (disabled || checking.current) return;
        checking.current = true; setBusy(true); setMessage("");
        try {
          await checkAutomationCookies(username.trim() ? normalizeUsername(username) : undefined);
          const refreshed = await onChecked();
          setMessage(refreshed === false ? "检查已完成，但状态刷新失败，请刷新状态重试。" : "检查完成，已更新状态。检查成功不会自动恢复已暂停的任务。");
        } catch (err) {
          setMessage(err instanceof ApiError && err.status === 429 ? `检查过于频繁，请${err.retryAfterSeconds ? `至少等待 ${err.retryAfterSeconds} 秒后` : "稍后"}再试。` : err instanceof Error ? `检查失败：${err.message}` : "检查失败，请稍后重试。");
        } finally { checking.current = false; setBusy(false); }
      }}>
        <label className="flex flex-col gap-1"><span>测试用户名或主页链接（可选）</span><input className="input input-bordered w-full" value={username} autoComplete="off" onChange={(e) => setUsername(e.target.value)} placeholder="留空由服务端选择测试目标" /></label>
        <button type="submit" className="btn btn-outline self-start" disabled={busy || disabled}>{busy ? "正在检查…" : "手动检查 Cookie"}</button>
      </form>
      <p role="status" aria-live="polite" className="text-sm">{message}</p>
    </div>
  </section>;
}
