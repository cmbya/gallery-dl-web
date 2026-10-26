import { ProfileGrid } from "@/components/ProfileGrid";

export default function ProfilesPage() {
  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-2xl font-bold">博主图库</h1>
      <p className="text-sm text-base-content/70">
        已下载的 Instagram / Facebook 博主会按用户名整理在这里。
      </p>
      <ProfileGrid />
    </div>
  );
}
