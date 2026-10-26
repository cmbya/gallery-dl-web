"use client";

import { useEffect, useState } from "react";
import {
  getSettings,
  updatePacing,
  type Pacing,
  type PacingStatus,
  type SettingsResponse,
} from "@/lib/api";

type Platform = "instagram" | "facebook";

const PLATFORMS: { id: Platform; label: string; hint: string }[] = [
  {
    id: "facebook",
    label: "Facebook",
    hint: "Facebook fetches a full page for every single photo, so the request delay is already paid once per image. Tune the floor here and leave seconds-per-image at 0, or you pay twice.",
  },
  {
    id: "instagram",
    label: "Instagram",
    hint: "Instagram returns ~30 posts per request, so the request delay spreads across them and costs far less — which is exactly why the per-image value matters more here than the floor does.",
  },
];

/**
 * Server-side, unlike the theme card next to it: pacing is what the *worker* does, so it has to
 * be the same for every browser, and it has to survive a restart. Applies to the next job — a
 * running one keeps the pacing it started with.
 */
export function PacingSetting() {
  const [status, setStatus] = useState<SettingsResponse["pacing"] | null>(null);
  const [draft, setDraft] = useState<Partial<Record<Platform, Pacing>>>({});
  const [msg, setMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);
  const [busy, setBusy] = useState<Platform | null>(null);

  useEffect(() => {
    getSettings()
      .then((s) => setStatus(s.pacing))
      .catch(() => {});
  }, []);

  function valueFor(platform: Platform): Pacing | null {
    const live = status?.[platform];
    // Field by field, so every field has to be listed: one left out here is silently dropped on
    // the next Save, which for `per_file` would read to the server as "turn the per-image delay
    // off" rather than "leave it alone".
    return (
      draft[platform] ??
      (live
        ? { mode: live.mode, min: live.min, max: live.max, per_file: live.per_file }
        : null)
    );
  }

  function edit(platform: Platform, patch: Partial<Pacing>) {
    const current = valueFor(platform);
    if (current) setDraft({ ...draft, [platform]: { ...current, ...patch } });
  }

  async function save(platform: Platform, pacing: Pacing | null) {
    setBusy(platform);
    setMsg(null);
    try {
      const s = await updatePacing(platform, pacing);
      setStatus(s.pacing);
      setDraft({ ...draft, [platform]: undefined });
      setMsg({ kind: "ok", text: "已保存，将应用于下一个新任务。" });
    } catch (err) {
      setMsg({ kind: "err", text: err instanceof Error ? err.message : "保存失败" });
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="card bg-base-100 shadow border border-base-300">
      <div className="card-body gap-4">
        <h2 className="card-title">下载请求间隔</h2>
        <p className="text-xs text-base-content/60">
          两个平台都会限制抓取频率。 In <strong>自适应</strong> mode a run starts at the
          floor and only slows down when the platform pushes back, then speeds back up — and the
          floor rises on its own as a run gets long, since that is when a block actually happens.
          Choose <strong>固定</strong> to sleep a random amount in the range on every request
          instead.
        </p>
        <p className="text-xs text-base-content/60">
          <strong>每张图片间隔（秒）</strong> is a separate control and works in both modes. The
          settings above space the <em>requests</em> that list posts; one Instagram request returns
          about 30 posts, whose images then download back to back with nothing between them. This
          puts a floor under that burst, jittered slightly so the gaps are not perfectly regular.
          Set it to 0 to turn it off. It is charged only on files actually downloaded, so
          re-running a profile you already have stays fast.
        </p>

        {PLATFORMS.map(({ id, label, hint }) => {
          const value = valueFor(id);
          const live: PacingStatus | undefined = status?.[id];
          if (!value) return null;
          const dirty = draft[id] !== undefined;
          return (
            <div key={id} className="rounded-box border border-base-300 p-3 flex flex-col gap-3">
              <div className="flex items-center gap-2">
                <span className="font-medium">{label}</span>
                {live?.overridden && <span className="badge badge-sm">自定义</span>}
              </div>
              <p className="text-xs text-base-content/60">{hint}</p>

              <div className="flex flex-wrap items-end gap-3">
                <label className="form-control">
                  <div className="label py-1">
                    <span className="label-text text-xs">模式</span>
                  </div>
                  <select
                    className="select select-bordered select-sm"
                    value={value.mode}
                    onChange={(e) => edit(id, { mode: e.target.value as Pacing["mode"] })}
                  >
                    <option value="adaptive">自适应</option>
                    <option value="fixed">固定</option>
                  </select>
                </label>
                <label className="form-control">
                  <div className="label py-1">
                    <span className="label-text text-xs">
                      {value.mode === "adaptive" ? "最小间隔（秒）" : "最小间隔（秒）"}
                    </span>
                  </div>
                  <input
                    type="number"
                    min={0}
                    step="0.5"
                    className="input input-bordered input-sm w-24"
                    value={value.min}
                    onChange={(e) => edit(id, { min: Number(e.target.value) })}
                  />
                </label>
                <label className="form-control">
                  <div className="label py-1">
                    <span className="label-text text-xs">
                      {value.mode === "adaptive" ? "最大间隔（秒）" : "最大间隔（秒）"}
                    </span>
                  </div>
                  <input
                    type="number"
                    min={0}
                    step="0.5"
                    className="input input-bordered input-sm w-24"
                    value={value.max}
                    onChange={(e) => edit(id, { max: Number(e.target.value) })}
                  />
                </label>
                <label className="form-control">
                  <div className="label py-1">
                    <span className="label-text text-xs">每张图片间隔（秒）</span>
                  </div>
                  <input
                    type="number"
                    min={0}
                    step="0.5"
                    className="input input-bordered input-sm w-28"
                    value={value.per_file ?? 0}
                    onChange={(e) => edit(id, { per_file: Number(e.target.value) })}
                  />
                </label>
                <button
                  className="btn btn-primary btn-sm"
                  disabled={busy === id || !dirty}
                  onClick={() => save(id, value)}
                >
                  {busy === id ? "正在保存…" : "Save"}
                </button>
                {live?.overridden && (
                  <button
                    className="btn btn-ghost btn-sm"
                    disabled={busy === id}
                    onClick={() => save(id, null)}
                  >
                    恢复默认值
                  </button>
                )}
              </div>
            </div>
          );
        })}

        {msg && (
          <div
            className={`alert ${msg.kind === "ok" ? "alert-success" : "alert-error"} py-2 text-sm`}
          >
            {msg.text}
          </div>
        )}
        <p className="text-xs text-base-content/60">
          Stored on the server, so it is the same in every browser. If you do get blocked, raise the
          floor and wait — retrying immediately extends the block.
        </p>
      </div>
    </div>
  );
}
