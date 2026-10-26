"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { detectPlatform } from "@/lib/platform";
import { createJob, getSettings, type PacingMode, type SettingsResponse } from "@/lib/api";

export function UrlForm() {
  const router = useRouter();
  const [url, setUrl] = useState("");
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [include, setInclude] = useState("posts,reels");
  const [videos, setVideos] = useState(true);
  const [anonymous, setAnonymous] = useState(false);
  const [albums, setAlbums] = useState(false);
  const [quickUpdate, setQuickUpdate] = useState(false);
  const [quickLimit, setQuickLimit] = useState(20);
  // "默认" means send nothing and let the server's Settings decide.
  const [pacingMode, setPacingMode] = useState<PacingMode | "默认">("默认");
  const [pacingMin, setPacingMin] = useState(1);
  const [pacingMax, setPacingMax] = useState(30);
  // null, not 0: the job layer saying nothing must inherit the server's per-image delay rather
  // than switching it off. Only a number the operator actually typed is sent.
  const [pacingPerFile, setPacingPerFile] = useState<number | null>(null);
  const [cookieStatus, setCookieStatus] = useState<SettingsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Presence booleans only — the API never returns cookie values. Used to tell the user *before*
  // they submit that this job will fall back to anonymous, rather than leaving them to infer it
  // from the result.
  useEffect(() => {
    getSettings()
      .then(setCookieStatus)
      .catch(() => {});
  }, []);

  const platform = detectPlatform(url);
  const hasCookies =
    platform === "instagram"
      ? cookieStatus?.has_ig
      : platform === "facebook"
        ? cookieStatus?.has_fb
        : undefined;
  // Only once we know: `undefined` means no platform detected yet, or settings still loading.
  const willFallBack = hasCookies === false && !anonymous;

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (!platform) {
      setError("请输入 Instagram（instagram.com）或 Facebook（facebook.com）链接。");
      return;
    }
    setBusy(true);
    try {
      const options: Record<string, unknown> = {};
      if (platform === "instagram") {
        options["include"] = include;
        options["videos"] = videos;
      }
      if (platform === "facebook") {
        // Default is "photos" alone; albums re-walks pages `photos` already covered.
        if (albums) options["include"] = "photos,albums";
        if (quickUpdate) options["quick_update"] = quickLimit;
      }
      // Omitted entirely unless overridden, so the server's Settings stay in charge.
      if (pacingMode !== "默认") {
        options["pacing"] = {
          mode: pacingMode,
          min: pacingMin,
          max: pacingMax,
          per_file: pacingPerFile,
        };
      }
      // Only sent when opted in. Omitted, the backend still runs anonymously if no cookies are
      // stored for the platform — this flag is the "even though I have cookies" override.
      if (anonymous) {
        options["anonymous"] = true;
      }
      const { job_id } = await createJob(url, platform, options);
      router.push(`/jobs/${job_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "创建下载任务失败");
      setBusy(false);
    }
  }

  return (
    <form onSubmit={onSubmit} className="card bg-base-100 shadow-xl border border-base-300">
      <div className="card-body gap-4">
        <h2 className="card-title">通过链接下载</h2>
        <label className="form-control">
          <div className="label">
            <span className="label-text">Instagram 或 Facebook 链接</span>
          </div>
          <input
            type="url"
            className="input input-bordered w-full"
            placeholder="https://www.instagram.com/p/Cxxxx/"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            required
            autoFocus
          />
        </label>

        {error && <div className="alert alert-error py-2 text-sm">{error}</div>}

        <div className="flex flex-col gap-1">
          <label className="label cursor-pointer justify-start gap-3">
            <input
              type="checkbox"
              className="checkbox checkbox-sm"
              checked={anonymous}
              onChange={(e) => setAnonymous(e.target.checked)}
            />
            <span className="label-text">匿名下载（不使用我的 Cookie）</span>
          </label>
          <p className="pl-9 text-xs text-base-content/60">
            {anonymous
              ? "仅支持公开主页。匿名访问时不会下载 Instagram 快拍、精选内容和收藏帖子；且 Instagram 匿名访问通常不如 Facebook 稳定。"
              : willFallBack
                ? `未保存 ${platform === "instagram" ? "Instagram" : "Facebook"} Cookie；此任务将以匿名方式运行（仅限公开内容）。`
                : "不勾选则使用“设置”中保存的 Cookie。下载公开主页时可勾选匿名模式，避免使用登录会话。"}
          </p>
        </div>

        <div className="collapse collapse-arrow bg-base-200">
          <input
            type="checkbox"
            checked={showAdvanced}
            onChange={(e) => setShowAdvanced(e.target.checked)}
          />
          <div className="collapse-title font-medium">此任务的高级选项</div>
          <div className="collapse-content flex flex-col gap-3 pt-2">
            {platform === "instagram" && (
              <>
                <label className="form-control">
                  <div className="label">
                    <span className="label-text">include</span>
                  </div>
                  <input
                    className="input input-bordered w-full"
                    value={include}
                    onChange={(e) => setInclude(e.target.value)}
                    placeholder="posts,reels,stories,highlights"
                  />
                </label>
                <label className="label cursor-pointer justify-start gap-3">
                  <input
                    type="checkbox"
                    className="checkbox checkbox-sm"
                    checked={videos}
                    onChange={(e) => setVideos(e.target.checked)}
                  />
                  <span className="label-text">下载视频</span>
                </label>
              </>
            )}

            {platform === "facebook" && (
              <>
                <div className="flex flex-col gap-1">
                  <label className="label cursor-pointer justify-start gap-3">
                    <input
                      type="checkbox"
                      className="checkbox checkbox-sm"
                      checked={albums}
                      onChange={(e) => setAlbums(e.target.checked)}
                    />
                    <span className="label-text">同时遍历相册</span>
                  </label>
                  <p className="pl-9 text-xs text-base-content/60">
                    耗时可能接近翻倍，但额外文件通常很少：遍历相册会重复获取主页已查过的照片页面；Facebook 只有获取页面后才能判断照片是否已下载。
                  </p>
                </div>
                <div className="flex flex-col gap-1">
                  <label className="label cursor-pointer justify-start gap-3">
                    <input
                      type="checkbox"
                      className="checkbox checkbox-sm"
                      checked={quickUpdate}
                      onChange={(e) => setQuickUpdate(e.target.checked)}
                    />
                    <span className="label-text">快速更新——遇到已下载文件后停止</span>
                    <input
                      type="number"
                      min={1}
                      className="input input-bordered input-xs w-20"
                      value={quickLimit}
                      disabled={!quickUpdate}
                      onChange={(e) => setQuickLimit(Number(e.target.value))}
                    />
                    <span className="label-text">个已下载文件</span>
                  </label>
                  <p className="pl-9 text-xs text-base-content/60">
                    Facebook 按时间从新到旧排列，因此更新时能很快找到新照片，然后会重新读取旧页面并跳过已下载内容。如果上次任务中断，请不要启用此选项，否则可能还没到上次中断的位置就停止。
                  </p>
                </div>
              </>
            )}

            <div className="flex flex-col gap-2">
              <span className="label-text">此任务的请求间隔</span>
              <div className="flex flex-wrap items-center gap-3">
                <select
                  className="select select-bordered select-sm"
                  value={pacingMode}
                  onChange={(e) => setPacingMode(e.target.value as PacingMode | "默认")}
                >
                  <option value="默认">使用服务器默认值</option>
                  <option value="adaptive">自适应</option>
                  <option value="fixed">固定</option>
                </select>
                {pacingMode !== "默认" && (
                  <>
                    <label className="flex items-center gap-2">
                      <span className="label-text text-xs">
                        {pacingMode === "adaptive" ? "最小间隔（秒）" : "最小间隔（秒）"}
                      </span>
                      <input
                        type="number"
                        min={0}
                        step="0.5"
                        className="input input-bordered input-sm w-20"
                        value={pacingMin}
                        onChange={(e) => setPacingMin(Number(e.target.value))}
                      />
                    </label>
                    <label className="flex items-center gap-2">
                      <span className="label-text text-xs">
                        {pacingMode === "adaptive" ? "最大间隔（秒）" : "最大间隔（秒）"}
                      </span>
                      <input
                        type="number"
                        min={0}
                        step="0.5"
                        className="input input-bordered input-sm w-20"
                        value={pacingMax}
                        onChange={(e) => setPacingMax(Number(e.target.value))}
                      />
                    </label>
                    <label className="flex items-center gap-2">
                      <span className="label-text text-xs">每张图片间隔（秒）</span>
                      <input
                        type="number"
                        min={0}
                        step="0.5"
                        placeholder="默认"
                        className="input input-bordered input-sm w-24"
                        value={pacingPerFile ?? ""}
                        onChange={(e) =>
                          setPacingPerFile(e.target.value === "" ? null : Number(e.target.value))
                        }
                      />
                    </label>
                  </>
                )}
              </div>
              <p className="text-xs text-base-content/60">
                自适应 starts at the floor and backs off only when the platform pushes back.
                Per image is a separate axis and applies in both modes; leave it blank to keep the
                server&apos;s value. Change the 默认 for every job in{" "}
                <a className="link" href="/settings">
                  Settings
                </a>
                .
              </p>
            </div>
          </div>
        </div>

        <div className="card-actions justify-end">
          <button type="submit" className="btn btn-primary" disabled={busy}>
            {busy ? "正在创建…" : "开始下载"}
          </button>
        </div>
        <p className="text-xs text-base-content/60">
          Cookie 非必需；不设置也可以下载公开主页。可在“设置”中添加{" "}
          <a className="link" href="/settings">
            Settings
          </a>{" "}
          to reach private or restricted content, and for more reliable Instagram results.
        </p>
      </div>
    </form>
  );
}
