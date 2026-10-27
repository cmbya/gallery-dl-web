"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { addSubscription, cancelAutomationRun, deleteSubscription, getAutomation, pauseAutomation, redownloadPost, repositionHistory, resumeAutomation, triggerAutomation, updateSubscription, type AutomationMode, type AutomationState } from "@/lib/api";
import { isActiveRun, reasonLabel, statusLabel } from "@/lib/automation";
import { AutomationResult } from "@/components/AutomationResult";
import { CookieCheckPanel } from "@/components/CookieCheckPanel";
import { SubscriptionForm } from "@/components/SubscriptionForm";

export function SubscriptionDashboard() {
  const [data, setData] = useState<AutomationState | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [resetId, setResetId] = useState<string | null>(null);
  const [postId, setPostId] = useState("");
  const mounted = useRef(false);
  const fetching = useRef<Promise<boolean> | null>(null);
  const actionLock = useRef(false);
  const editButtons = useRef<Record<string, HTMLButtonElement | null>>({});
  const load = useCallback(async () => {
    if (fetching.current) return fetching.current;
    const request = (async () => {
      try {
        const next = await getAutomation();
        if (mounted.current) { setData(next); setError(""); }
        return true;
      } catch (err) {
        if (mounted.current) setError(err instanceof Error ? `状态读取失败：${err.message}` : "状态读取失败，请重试。");
        return false;
      } finally {
        if (mounted.current) setLoading(false);
      }
    })();
    fetching.current = request;
    try { return await request; } finally { if (fetching.current === request) fetching.current = null; }
  }, []);
  useEffect(() => {
    mounted.current = true;
    // Schedule the initial network read after effect setup; subsequent reads are event-driven.
    void Promise.resolve().then(() => { if (mounted.current) return load(); });
    const visible = () => { if (document.visibilityState === "visible") void load(); };
    document.addEventListener("visibilitychange", visible);
    return () => { mounted.current = false; document.removeEventListener("visibilitychange", visible); };
  }, [load]);
  const active = data?.runs.some((run) => isActiveRun(run.status)) ?? false;
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => { if (document.visibilityState === "visible") void load(); }, 15000);
    return () => window.clearInterval(timer);
  }, [active, load]);
  async function act(work: () => Promise<unknown>, message: string): Promise<boolean> {
    if (actionLock.current) return false;
    actionLock.current = true; setBusy(true); setError(""); setNotice("");
    try { await work(); if (fetching.current) await fetching.current; const refreshed = await load(); setNotice(`${message}${refreshed ? "" : " 操作已提交，但状态刷新失败；请刷新状态，不要重复提交。"}`); return true; }
    catch (err) { setError(err instanceof Error ? `操作失败：${err.message}` : "操作失败，请重试。"); return false; }
    finally { actionLock.current = false; setBusy(false); }
  }
  async function trigger(mode: AutomationMode, id?: string) {
    let message = "";
    await act(async () => {
      const result = await triggerAutomation(mode, id);
      message = `已接受 ${result.accepted} 项，跳过 ${result.skipped} 项。${result.paused ? "全局当前已暂停。" : ""}`;
    }, "");
    if (message) setNotice((current) => message + current);
  }
  function closeEditor(id: string) { setEditing(null); requestAnimationFrame(() => editButtons.current[id]?.focus()); }
  return <div className="flex flex-col gap-5">
    <header className="flex flex-col gap-2">
      <h1 className="text-2xl font-bold">订阅管理</h1>
      <p className="text-sm text-base-content/70">管理 Instagram 最新刷新与历史补全。定时触发由外部飞牛任务计划配置，本页面不运行定时调度。</p>
      <button className="btn btn-outline btn-sm self-start" onClick={() => void load()} disabled={loading || busy}>{loading ? "正在刷新状态…" : "刷新状态"}</button>
    </header>
    {error && <p role="alert" className="alert alert-error break-words">{error}</p>}
    <p role="status" aria-live="polite" className="text-sm">{notice}</p>
    {!data && <p role="status">{loading ? "正在读取订阅…" : "暂无可用状态，请刷新重试。"}</p>}
    {data && <>
      <section className="card bg-base-100 border border-base-300" aria-labelledby="global-title"><div className="card-body gap-3">
        <h2 id="global-title" className="card-title">全局控制</h2>
        <p>自动任务：<strong>{data.paused ? "已暂停" : "未暂停"}</strong></p>
        {data.pause_reason && <p className="text-sm break-words">暂停原因：{reasonLabel(data.pause_reason)}</p>}
        <div className="flex flex-wrap gap-2">
          <button className="btn btn-outline" disabled={busy} onClick={() => void act(() => data.paused ? resumeAutomation() : pauseAutomation("用户手动暂停"), data.paused ? "已恢复自动任务。" : "已暂停自动任务。")}>{data.paused ? "恢复全局任务" : "暂停全局任务"}</button>
          <button className="btn" disabled={busy || data.paused || !data.subscriptions.some((s) => s.enabled && s.refresh_enabled)} onClick={() => void trigger("refresh")}>立即刷新全部</button>
          <button className="btn" disabled={busy || data.paused || !data.subscriptions.some((s) => s.enabled && s.history_enabled)} onClick={() => void trigger("history")}>立即补全全部历史</button>
        </div>
        <p className="text-sm text-base-content/70">立即执行仍遵循订阅和模式开关；暂停时不能新触发。运行中的任务可在下方逐项取消。</p>
      </div></section>
      <CookieCheckPanel check={data.cookie_check} disabled={busy} onChecked={load} />
      <section className="card bg-base-100 border border-base-300" aria-labelledby="add-title"><div className="card-body">
        <h2 id="add-title" className="card-title">添加订阅</h2>
        <SubscriptionForm busy={busy} onSave={(input) => act(() => addSubscription(input), "已添加订阅。")} />
      </div></section>
      <section className="flex flex-col gap-3" aria-labelledby="subscriptions-title">
        <h2 id="subscriptions-title" className="text-xl font-semibold">订阅列表（{data.subscriptions.length}）</h2>
        {data.subscriptions.length === 0 && <p className="text-base-content/70">尚未添加订阅。填写上方表单开始管理。</p>}
        {data.subscriptions.map((subscription) => <article className="card bg-base-100 border border-base-300" key={subscription.id} aria-label={`${subscription.username} 的订阅`}><div className="card-body gap-3">
          <h3 className="card-title break-all">@{subscription.username} <span className="badge">{subscription.enabled ? "已启用" : "已停用"}</span></h3>
          <p className="text-sm">最新刷新：{subscription.refresh_enabled ? `已启用，每次 ${subscription.refresh_limit} 帖` : "已关闭"} · 历史补全：{subscription.history_enabled ? `已启用，每次 ${subscription.history_limit} 帖` : "已关闭"}</p>
          <p className="text-sm">历史进度：{statusLabel(subscription.history_status)}</p>
          <AutomationResult result={subscription.last_result} />
          {editing === subscription.id ? <div className="border-t border-base-300 pt-3"><h4 className="font-medium mb-2">编辑 @{subscription.username}</h4><SubscriptionForm initial={subscription} busy={busy} onCancel={() => closeEditor(subscription.id)} onSave={async (input) => {
            const saved = await act(() => updateSubscription(subscription.id, input), "已保存订阅修改。");
            if (saved) closeEditor(subscription.id);
            return saved;
          }} /></div> : <div className="flex flex-wrap gap-2">
            <button className="btn btn-sm" disabled={busy || data.paused || !subscription.enabled || !subscription.refresh_enabled} onClick={() => void trigger("refresh", subscription.id)} aria-label={`立即刷新 ${subscription.username}`}>立即刷新</button>
            <button className="btn btn-sm" disabled={busy || data.paused || !subscription.enabled || !subscription.history_enabled} onClick={() => void trigger("history", subscription.id)} aria-label={`补全 ${subscription.username} 的历史`}>补全历史</button>
            <button className="btn btn-sm btn-outline" disabled={busy} onClick={() => void act(() => updateSubscription(subscription.id, { enabled: !subscription.enabled }), subscription.enabled ? "已停用订阅。" : "已启用订阅。")} aria-label={`${subscription.enabled ? "停用" : "启用"} ${subscription.username}`}>{subscription.enabled ? "停用" : "启用"}</button>
            <button className="btn btn-sm btn-ghost" ref={(element) => { editButtons.current[subscription.id] = element; }} disabled={busy} onClick={() => setEditing(subscription.id)} aria-label={`编辑 ${subscription.username}`}>编辑</button>
            <button className="btn btn-sm btn-outline" disabled={busy || data.runs.some((run) => run.username === subscription.username && isActiveRun(run.status))} aria-label={`重新定位 ${subscription.username} 的历史进度`} onClick={() => {
              if (window.confirm(`重新定位 @${subscription.username} 的历史进度？仅重置历史游标和状态；下次手动或定时历史补全将从头开始，受扫描预算限制并跳过已归档帖子。不会清除帖子/媒体归档或已有文件，也不会立即启动下载。`)) void act(() => repositionHistory(subscription.id), "已重新定位历史进度；归档和文件保留。下次历史补全将从头开始。");
            }}>重新定位历史进度</button>
            <button className="btn btn-sm btn-ghost" disabled={busy} aria-expanded={resetId === subscription.id} onClick={() => { setResetId(resetId === subscription.id ? null : subscription.id); setPostId(""); }} aria-label={`重下 ${subscription.username} 的指定帖子`}>重下指定帖子</button>
            <button className="btn btn-sm btn-ghost text-error" disabled={busy} aria-label={`删除 ${subscription.username} 的订阅`} onClick={() => {
              if (window.confirm(`删除 @${subscription.username} 的订阅？仅删除订阅，保留已有文件与下载记录。`)) void act(() => deleteSubscription(subscription.id), "已删除订阅；已有文件与下载记录保留。");
            }}>删除订阅</button>
          </div>}
          {resetId === subscription.id && <form className="flex flex-col gap-2 border-t border-base-300 pt-3" onSubmit={(event) => {
            event.preventDefault();
            if (!postId.trim() || !window.confirm("确认重置此帖的下载记录？不会删除原文件；之后可再次触发下载。")) return;
            void act(() => redownloadPost(subscription.id, postId.trim()), "已重置指定帖子记录，原文件保留。可再次触发下载。").then((saved) => { if (saved) { setResetId(null); setPostId(""); } });
          }}><label className="flex flex-col gap-1"><span>需要重新下载的帖子编号</span><input className="input input-bordered w-full" value={postId} required onChange={(e) => setPostId(e.target.value)} /></label><p className="text-sm">仅显式重置此帖记录，不删除原文件。</p><button className="btn btn-outline self-start" type="submit" disabled={busy}>确认重置帖子记录</button></form>}
        </div></article>)}
      </section>
      <section className="flex flex-col gap-3" aria-labelledby="runs-title">
        <h2 id="runs-title" className="text-xl font-semibold">最近运行</h2>
        <p className="text-sm text-base-content/70">统计以帖子为单位；一个帖子可能包含多个文件。仅当有排队或运行任务且页面可见时，每 15 秒刷新状态。</p>
        {data.runs.length === 0 && <p>尚无运行记录。</p>}
        {data.runs.map((run) => <article className="border border-base-300 rounded-lg p-4 flex flex-col gap-2" key={run.id} aria-label={`${run.username} 的${run.mode === "refresh" ? "刷新" : "历史补全"}运行`}>
          <h3 className="font-semibold break-all">@{run.username} · {run.mode === "refresh" ? "最新刷新" : "历史补全"}</h3>
          <AutomationResult result={run} />
          {isActiveRun(run.status) && <button className="btn btn-sm btn-outline self-start" disabled={busy} aria-label={`取消 ${run.username} 的运行 ${run.id}`} onClick={() => void act(() => cancelAutomationRun(run.id), "已请求取消运行；已下载文件保留。")}>取消运行</button>}
        </article>)}
      </section>
    </>}
  </div>;
}
