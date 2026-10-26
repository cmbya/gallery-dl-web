import { UrlForm } from "@/components/UrlForm";

export default function Home() {
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-1">
        <h1 className="text-3xl font-bold">下载 Instagram 和 Facebook 图片与视频</h1>
        <p className="text-base-content/70">
          粘贴帖子、Reel 短视频或个人主页链接，通过{" "}
          <a
            className="link"
            href="https://github.com/mikf/gallery-dl"
            target="_blank"
            rel="noreferrer"
          >
            gallery-dl
          </a>{" "}
          下载，进度会实时显示。
        </p>
      </div>
      <UrlForm />
    </div>
  );
}
