import { QueueList } from "@/components/QueueList";

export default function QueuePage() {
  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-2xl font-bold">任务队列</h1>
      <p className="text-sm text-base-content/70">
        查看正在下载、排队等待或已暂停的任务。暂停任务会释放下载名额，让等待中的任务开始；停止任务会保留已下载的文件。
      </p>
      <QueueList />
    </div>
  );
}
