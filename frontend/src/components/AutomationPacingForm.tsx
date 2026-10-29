"use client";

import { useState } from "react";
import { updateAutomationPacing, type AutomationPacing, type AutomationMode } from "@/lib/api";

const fields = { request_min: "请求间隔最小值", request_max: "请求间隔最大值", media_interval: "每媒体间隔", blogger_min: "博主间隔最小值", blogger_max: "博主间隔最大值" } as const;
export function AutomationPacingForm({ pacing, busy, onSaved }: { pacing: AutomationPacing; busy: boolean; onSaved: () => Promise<unknown> }) {
  const [draft, setDraft] = useState(pacing);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  return <form className="flex flex-col gap-3" onSubmit={async (event) => {
    event.preventDefault();
    if (saving || busy) return;
    for (const value of Object.values(draft)) {
      if (Object.values(value).some((n) => !Number.isFinite(n) || n < 0 || n > 1800) || value.request_min > value.request_max || value.blogger_min > value.blogger_max) {
        setMessage("请输入 0–1800 秒的有限数值，最小值不能大于最大值。"); return;
      }
    }
    setSaving(true); setMessage("");
    try { await updateAutomationPacing(draft); await onSaved(); setMessage("已保存，下一任务生效；已有等待时间不缩短。"); }
    catch (error) { setMessage(error instanceof Error ? error.message : "保存失败"); }
    finally { setSaving(false); }
  }}>
    <h3 className="font-semibold">自动订阅独立限速（秒）</h3>
    {(["refresh", "history"] as AutomationMode[]).map((mode) => <fieldset key={mode} disabled={busy || saving} className="flex flex-wrap gap-3">
      <legend>{mode === "refresh" ? "最新刷新" : "历史补全"}</legend>
      {(Object.keys(fields) as (keyof typeof fields)[]).map((key) => <label key={key} className="flex flex-col gap-1"><span>{fields[key]}</span><input className="input input-bordered w-36" type="number" required min="0" max="1800" step="any" value={draft[mode][key]} onChange={(e) => setDraft({ ...draft, [mode]: { ...draft[mode], [key]: e.target.value === "" ? NaN : Number(e.target.value) } })} /></label>)}
    </fieldset>)}
    <p className="text-sm">默认请求 5–10 秒、媒体 3 秒、博主 60–120 秒，仅是保守起点，不保证避免限制。等待计入任务硬超时；不会扣减请求次数。普通手动下载沿用原有限速。</p>
    <button type="submit" className="btn btn-outline self-start" disabled={busy || saving}>保存自动限速</button>
    <p role="status">{message}</p>
  </form>;
}
