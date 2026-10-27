"use client";

import { useEffect, useState } from "react";
import { getSettings, updateCookies, type SettingsResponse } from "@/lib/api";

export function CookieForm() {
  const [ig, setIg] = useState("");
  const [igText, setIgText] = useState("");
  const [fb, setFb] = useState("");
  const [status, setStatus] = useState<SettingsResponse | null>(null);
  const [msg, setMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    getSettings().then(setStatus).catch(() => {});
  }, []);

  async function onFile(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    if (f) setFb(await f.text());
  }

  async function save() {
    setBusy(true);
    setMsg(null);
    try {
      const body: { ig_sessionid?: string; ig_cookies_text?: string; fb_cookies_text?: string } = {};
      if (ig && igText) throw new Error("Instagram 请只填写完整 Cookie 或 sessionid 中的一种。");
      if (ig) body.ig_sessionid = ig;
      if (igText) body.ig_cookies_text = igText;
      if (fb) body.fb_cookies_text = fb;
      const s = await updateCookies(body);
      setStatus(s);
      setMsg({ kind: "ok", text: "已保存。Cookie 内容不会再次显示。" });
      setIg("");
      setIgText("");
      setFb("");
    } catch (err) {
      setMsg({
        kind: "err",
        text: err instanceof Error ? err.message : "保存失败",
      });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="card bg-base-100 shadow border border-base-300">
        <div className="card-body gap-3">
          <div className="flex items-center justify-between">
            <h2 className="card-title">Instagram</h2>
            {status?.has_ig && <span className="badge badge-success badge-sm">已配置</span>}
          </div>
          <p className="text-xs text-base-content/60">
            推荐上传浏览器扩展导出的 Netscape 格式 cookies.txt，需包含 sessionid。仅导入 Instagram 域名的 Cookie，忽略其他网站。Cookie 可访问账号，请勿分享。
          </p>
          <label className="flex flex-col gap-2">
            <span className="text-sm">完整 Instagram Cookie（推荐）</span>
            <textarea
              className="textarea textarea-bordered w-full h-32 font-mono text-xs"
              placeholder="# Netscape HTTP Cookie File"
              value={igText}
              onChange={(e) => setIgText(e.target.value)}
              autoComplete="off"
              spellCheck={false}
            />
          </label>
          <label className="flex flex-col gap-2">
            <span className="text-sm">上传 Instagram cookies.txt</span>
            <input
              type="file"
              accept=".txt,text/plain"
              className="file-input file-input-bordered file-input-sm"
              onChange={async (e) => {
                const input = e.currentTarget;
                const file = input.files?.[0];
                if (file) {
                  setIgText(await file.text());
                  setIg("");
                }
                input.value = "";
              }}
            />
          </label>
          <p className="text-xs text-base-content/60">兼容旧方式：也可仅填写 sessionid 的值。两种方式只能选一种，留空不会清除已保存内容。</p>
          <input
            type="password"
            className="input input-bordered w-full"
            placeholder="sessionid"
            value={ig}
            onChange={(e) => setIg(e.target.value)}
            autoComplete="off"
          />
        </div>
      </div>

      <div className="card bg-base-100 shadow border border-base-300">
        <div className="card-body gap-3">
          <div className="flex items-center justify-between">
            <h2 className="card-title">Facebook</h2>
            {status?.has_fb && <span className="badge badge-success badge-sm">已配置</span>}
          </div>
          <p className="text-xs text-base-content/60">
            请粘贴 Netscape 格式的 <code>cookies.txt</code> （可通过浏览器扩展导出），或上传文件。<strong>这些 Cookie 可完全访问账号，请使用专用账号。</strong>
          </p>
          <textarea
            className="textarea textarea-bordered w-full h-40 font-mono text-xs"
            placeholder={"# Netscape HTTP Cookie File\n.facebook.com\tTRUE\t/\tTRUE\t1900000000\tc_user\t…"}
            value={fb}
            onChange={(e) => setFb(e.target.value)}
          />
          <input
            type="file"
            accept=".txt,text/plain"
            className="file-input file-input-bordered file-input-sm"
            onChange={onFile}
          />
        </div>
      </div>

      {msg && (
        <div className={`alert ${msg.kind === "ok" ? "alert-success" : "alert-error"} py-2 text-sm`}>
          {msg.text}
        </div>
      )}
      <button className="btn btn-primary self-start" onClick={save} disabled={busy}>
        {busy ? "正在保存…" : "保存 Cookie"}
      </button>
    </div>
  );
}
