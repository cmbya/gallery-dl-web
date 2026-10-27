import { AutomationDiagnostic } from "@/components/AutomationDiagnostic";
import type { AutomationResult as Result } from "@/lib/api";
import { formatTime, reasonLabel, statusLabel } from "@/lib/automation";

export function AutomationResult({ result }: { result?: Result | null }) {
  if (!result) return <p className="text-sm text-base-content/70">尚无运行结果</p>;
  return <div className="text-sm flex flex-col gap-1">
    <p>状态：{statusLabel(result.status)} · 时间：{formatTime(result.created_at)}</p>
    <dl className="flex flex-wrap gap-x-4 gap-y-1" aria-label="帖子处理统计">
      <div className="flex gap-1"><dt>已检查帖子</dt><dd>{result.checked}</dd></div>
      <div className="flex gap-1"><dt>本轮新增完成</dt><dd>{result.completed}</dd></div>
      <div className="flex gap-1"><dt>已跳过帖子</dt><dd>{result.skipped}</dd></div>
      <div className="flex gap-1"><dt>失败帖子</dt><dd>{result.failed}</dd></div>
    </dl>
    {result.file_count !== undefined && <p>下载文件数（非帖子数）：{result.file_count}</p>}
    <AutomationDiagnostic diagnostic={result.diagnostic} />
    {result.reason && <p className="break-words">说明：{reasonLabel(result.reason)}</p>}
  </div>;
}
