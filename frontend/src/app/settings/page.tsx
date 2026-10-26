import { CookieForm } from "@/components/CookieForm";
import { PacingSetting } from "@/components/PacingSetting";
import { ThemeSetting } from "@/components/ThemeSetting";

export default function SettingsPage() {
  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-2xl font-bold">设置</h1>
      <p className="text-sm text-base-content/70">
        登录凭证仅保存在后端的 <code>/data/cookies.json</code>（权限为 0600，且已加入 Git 忽略）。API 不会返回 Cookie 内容，只会显示是否已配置。
      </p>
      <p className="text-sm text-base-content/70">
        Cookie <b>非必需</b>：未保存 Cookie 时，任务会以匿名方式下载公开主页内容。需要访问私密或受限内容，或希望 Instagram 下载更稳定时，再添加 Cookie。匿名访问时 Instagram 不会下载快拍、精选内容和收藏帖子。
      </p>

      <div className="alert alert-info flex flex-col items-start gap-2 py-3">
        <div className="font-semibold">📷 最简单的方式：使用浏览器扩展</div>
        <div className="text-sm">
          在 Chrome / Edge / Brave 中加载未打包的 <code>extension/</code> 文件夹，在扩展弹窗中填入本服务地址，然后在登录状态下点击 <b>发送 Instagram 会话</b> /{" "}
          <b>发送 Facebook Cookie</b>。扩展会读取你自己的 Cookie 并发送到此处——一键导入，之后也可刷新。详见{" "}
          <a
            className="link"
            href="https://github.com/lumduan/gallery-dl-web/blob/main/扩展安装说明"
            target="_blank"
            rel="noreferrer"
          >
            扩展安装说明
          </a>
          .
        </div>
      </div>

      <div className="divider text-xs text-base-content/50">或在下方手动粘贴</div>

      <CookieForm />

      <div className="divider text-xs text-base-content/50">下载节奏</div>

      <PacingSetting />

      <div className="divider text-xs text-base-content/50">界面外观</div>

      <ThemeSetting />
    </div>
  );
}
