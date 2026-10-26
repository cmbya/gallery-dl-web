"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { JobControls } from "@/components/JobControls";
import { listJobs, type JobSummary } from "@/lib/api";

// Polling, not SSE: this is a dashboard over N jobs, and one poll of /api/jobs carries every
// counter the rows need. An EventSource per row would mean N concurrent streams for the same data.
const POLL_MS = 2000;

const PLATFORM_LABEL: Record<string, string> = { instagram: "IG", facebook: "FB" };

function statusBadge(status: string): string {
  const map: Record<string, string> = {
    queued: "badge-warning",
    running: "badge-info",
    paused: "badge-warning",
    completed: "badge-success",
    failed: "badge-error",
    cancelled: "badge-ghost",
  };
  return map[status] ?? "badge-ghost";
}

function elapsed(from: number | null, to: number | null): string {
  if (!from) return "—";
  const secs = Math.max(0, (to ?? Date.now() / 1000) - from);
  if (secs < 60) return `${Math.round(secs)}s`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m ${Math.round(secs % 60)}s`;
  return `${Math.floor(secs / 3600)}h ${Math.floor((secs % 3600) / 60)}m`;
}

function title(job: JobSummary): string {
  return job.profile ?? job.url.replace(/^https?:\/\/(www\.)?/, "");
}

/** Reason text for a terminal job, pulled out of the backend's final_summary. */
function outcome(job: JobSummary): string {
  const final = (job.final_summary ?? {}) as { reason?: string };
  if (job.status === "completed") return `${job.downloaded} 已下载，${job.skipped} 已跳过`;
  if (job.status === "cancelled") return "已手动停止";
  const reasons: Record<string, string> = { "rate-limited": "平台触发频率限制", "login-required": "需要登录 Cookie", "downloads-dir-unwritable": "下载目录无写入权限", "no-progress": "超时前未发现文件", "worker-crash": "下载进程异常退出", "cancelled": "已停止" };
  return reasons[final.reason ?? ""] ?? final.reason ?? "失败";
}

function JobRow({
  job,
  waitingAhead,
  onChanged,
}: {
  job: JobSummary;
  waitingAhead?: number;
  onChanged: () => void;
}) {
  // A resumed job goes back through `queued` while it waits for a slot; started_at tells the two
  // apart so the hint can say "resuming" instead of implying it never ran.
  const resuming = job.status === "queued" && job.started_at !== null;
  return (
    <li className="flex flex-wrap items-center gap-3 border-b border-base-300 px-4 py-3 last:border-0">
      <div className="min-w-0 flex-1">
        <Link href={`/jobs/${job.id}`} className="link-hover flex items-center gap-2 font-medium">
          <span className="badge badge-outline badge-sm">
            {PLATFORM_LABEL[job.platform] ?? job.platform}
          </span>
          <span className="truncate">{title(job)}</span>
        </Link>
        <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-base-content/70">
          <span className={`badge badge-sm ${statusBadge(job.status)}`}>{({ queued: "排队中", running: "下载中", paused: "已暂停", completed: "已完成", failed: "失败", cancelled: "已停止" }[job.status] ?? job.status)}</span>
          {/* Two jobs that ran with and without a session behave very differently; without this
              they are indistinguishable in the queue. */}
          {job.anonymous && (
            <span className="badge badge-ghost badge-sm" title="匿名模式：仅限公开内容">
              匿名
            </span>
          )}
          {job.status === "queued" && !resuming && (
            <span>
              等待中
              {waitingAhead ? ` — ${waitingAhead} 个任务排在前面` : " 等待空闲下载名额"}
            </span>
          )}
          {resuming && <span>恢复中——等待空闲下载名额</span>}
          <span className="font-mono">
            {job.downloaded} ✓ · {job.skipped} ↷
          </span>
          <span>{elapsed(job.started_at ?? job.created_at, job.ended_at)}</span>
        </div>
      </div>
      <JobControls jobId={job.id} status={job.status} onChanged={onChanged} />
    </li>
  );
}

export function QueueList() {
  const [jobs, setJobs] = useState<JobSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setJobs(await listJobs());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "could not load the queue");
    }
  }, []);

  useEffect(() => {
    let live = true;
    const tick = async () => {
      if (live) await refresh();
    };
    void tick();
    const timer = setInterval(tick, POLL_MS);
    return () => {
      live = false;
      clearInterval(timer);
    };
  }, [refresh]);

  if (error && jobs === null) return <div className="alert alert-error py-2 text-sm">{error}</div>;
  if (jobs === null) return <p className="text-base-content/60">正在加载…</p>;

  const active = jobs.filter((j) => !["completed", "failed", "cancelled"].includes(j.status));
  const recent = jobs.filter((j) => ["completed", "failed", "cancelled"].includes(j.status));
  // Position in line: how many other never-started jobs were created before this one.
  const queuedOrder = active
    .filter((j) => j.status === "queued" && j.started_at === null)
    .sort((a, b) => a.created_at - b.created_at)
    .map((j) => j.id);

  return (
    <div className="flex flex-col gap-4">
      <div className="card border border-base-300 bg-base-100 shadow">
        <div className="card-body gap-2 p-0">
          <h2 className="px-4 pt-4 font-semibold">
            进行中 <span className="text-base-content/60">({active.length})</span>
          </h2>
          {active.length === 0 ? (
            <p className="px-4 pb-4 text-sm text-base-content/60">
              当前没有正在下载的任务。{" "}
              <Link className="link" href="/">
                开始下载
              </Link>
              。
            </p>
          ) : (
            <ul className="flex flex-col">
              {active.map((job) => (
                <JobRow
                  key={job.id}
                  job={job}
                  waitingAhead={Math.max(0, queuedOrder.indexOf(job.id))}
                  onChanged={refresh}
                />
              ))}
            </ul>
          )}
        </div>
      </div>

      {recent.length > 0 && (
        <div className="card border border-base-300 bg-base-100 shadow">
          <div className="card-body gap-2 p-0">
            <h2 className="px-4 pt-4 font-semibold">最近任务</h2>
            <ul className="flex flex-col">
              {recent.map((job) => (
                <li
                  key={job.id}
                  className="flex flex-wrap items-center gap-3 border-b border-base-300 px-4 py-2 text-sm last:border-0"
                >
                  <Link href={`/jobs/${job.id}`} className="link-hover min-w-0 flex-1 truncate">
                    <span className="badge badge-outline badge-sm mr-2">
                      {PLATFORM_LABEL[job.platform] ?? job.platform}
                    </span>
                    {title(job)}
                  </Link>
                  {job.anonymous && (
                    <span
                      className="badge badge-ghost badge-sm"
                      title="匿名模式：仅限公开内容"
                    >
                      匿名
                    </span>
                  )}
                  <span className={`badge badge-sm ${statusBadge(job.status)}`}>{({ queued: "排队中", running: "下载中", paused: "已暂停", completed: "已完成", failed: "失败", cancelled: "已停止" }[job.status] ?? job.status)}</span>
                  <span className="text-xs text-base-content/60">{outcome(job)}</span>
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}

      <p className="text-xs text-base-content/60">
        任务列表保存在内存中：重启后会清空并停止正在运行的任务。重新下载同一博主时，会跳过磁盘上已有的文件。
      </p>
    </div>
  );
}
