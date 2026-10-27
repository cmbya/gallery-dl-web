// Typed wrappers for the backend JSON API. All paths are relative ("/api/..."); the catch-all
// route handler in src/app/api/[...path]/route.ts proxies them to the FastAPI backend, reading
// BACKEND_URL at request time (deliberately NOT next.config rewrites — those bake in at build).

export type AutomationMode = "refresh" | "history";
export interface SubscriptionInput {
  username: string;
  enabled: boolean;
  refresh_enabled: boolean;
  refresh_limit: number;
  history_enabled: boolean;
  history_limit: number;
}
export interface AutomationDiagnostic {
  stage: string;
  status?: number;
  target?: string;
  classification?: string;
}
export interface AutomationResult {
  diagnostic?: AutomationDiagnostic | null;
  status: string;
  checked: number;
  completed: number;
  skipped: number;
  failed: number;
  reason?: string | null;
  created_at?: number | string;
  file_count?: number;
}
export interface Subscription extends SubscriptionInput {
  id: string;
  history_status?: string | null;
  last_result?: AutomationResult | null;
}
export interface AutomationRun extends AutomationResult {
  id: string;
  username: string;
  mode: AutomationMode;
}
export interface CookieCheck {
  diagnostic?: AutomationDiagnostic | null;
  login_status: string;
  posts_status: string;
  checked_at?: number | string | null;
  reason?: string | null;
}
export interface AutomationState {
  subscriptions: Subscription[];
  paused: boolean;
  pause_reason: string | null;
  runs: AutomationRun[];
  cookie_check: CookieCheck | null;
}
async function automationRequest<T>(path: string, method = "GET", body?: unknown): Promise<T> {
  const response = await fetch(`/api/automation${path}`, {
    method, cache: "no-store",
    ...(body === undefined ? {} : { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
  });
  if (!response.ok) return asJson<T>(response);
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}
export const getAutomation = () => automationRequest<AutomationState>("");
export const addSubscription = (input: SubscriptionInput) => automationRequest<Subscription>("/subscriptions", "POST", input);
export const updateSubscription = (id: string, input: Partial<SubscriptionInput>) => automationRequest<Subscription>(`/subscriptions/${encodeURIComponent(id)}`, "PATCH", input);
export const deleteSubscription = (id: string) => automationRequest<void>(`/subscriptions/${encodeURIComponent(id)}`, "DELETE");
export const triggerAutomation = (mode: AutomationMode, subscriptionId?: string) => automationRequest<{ accepted: number; skipped: number; paused: boolean }>("/trigger", "POST", { mode, ...(subscriptionId ? { subscription_id: subscriptionId } : {}) });
export const pauseAutomation = (reason?: string) => automationRequest<unknown>("/pause", "POST", reason ? { reason } : {});
export const resumeAutomation = () => automationRequest<unknown>("/resume", "POST", {});
export const cancelAutomationRun = (id: string) => automationRequest<unknown>(`/runs/${encodeURIComponent(id)}/cancel`, "POST", {});
export const checkAutomationCookies = (username?: string) => automationRequest<CookieCheck>("/cookies/check", "POST", username ? { username } : {});
export const repositionHistory = (id: string) => automationRequest<{ reset: boolean }>(`/subscriptions/${encodeURIComponent(id)}/reposition`, "POST", {});
export const redownloadPost = (id: string, postId: string) => automationRequest<unknown>(`/subscriptions/${encodeURIComponent(id)}/redownload`, "POST", { post_id: postId });

export interface JobSummary {
  id: string;
  url: string;
  platform: string;
  /** Profile display name; null for single-post URLs. */
  profile: string | null;
  /** Ran logged-out — either opted into, or because no cookies are stored for the platform. */
  anonymous: boolean;
  status: string;
  created_at: number;
  started_at: number | null;
  ended_at: number | null;
  paused_at: number | null;
  downloaded: number;
  skipped: number;
  file_count: number;
  last_activity_ts: number | null;
  final_summary: Record<string, unknown> | null;
}

export type PacingMode = "adaptive" | "fixed";

/**
 * Pacing for one platform, on two independent axes.
 *
 * `min`/`max` space the extractor's API requests and mean different things per mode: in `adaptive`
 * they are the floor (and starting delay) and the back-off ceiling; in `fixed` they are the ends
 * of a random range sampled per request.
 *
 * `per_file` is the other axis and applies in BOTH modes: seconds between
 * the individual image downloads a single API request releases. One Instagram request returns ~30
 * posts whose images then download back to back, so `min`/`max` never touch them. 0 disables it;
 * `null` means "not specified" and inherits the server default.
 */
export interface Pacing {
  mode: PacingMode;
  min: number;
  max: number;
  per_file: number | null;
}

/** As the API reports it: the effective values, plus whether the operator set them. */
export interface PacingStatus extends Pacing {
  overridden: boolean;
}

export interface SettingsResponse {
  has_ig: boolean;
  has_fb: boolean;
  pacing: Partial<Record<"instagram" | "facebook", PacingStatus>>;
}

export interface FileEntry {
  path: string;
  name: string;
  size: number;
  mtime: number;
  platform: string;
}

export class ApiError extends Error {
  constructor(public status: number, message: string, public retryAfterSeconds?: number) { super(message); }
}

async function asJson<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = (body as { detail?: string }).detail ?? detail;
    } catch {
      /* keep statusText */
    }
    const retryAfter = res.headers.get("Retry-After");
    const seconds = retryAfter && /^\d+$/.test(retryAfter) ? Number(retryAfter) : undefined;
    throw new ApiError(res.status, typeof detail === "string" ? detail : "请求参数不符合要求", seconds);
  }
  return (await res.json()) as T;
}

export async function createJob(
  url: string,
  platform?: string | null,
  options?: Record<string, unknown>,
): Promise<{ job_id: string; status: string }> {
  const res = await fetch("/api/jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url, platform, options }),
  });
  return asJson(res);
}

export async function getJob(jobId: string): Promise<JobSummary> {
  return asJson(await fetch(`/api/jobs/${jobId}`));
}

export async function listJobs(activeOnly = false): Promise<JobSummary[]> {
  return asJson(await fetch(activeOnly ? "/api/jobs?active=1" : "/api/jobs"));
}

/**
 * Pause / resume / stop one job.
 *
 * Pause SIGSTOPs the worker and hands its concurrency slot to a queued profile; resume continues
 * the same worker where it left off; cancel is terminal but keeps the files already downloaded.
 * The backend answers 409 when the action does not apply to the job's current state.
 */
async function controlJob(jobId: string, action: string): Promise<JobSummary> {
  return asJson(await fetch(`/api/jobs/${jobId}/${action}`, { method: "POST" }));
}

export const pauseJob = (jobId: string) => controlJob(jobId, "pause");
export const resumeJob = (jobId: string) => controlJob(jobId, "resume");
export const cancelJob = (jobId: string) => controlJob(jobId, "cancel");

export async function getSettings(): Promise<SettingsResponse> {
  return asJson(await fetch("/api/settings"));
}

/** Set one platform's pacing override, or pass `null` to fall back to the server's env default. */
export async function updatePacing(
  platform: "instagram" | "facebook",
  pacing: Pacing | null,
): Promise<SettingsResponse> {
  const res = await fetch("/api/settings/pacing", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ platform, pacing }),
  });
  return asJson(res);
}

export async function updateCookies(body: {
  ig_sessionid?: string;
  ig_cookies_text?: string;
  fb_cookies_text?: string;
}): Promise<SettingsResponse> {
  const res = await fetch("/api/settings/cookies", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return asJson(res);
}

/**
 * A *page* of the downloads listing, newest first.
 *
 * `total` is what is on disk, not `files.length`. A real install holds hundreds of thousands, and
 * serialising all of them was tens of megabytes of JSON for a table that shows a screenful.
 */
export interface FileListResponse {
  files: FileEntry[];
  total: number;
  truncated: boolean;
}

export async function listFiles(limit?: number): Promise<FileListResponse> {
  const qs = limit === undefined ? "" : `?limit=${limit}`;
  return asJson(await fetch(`/api/files${qs}`));
}

export function downloadUrl(path: string): string {
  return `/api/files/download?path=${encodeURIComponent(path)}`;
}

export function jobZipUrl(jobId: string): string {
  return `/api/jobs/${jobId}/zip`;
}

export interface ProfileFileEntry {
  filename: string;
  path: string;
  bytes: number;
  mtime: number;
  kind: string;
  media_id: string | null;
  thumb_url: string;
  file_url: string;
}

export interface ProfileSummary {
  platform: string;
  name: string;
  avatar_url: string | null;
  image_count: number;
  video_count: number;
  total_bytes: number;
  last_updated: number;
}

export interface ProfileMetadata {
  platform: string;
  name: string;
  avatar: string | null;
  avatar_url: string | null;
  images: ProfileFileEntry[];
  videos: ProfileFileEntry[];
  image_count: number;
  video_count: number;
  total_bytes: number;
  last_updated: number;
}

export async function listProfiles(): Promise<ProfileSummary[]> {
  return asJson(await fetch("/api/profiles"));
}

export async function getProfile(platform: string, name: string): Promise<ProfileMetadata> {
  return asJson(await fetch(`/api/profiles/${platform}/${encodeURIComponent(name)}`));
}

export function profileZipUrl(platform: string, name: string): string {
  return `/api/profiles/${platform}/${encodeURIComponent(name)}/zip`;
}

export async function deleteProfile(platform: string, name: string): Promise<void> {
  const res = await fetch(`/api/profiles/${platform}/${encodeURIComponent(name)}`, {
    method: "DELETE",
  });
  if (!res.ok && res.status !== 204) {
    throw new Error(`delete failed: ${res.status}`);
  }
}
