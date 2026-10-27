import type { AutomationDiagnostic as Diagnostic } from "@/lib/api";

const stages: Record<string, string> = {
  profile: "用户页", identity: "登录身份接口", graphql: "帖子查询接口", api: "数据接口",
  media: "媒体页面", home: "首页", login: "登录页", challenge: "安全验证页", other: "其他端点", unknown: "未知端点",
};
export function AutomationDiagnostic({ diagnostic }: { diagnostic?: Diagnostic | null }) {
  if (!diagnostic) return null;
  const status = Number.isInteger(diagnostic.status) && diagnostic.status! >= 100 && diagnostic.status! <= 599 ? diagnostic.status : null;
  return <p className="text-sm break-words">诊断：{stages[diagnostic.stage] ?? "未知端点"}{status && ` · HTTP ${status}`}{diagnostic.target && ` → ${stages[diagnostic.target] ?? "未知端点"}`}</p>;
}
