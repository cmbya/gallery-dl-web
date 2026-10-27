"use client";

import { useId, useRef, useState } from "react";
import type { SubscriptionInput } from "@/lib/api";
import { subscriptionInput } from "@/lib/automation";

const defaults: SubscriptionInput = { username: "", enabled: true, refresh_enabled: true, refresh_limit: 20, history_enabled: false, history_limit: 100 };

export function SubscriptionForm({ initial = defaults, busy, onSave, onCancel }: {
  initial?: SubscriptionInput;
  busy: boolean;
  onSave: (input: SubscriptionInput) => Promise<boolean>;
  onCancel?: () => void;
}) {
  const id = useId();
  const [input, setInput] = useState<SubscriptionInput>({ ...initial });
  const [error, setError] = useState("");
  const saving = useRef(false);
  const [submitting, setSubmitting] = useState(false);
  return (
    <form className="flex flex-col gap-3" onSubmit={async (event) => {
      event.preventDefault();
      if (busy || saving.current) return;
      saving.current = true; setSubmitting(true);
      setError("");
      try {
        const saved = await onSave(subscriptionInput(input));
        if (saved && !onCancel) setInput({ ...defaults });
      } catch (err) { setError(err instanceof Error ? err.message : "请检查填写内容。"); }
      finally { saving.current = false; setSubmitting(false); }
    }}>
      <label className="flex flex-col gap-1" htmlFor={`${id}-username`}>
        <span>Instagram 用户名或主页链接</span>
        <input id={`${id}-username`} className="input input-bordered w-full" value={input.username} required autoComplete="off" placeholder="用户名或 https://www.instagram.com/用户名/" onChange={(e) => setInput({ ...input, username: e.target.value })} />
      </label>
      <label className="flex items-center gap-2"><input type="checkbox" className="checkbox checkbox-sm" checked={input.enabled} onChange={(e) => setInput({ ...input, enabled: e.target.checked })} />启用此订阅</label>
      <div className="grid gap-3 sm:grid-cols-2">
        <fieldset className="border border-base-300 rounded-lg p-3 flex flex-col gap-2">
          <legend className="px-1 font-medium">最新帖子刷新</legend>
          <label className="flex items-center gap-2"><input type="checkbox" className="checkbox checkbox-sm" checked={input.refresh_enabled} onChange={(e) => setInput({ ...input, refresh_enabled: e.target.checked })} />启用刷新</label>
          <label className="flex flex-col gap-1"><span>每次刷新帖子上限</span><input type="number" min="1" step="1" required className="input input-bordered w-full" value={Number.isNaN(input.refresh_limit) ? "" : input.refresh_limit} onChange={(e) => setInput({ ...input, refresh_limit: e.target.valueAsNumber })} /></label>
        </fieldset>
        <fieldset className="border border-base-300 rounded-lg p-3 flex flex-col gap-2">
          <legend className="px-1 font-medium">历史帖子补全</legend>
          <label className="flex items-center gap-2"><input type="checkbox" className="checkbox checkbox-sm" checked={input.history_enabled} onChange={(e) => setInput({ ...input, history_enabled: e.target.checked })} />启用历史补全</label>
          <label className="flex flex-col gap-1"><span>每次历史补全帖子上限</span><input type="number" min="1" step="1" required className="input input-bordered w-full" value={Number.isNaN(input.history_limit) ? "" : input.history_limit} onChange={(e) => setInput({ ...input, history_limit: e.target.valueAsNumber })} /></label>
        </fieldset>
      </div>
      <p className="text-sm text-base-content/70">刷新和历史补全可同时启用；两种上限均按帖子数计算，不是文件数。停用订阅不会删除文件。</p>
      {error && <p role="alert" className="text-error">{error}</p>}
      <div className="flex gap-2"><button className="btn btn-primary" disabled={busy || submitting} type="submit">{busy || submitting ? "正在保存…" : onCancel ? "保存修改" : "添加订阅"}</button>{onCancel && <button className="btn btn-ghost" type="button" disabled={busy || submitting} onClick={onCancel}>取消编辑</button>}</div>
    </form>
  );
}
