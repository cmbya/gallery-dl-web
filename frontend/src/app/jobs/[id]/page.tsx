"use client";

import { useParams } from "next/navigation";
import { JobProgress } from "@/components/JobProgress";

export default function JobPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  if (!id) return <p className="text-base-content/60">正在加载…</p>;
  return <JobProgress jobId={id} />;
}
