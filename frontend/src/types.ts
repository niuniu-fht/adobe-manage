export interface HeartbeatPoint {
  ts: number;
  availability: number | null;
}

export interface InstanceSnapshot {
  ops_api_version: number;
  measured_at: number;
  instance: {
    service: string;
    version: string;
    build_sha?: string | null;
    started_at: number;
    uptime_seconds: number;
  };
  requests: {
    total: number;
    successful: number;
    failed: number;
    error_rate: number;
    duration_p50_seconds: number;
    duration_p95_seconds: number;
    in_progress: number;
    generated_images: number;
    generated_videos: number;
    today?: {
      total: number;
      successful: number;
      failed: number;
      generated_images: number;
      generated_videos: number;
    };
  };
  tokens: {
    total: number;
    active: number;
    status_counts: Record<string, number>;
    expiring_24h: number;
    credits_total: number;
    credits_available: number;
  };
  refresh_profiles: {
    total: number;
    failing: number;
    consecutive_failures_max: number;
  };
  storage: {
    generated_usage_bytes: number;
    generated_usage_mb: number;
    generated_file_count: number;
  };
}

export interface FleetInstance {
  id: string;
  name: string;
  location: string;
  base_url: string;
  enabled: boolean;
  tags: string[];
  state: "online" | "offline" | "unknown";
  consecutive_failures: number;
  last_seen_at?: number | null;
  last_failure_at?: number | null;
  last_error: string;
  latency_seconds?: number | null;
  ops_api_version?: number | null;
  capabilities: string[];
  snapshot?: InstanceSnapshot | null;
  heartbeat?: HeartbeatPoint[];
  active_alerts?: number;
  created_at: number;
  updated_at: number;
}

export interface TokenItem {
  id: string;
  value: string;
  status: string;
  fails: number;
  source: string;
  auto_refresh: boolean;
  auto_refresh_enabled?: boolean | null;
  refresh_profile_name?: string;
  refresh_profile_email?: string;
  credits_total?: number | null;
  credits_available?: number | null;
  credits_updated_at?: number | null;
  expires_at?: number | null;
  remaining_seconds?: number | null;
  instance_id: string;
  instance_name: string;
}

export interface LogItem {
  id: string;
  ts: number;
  method?: string;
  path?: string;
  status_code?: number;
  duration_sec?: number;
  operation?: string;
  model?: string;
  prompt?: string;
  prompt_preview?: string;
  task_status?: string;
  task_progress?: number;
  error?: string;
  error_code?: string;
  preview_url?: string;
  preview_kind?: string;
  instance_id: string;
  instance_name: string;
}

export interface AlertItem {
  id: number;
  instance_id: string;
  instance_name: string;
  rule_id: string;
  rule_name: string;
  state: string;
  severity: string;
  message: string;
  value?: number | null;
  opened_at: number;
  firing_at?: number | null;
  resolved_at?: number | null;
  updated_at: number;
}

export interface AuditItem {
  id: string;
  ts: number;
  instance_id?: string | null;
  instance_name: string;
  action: string;
  resource_type: string;
  resource_id: string;
  outcome: string;
  duration_seconds: number;
  request_id: string;
  detail: Record<string, unknown>;
}
