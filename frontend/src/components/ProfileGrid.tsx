"use client";

import { useEffect, useState } from "react";
import Link from "next/link";

import { listProfiles, type ProfileSummary } from "@/lib/api";

export function ProfileGrid() {
  const [profiles, setProfiles] = useState<ProfileSummary[] | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    listProfiles()
      .then(setProfiles)
      .catch((e: unknown) => setErr(e instanceof Error ? e.message : "failed to load"));
  }, []);

  if (err) return <div className="alert alert-error">{err}</div>;
  if (profiles === null) return <p className="text-base-content/60">正在加载…</p>;
  if (profiles.length === 0)
    return (
      <p className="text-base-content/60">
        还没有博主记录。提交 Instagram 或 Facebook 主页链接开始下载。
      </p>
    );

  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-4">
      {profiles.map((p) => (
        <Link
          key={`${p.platform}-${p.name}`}
          href={`/profiles/${p.platform}/${encodeURIComponent(p.name)}`}
          className="card bg-base-100 shadow border border-base-300 hover:shadow-md transition-shadow"
        >
          <figure className="aspect-square overflow-hidden bg-base-200">
            {p.avatar_url ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={p.avatar_url} alt={p.name} className="w-full h-full object-cover" />
            ) : (
              <div className="w-full h-full flex items-center justify-center text-5xl">👤</div>
            )}
          </figure>
          <div className="card-body p-3 gap-1">
            <div className="font-semibold truncate">{p.name}</div>
            <div className="text-xs text-base-content/60 flex items-center gap-2 flex-wrap">
              <span className="badge badge-sm">{p.platform}</span>
              <span>{p.image_count} 📷</span>
              {p.video_count > 0 && <span>{p.video_count} 🎬</span>}
            </div>
          </div>
        </Link>
      ))}
    </div>
  );
}
