export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

let csrfToken = "";

export function setCsrfToken(value: string) {
  csrfToken = value;
}

export async function apiFetch<T>(path: string, options: RequestInit = {}): Promise<T> {
  const method = String(options.method || "GET").toUpperCase();
  const headers = new Headers(options.headers || {});
  if (options.body && !(options.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }
  if (!["GET", "HEAD", "OPTIONS"].includes(method) && csrfToken) {
    headers.set("X-CSRF-Token", csrfToken);
  }
  const response = await fetch(`/api${path}`, {
    ...options,
    method,
    headers,
    credentials: "include"
  });
  if (!response.ok) {
    let message = `HTTP ${response.status}`;
    try {
      const payload = await response.json();
      message = String(payload.detail || payload.message || message);
    } catch {
      // Keep the status-based message.
    }
    throw new ApiError(response.status, message);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export async function apiDownload(path: string, body: unknown, filename: string) {
  const response = await fetch(`/api${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
    credentials: "include",
    body: JSON.stringify(body)
  });
  if (!response.ok) throw new ApiError(response.status, `Download failed: HTTP ${response.status}`);
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

export function emitToast(message: string, tone: "success" | "error" | "info" = "info") {
  window.dispatchEvent(new CustomEvent("manager-toast", { detail: { message, tone } }));
}

export function formatTime(ts?: number | null) {
  if (!ts) return "-";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit"
  }).format(new Date(ts * 1000));
}

export function formatDuration(seconds?: number | null) {
  if (seconds == null) return "-";
  if (seconds < 1) return `${Math.round(seconds * 1000)} ms`;
  if (seconds < 60) return `${seconds.toFixed(1)} s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${Math.floor(seconds % 60)}s`;
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
}

export function formatNumber(value?: number | null, digits = 0) {
  if (value == null || Number.isNaN(value)) return "-";
  return new Intl.NumberFormat("zh-CN", { maximumFractionDigits: digits }).format(value);
}
