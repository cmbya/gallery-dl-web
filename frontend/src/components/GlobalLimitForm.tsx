"use client";

import { useRef, useState } from "react";
import { updateGlobalLimits, type GlobalLimits } from "@/lib/api";

export function GlobalLimitForm({ limits, busy, onSaved }: { limits: GlobalLimits; busy: boolean; onSaved: () => Promise<unknown> }) {
  const [value, setValue] = useState(limits);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const lock = useRef(false);
  const [serverLimits, setServerLimits] = useState(limits);
  if (limits.refresh !== serverLimits.refresh || limits.history !== serverLimits.history) {
    // Sync untouched fields during render so a server refresh cannot reset local edits.
    setValue({
      refresh: value.refresh === serverLimits.refresh ? limits.refresh : value.refresh,
      history: value.history === serverLimits.history ? limits.history : value.history,
    });
    setServerLimits(limits);
  }
  return <form aria-label="跨博主每轮新增总限额" className="flex flex-col gap-2" onSubmit={async (event) => {
    event.preventDefault();
    if (busy || lock.current) return;
    if (Object.values(value).some((limit) => !Number.isSafeInteger(limit) || limit < 1 || limit > 1000)) {
      setMessage("总限额必须是 1–1000 的整数。"); return;
    }
    lock.current = true; setSaving(true); setMessage("");
    try {
      await updateGlobalLimits(value);
      setMessage("已保存总限额，新批次生效。");
      await onSaved();
    } catch (error) { setMessage(error instanceof Error ? error.message : "保存失败，请重试。"); }
    finally { lock.current = false; setSaving(false); }
  }}>
    <h3 className="font-medium">跨博主每轮新增总限额</h3>
    {(["refresh", "history"] as const).map((mode) => <label key={mode} className="flex flex-col gap-1">
      <span>{mode === "refresh" ? "追新" : "历史补全"}每轮新增帖子总限额</span>
      <input name={mode} type="number" min="1" max="1000" step="1" required className="input input-bordered" value={Number.isNaN(value[mode]) ? "" : value[mode]} disabled={busy || saving} onChange={(event) => setValue({ ...value, [mode]: event.target.valueAsNumber })} />
    </label>)}
    <button type="submit" className="btn btn-outline self-start" disabled={busy || saving}>保存总限额</button>
    {message && <p role="status">{message}</p>}
  </form>;
}
