import type { ArchiveProgress as Progress } from "@/lib/api";
import { formatTime } from "@/lib/automation";

export function ArchiveProgress({ progress }: { progress?: Progress }) {
  if (!progress) return <p className="text-sm">累计归档：进度暂不可用</p>;
  return <div className="text-sm flex flex-col gap-1" aria-label="累计归档进度">
    <p>{progress.platform_total === null
      ? `已归档${progress.completed}帖·总数未知`
      : `累计归档：${progress.completed} / 约${progress.platform_total}帖`}</p>
    {progress.platform_total !== null && <p className="text-base-content/70">平台参考总数，可能包含 Reels 或不可访问内容，与本地归档范围不完全一致。观测时间：{formatTime(progress.total_updated_at)}</p>}
    {progress.history_complete && <>
      <p className="font-medium">历史补全完成·已归档{progress.completed}帖</p>
      <p className="text-base-content/70">仅表示当前可访问历史已处理。平台内容变化后不会自动重新扫描全部历史；需要时可手动重新定位历史进度。可关闭历史补全并保留最新刷新。</p>
    </>}
  </div>;
}
