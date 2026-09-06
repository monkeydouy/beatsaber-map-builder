import type {
  BatchUploadResponse,
  YouTubeBatchResponse,
  DifficultyInfo,
  GenerationRequest,
  HealthInfo,
  Job,
  StyleInfo,
  YouTubePreview,
} from "@/types";

/**
 * The API base is read from the environment so the same build works in Docker
 * (where the browser still talks to localhost) and in local development.
 */
export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, "") ?? "http://localhost:8000";

export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/**
 * Pull the server's user-facing message out of a failed response.
 * The backend never puts internals in `detail`, so it is safe to display.
 */
async function readError(response: Response): Promise<never> {
  let message = "Something went wrong. Please try again.";
  try {
    const body = await response.json();
    if (typeof body?.detail === "string") {
      message = body.detail;
    } else if (Array.isArray(body?.detail) && body.detail[0]?.msg) {
      message = String(body.detail[0].msg);
    }
  } catch {
    /* non-JSON error body: keep the generic message */
  }
  throw new ApiError(message, response.status);
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: {
        ...(init?.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
        ...init?.headers,
      },
    });
  } catch {
    throw new ApiError("Could not reach the SaberMapper server.", 0);
  }
  if (!response.ok) {
    await readError(response);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export const api = {
  health: () => request<HealthInfo>("/api/health"),

  difficulties: () => request<DifficultyInfo[]>("/api/difficulties"),

  styles: () => request<StyleInfo[]>("/api/styles"),

  uploadFile: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<{ job_id: string; status: string }>("/api/jobs/upload", {
      method: "POST",
      body: form,
    });
  },

  uploadFiles: (files: File[]) => {
    const form = new FormData();
    for (const file of files) form.append("files", file);
    return request<BatchUploadResponse>("/api/jobs/upload/batch", {
      method: "POST",
      body: form,
    });
  },

  previewYouTube: (url: string) =>
    request<YouTubePreview>("/api/jobs/youtube/preview", {
      method: "POST",
      body: JSON.stringify({ url, confirmed: false }),
    }),

  createYouTubeJobs: (urls: string[]) =>
    request<YouTubeBatchResponse>("/api/jobs/youtube/batch", {
      method: "POST",
      body: JSON.stringify({ urls, confirmed: true }),
    }),

  createYouTubeJob: (url: string) =>
    request<{ job_id: string; status: string }>("/api/jobs/youtube", {
      method: "POST",
      body: JSON.stringify({ url, confirmed: true }),
    }),

  job: (jobId: string) => request<Job>(`/api/jobs/${jobId}`),

  generate: (jobId: string, payload: GenerationRequest) =>
    request<Job>(`/api/jobs/${jobId}/generate`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  downloadUrl: (jobId: string) => `${API_BASE}/api/jobs/${jobId}/download`,

  bundleUrl: (jobIds: string[]) =>
    `${API_BASE}/api/jobs/bundle/download?ids=${encodeURIComponent(jobIds.join(","))}`,
};
