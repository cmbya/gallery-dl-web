"use client";

import { useEffect, useRef, useState } from "react";
import { JobControls } from "@/components/JobControls";
import { getJob, jobZipUrl, type JobSummary } from "@/lib/api";
import { JOB_EVENT_TYPES, type JobEvent } from "@/lib/events";

function describe(ev: JobEvent): string {
  switch (ev.type) {
    case "queued":
      return "排队中";
    case "started":
      return "已开始";
    case "prepare":
      return `正在获取 ${ev.filename ?? "…"}`;
    case "file":
      return `${ev.event === "skipped" ? "↷ 已跳过" : "✓ 已下载"} ${ev.filename ?? ""}${
        ev.bytes ? ` (${ev.bytes} B)` : ""
      }`;
    case "progress":
      return `进度：已下载 ${ev.downloaded ?? 0}，已跳过 ${ev.skipped ?? 0}，失败 ${
        ev.failed ?? 0
      } 项`;
    case "heartbeat":
      return `· 仍在运行（${Math.round(ev.elapsed ?? 0)} 秒）`;
    case "pacing":
      return `${PACING_ROUTINE.includes(ev.reason ?? "") ? "⏱" : "🐢"} pacing ${
        ev.previous ?? 0
      }s → ${ev.delay ?? 0}s (${ev.reason ?? "?"})`;
    case "pushback":
      // The operator's cue that this is the platform, not the app. `terminal` means the run is
      // over and retrying would extend the block, so it must not read like a transient hiccup.
      return ev.tier === "terminal"
        ? `⛔ ${ev.platform ?? "platform"} stopped the run (${ev.rule ?? "?"}) — this is a rate limit, not a download error`
        : `🐢 ${ev.platform ?? "platform"} pushed back (${ev.rule ?? "?"}) — backed off to ${ev.delay ?? 0}s`;
    case "pacing-telemetry":
      // Non-terminal: the manager killed the worker, and it flushed its request record on the way
      // out so the run can be diagnosed without repeating it.
      return `🔍 已保存最近 ${ev.pacing_telemetry?.length ?? 0} 条请求记录供排查`;
    case "stalled":
      return ev.phase === "warmup"
        ? `⏳ no files yet after ${Math.round(ev.threshold ?? 0)}s (attempt ${ev.attempt ?? 1})`
        : `⏳ stalled — no progress for ${Math.round(ev.since_last_file ?? 0)}s (attempt ${
            ev.attempt ?? 1
          })`;
    case "retrying":
      return `↻ retrying (attempt ${ev.attempt ?? 1})`;
    case "paused":
      return "⏸ 已暂停——下载进程已挂起，已释放队列名额";
    case "resumed":
      return `▶ 已继续（暂停 ${Math.round(ev.paused_for ?? 0)} 秒）`;
    case "error":
      return `${ev.fatal ? "⛔ " : "⚠ "}${ev.message ?? ev.kind ?? "error"}`;
    case "completed":
      return `已完成——下载 ${ev.downloaded ?? 0} 个文件`;
    case "cancelled":
      return `⏹ 已停止——保留已下载的 ${ev.downloaded ?? 0} 个文件`;
    case "failed":
      return ev.reason === "rate-limited"
        ? "⏸ 已停止——平台限制了此账号的请求频率"
        : `任务失败——${ev.reason ?? "原因未知"}`;
    default:
      return ev.type;
  }
}

function StatusBadge({ status }: { status: string }) {
  const map: Record<string, string> = {
    connecting: "badge-ghost",
    queued: "badge-warning",
    running: "badge-info",
    started: "badge-info",
    paused: "badge-warning",
    completed: "badge-success",
    failed: "badge-error",
    cancelled: "badge-ghost",
  };
  return <span className={`badge ${map[status] ?? "badge-ghost"}`}>{({ connecting: "连接中", queued: "排队中", running: "下载中", started: "已开始", paused: "已暂停", completed: "已完成", failed: "失败", cancelled: "已停止" }[status] ?? status)}</span>;
}

/** Pacing reasons that are the controller working normally, not the platform pushing back. */
const PACING_ROUTINE = ["ramp", "recovered"];

const TERMINAL_STATUSES = ["completed", "failed", "cancelled"];

export function JobProgress({ jobId }: { jobId: string }) {
  const [events, setEvents] = useState<JobEvent[]>([]);
  const [summary, setSummary] = useState<JobSummary | null>(null);
  const [status, setStatus] = useState("connecting");
  const [counts, setCounts] = useState({ downloaded: 0, skipped: 0, failed: 0 });
  const [stall, setStall] = useState<string | null>(null);
  const [alive, setAlive] = useState<number | null>(null);
  const [pacing, setPacing] = useState<JobEvent | null>(null);
  // The terminal event as it arrives on the stream — without this the failure alert below only
  // renders after a reload, because `summary` is fetched once at mount.
  const [terminalEvent, setTerminalEvent] = useState<JobEvent | null>(null);
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    let cancelled = false;
    getJob(jobId)
      .then((s) => {
        if (!cancelled) {
          setSummary(s);
          setStatus(s.status);
        }
      })
      .catch(() => {});

    const es = new EventSource(`/api/jobs/${jobId}/events`);
    esRef.current = es;

    const apply = (ev: JobEvent) => {
      setEvents((prev) => [...prev, ev]);
      if (ev.type === "progress") {
        setCounts({
          downloaded: ev.downloaded ?? 0,
          skipped: ev.skipped ?? 0,
          failed: ev.failed ?? 0,
        });
      }
      if (ev.type === "heartbeat") {
        setAlive(ev.elapsed ?? null);
      }
      if (ev.type === "pacing") {
        setPacing(ev);
      }
      if (ev.type === "stalled") {
        setStall(
          ev.phase === "warmup"
            ? "尚未发现文件：主页可能较大、受限或请求被限流。正在重试…"
            : "任务停滞、没有进度，正在重试…",
        );
      } else if (ev.type === "retrying") {
        setStall(`Retrying (attempt ${ev.attempt ?? 1})…`);
      } else if (
        ev.type === "file" ||
        ev.type === "progress" ||
        TERMINAL_STATUSES.includes(ev.type)
      ) {
        setStall(null);
      }
      // Pause/resume also arrive over SSE, so the badge stays right even when the action was
      // taken from the Queue page in another tab.
      if (ev.type === "paused") setStatus("paused");
      if (ev.type === "resumed") setStatus("running");
      if (TERMINAL_STATUSES.includes(ev.type)) {
        setStatus(ev.type);
        setTerminalEvent(ev);
      }
    };
    const onMsg = (e: MessageEvent) => {
      try {
        apply(JSON.parse(e.data) as JobEvent);
      } catch {
        /* ignore malformed */
      }
    };
    JOB_EVENT_TYPES.forEach((t) => es.addEventListener(t, onMsg));
    es.addEventListener("end", () => es.close());
    es.onerror = () => {
      /* EventSource auto-reconnects; backend replays history on connect */
    };

    return () => {
      cancelled = true;
      es.close();
    };
  }, [jobId]);

  const terminal = TERMINAL_STATUSES.includes(status);
  // Prefer the live terminal event; fall back to the summary fetched at mount (page reload case).
  const finalReason = (terminalEvent ??
    (summary?.final_summary as
      | { reason?: string; message?: string; resume_url?: string }
      | undefined)) as
    | { reason?: string; message?: string; resume_url?: string }
    | undefined;

  return (
    <div className="flex flex-col gap-4">
      <div className="card bg-base-100 shadow-xl border border-base-300">
        <div className="card-body gap-3">
          <div className="flex items-center justify-between gap-2">
            <h2 className="card-title truncate">任务 {jobId.slice(0, 12)}…</h2>
            <div className="flex items-center gap-2">
              {pacing !== null && !TERMINAL_STATUSES.includes(status) && (
                <span
                  className={`badge badge-sm ${
                    PACING_ROUTINE.includes(pacing.reason ?? "") ? "badge-ghost" : "badge-warning"
                  }`}
                  title={
                    PACING_ROUTINE.includes(pacing.reason ?? "")
                      ? "当前请求间隔"
                      : `平台限制请求，已自动延长间隔 (${pacing.reason})`
                  }
                >
                  ⏱ {pacing.delay}s
                </span>
              )}
              {summary?.anonymous && (
                <span
                  className="badge badge-ghost badge-sm"
                  title="Ran logged-out — public content only"
                >
                  anonymous
                </span>
              )}
              <StatusBadge status={status} />
              <JobControls jobId={jobId} status={status} onChanged={setStatus} />
            </div>
          </div>
          {summary && (
            <p className="text-sm text-base-content/70 break-all">{summary.url}</p>
          )}
          <div className="stats stats-vertical sm:stats-horizontal shadow bg-base-200">
            <div className="stat">
              <div className="stat-title">已下载</div>
              <div className="stat-value text-success">{counts.downloaded}</div>
            </div>
            <div className="stat">
              <div className="stat-title">已跳过</div>
              <div className="stat-value">{counts.skipped}</div>
            </div>
            <div className="stat">
              <div className="stat-title">失败</div>
              <div className="stat-value text-error">{counts.failed}</div>
            </div>
          </div>
          {status === "paused" && (
            <div className="alert alert-warning flex-col items-start gap-1 py-2 text-sm">
              <span className="font-semibold">⏸ 已暂停——下载进程已挂起。</span>
              <span>
                It keeps its place in the profile, so resuming continues from here instead of
                starting the walk over. Its queue slot has been freed for another profile. A very
                long pause can drop the connection to the platform; gallery-dl retries when it
                resumes.
              </span>
            </div>
          )}
          {!terminal && status !== "paused" && stall && (
            <div className="alert alert-warning py-2 text-sm">{stall}</div>
          )}
          {status === "queued" && summary?.started_at != null && (
            <div className="alert alert-info py-2 text-sm">
              正在恢复——等待空闲下载名额。
            </div>
          )}
          {!terminal && status !== "paused" && !stall && alive !== null && counts.downloaded + counts.skipped === 0 && (
            <div className="alert alert-info py-2 text-sm">
              正在读取博主主页… ({Math.round(alive)}s). Large or rate-limited profiles
              can take several minutes before the first file appears.
            </div>
          )}
          {terminal && status !== "failed" && counts.downloaded > 0 && (
            <a className="btn btn-primary btn-sm self-start" href={jobZipUrl(jobId)}>
              ⬇ 下载全部文件（.zip）
            </a>
          )}
          {status === "cancelled" && (
            <div className="alert flex-col items-start gap-1 py-2 text-sm">
              <span className="font-semibold">⏹ 已停止。</span>
              <span>
                {counts.downloaded} file(s) downloaded before you stopped were kept, and this
                profile&apos;s gallery has been updated. Re-running it skips them and continues.
              </span>
            </div>
          )}
          {terminal && status === "failed" && finalReason && (
            <div
              className={`alert flex-col items-start gap-1 py-2 text-sm ${
                // A rate limit is the platform pausing us, not a failure of the app — don't
                // shout at the user in red for something they can only wait out.
                finalReason.reason === "rate-limited" ? "alert-warning" : "alert-error"
              }`}
            >
              {finalReason.reason === "login-required" ? (
                <>
                  <span className="font-semibold">This content needs a logged-in session.</span>
                  {/* Render the backend's message rather than hardcoding prose: several different
                      failures promote to this one reason and their advice differs. An unreadable
                      Facebook profile, for one, is served the same page as a profile that no
                      longer exists, so its message has to mention the URL as well as cookies. */}
                  <span>
                    {String(
                      finalReason.message ??
                        "The profile is private or restricted, or the platform refused anonymous access. Files already downloaded are skipped.",
                    )}
                  </span>
                  <span>
                    <a className="link" href="/settings">
                      打开设置
                    </a>{" "}
                    to add or refresh cookies for this platform.
                  </span>
                  {summary?.anonymous && (
                    <span className="opacity-80">
                      This run was anonymous, so any cookies you have stored were not used — re-run
                      it with anonymous mode turned off.
                    </span>
                  )}
                </>
              ) : finalReason.reason === "rate-limited" ? (
                <>
                  <span className="font-semibold">⏸ 平台限制了请求频率，并非应用错误。</span>
                  <span>{String(finalReason.message ?? "")}</span>
                  {counts.downloaded + counts.skipped > 0 && (
                    <span className="opacity-80">
                      {counts.downloaded} file(s) downloaded before the block were saved — a later
                      re-run skips them and continues.
                    </span>
                  )}
                  {finalReason.resume_url && (
                    <span className="break-all font-mono text-xs opacity-80">
                      续传位置： {finalReason.resume_url}
                    </span>
                  )}
                </>
              ) : finalReason.reason === "downloads-dir-unwritable" ? (
                <>
                  <span className="font-semibold">下载目录没有写入权限。</span>
                  <span className="whitespace-pre-wrap break-all font-mono text-xs">
                    {String(finalReason.message ?? "")}
                  </span>
                </>
              ) : finalReason.reason === "no-progress" ? (
                <>
                  <span className="font-semibold">
                    超时前未找到文件。
                  </span>
                  <span className="whitespace-pre-wrap break-all font-mono text-xs">
                    {String(finalReason.message ?? "")}
                  </span>
                </>
              ) : (
                <span className="whitespace-pre-wrap break-all">
                  {String(finalReason.message ?? finalReason.reason ?? "Download failed")}
                </span>
              )}
            </div>
          )}
        </div>
      </div>

      <div className="card bg-base-100 shadow border border-base-300">
        <div className="card-body">
          <h3 className="font-semibold">任务动态</h3>
          <ul className="flex flex-col gap-1 max-h-96 overflow-y-auto font-mono text-xs">
            {events.map((ev, i) => (
              <li key={i} className="text-base-content/80">
                {describe(ev)}
              </li>
            ))}
            {!terminal && <li className="text-base-content/50 animate-pulse">working…</li>}
          </ul>
        </div>
      </div>
    </div>
  );
}
