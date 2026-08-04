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
      safety_review_failed?: number | null;
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
  accounts?: AccountSummary;
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

export interface AccountSummary {
  total: number;
  available: number;
  low_credit: number;
  balance_unknown: number;
  refresh_failing: number;
  credential_error: number;
  credits_available: number;
  credits_total: number;
  low_credit_threshold: number;
}

export type AccountHealth =
  | "healthy"
  | "low_credit"
  | "balance_unknown"
  | "refresh_failed"
  | "credential_error"
  | "disabled";

export interface AccountItem {
  id: string;
  name: string;
  display_name: string;
  email: string;
  user_id: string;
  enabled: boolean;
  health: AccountHealth;
  low_credit: boolean;
  credits_available?: number | null;
  credits_total?: number | null;
  credits_updated_at?: number | null;
  credential_status: string;
  credential_expires_at?: number | null;
  consecutive_failures: number;
  last_attempt_at?: number | null;
  last_success_at?: number | null;
  next_refresh_at?: number | null;
  last_error: string;
  imported_at?: number | null;
  instance_id: string;
  instance_name: string;
  duplicate: boolean;
  duplicate_instances?: string[];
}

export interface AccountsResponse {
  status: "ok" | "partial";
  low_credit_threshold: number;
  accounts: AccountItem[];
  summary?: AccountSummary;
  instance_summaries?: Record<string, AccountSummary>;
  errors?: { instance_id: string; instance_name: string; detail: string }[];
}

export interface AccountMoveResponse {
  status: "ok" | "partial" | "failed";
  source: { id: string; name: string };
  target: { id: string; name: string };
  requested_count: number;
  exported_count: number;
  imported_count: number;
  moved_count: number;
  retained_count: number;
  export_missing_count: number;
  import_failed_count: number;
  refresh_failed_count: number;
  cleanup_failed_count: number;
  source_state_unknown_count: number;
}

export interface AccountSafeReplaceResponse {
  status: "ok" | "partial";
  message: string;
  source_email: string;
  replacement_email: string;
  replacement_profile_id: string;
  imported_count: number;
  refresh_failed_count: number;
  old_profile_removed: boolean;
}

export interface AccountSafeReplaceOperation {
  id: string;
  status: "starting" | "running" | "finalizing" | "done" | "partial" | "failed" | "cancelled";
  phase: "starting" | "pulling" | "importing" | "cleanup" | "complete" | "failed" | "cancelled";
  upstream_job_id?: number | null;
  target: number;
  success: number;
  fail: number;
  logs: string[];
  error: string;
  result?: AccountSafeReplaceResponse | null;
  created_at: number;
  updated_at: number;
  can_cancel: boolean;
  cancel_requested: boolean;
}

export interface AutoReplacementOperation {
  id: string;
  instance_id: string;
  instance_name: string;
  profile_id: string;
  source_email: string;
  trigger: string;
  credits_available?: number | null;
  credit_threshold: number;
  health: string;
  status: "queued" | "running" | "done" | "partial" | "failed" | "skipped";
  phase: "queued" | "checking" | "local_removal" | "mother_replacement" | "importing" | "complete" | "failed";
  upstream_job_id?: number | null;
  logs: string[];
  error: string;
  replacement_email: string;
  remove_only: boolean;
  created_at: number;
  updated_at: number;
}

export interface AutoReplacementsResponse {
  active_id?: string | null;
  active: boolean;
  queued: number;
  operations: AutoReplacementOperation[];
  settings: {
    credit_threshold: number;
    refresh_interval_minutes: number;
    enabled: boolean;
    refill_mode: "new_domain" | "registered_reuse";
  };
  credit_refresh: {
    running: boolean;
    started_at?: number | null;
    finished_at?: number | null;
    next_refresh_at?: number | null;
    instances: number;
    succeeded_instances: number;
    failed_instances: number;
    errors: { instance_id: string; instance_name: string; error: string }[];
  };
}

export interface ImageQueueOutput {
  index: number;
  state: string;
  token_id?: string | null;
  account_name?: string | null;
  upstream_job_id?: string | null;
  retry_count: number;
  next_run_at?: number | null;
  rate_limit_wait_seconds: number;
  download_attempt: number;
  last_error?: string | null;
  updated_at?: number | null;
}

export interface ImageQueueRequest {
  id: string;
  log_id: string;
  instance_id: string;
  instance_name: string;
  instance_location: string;
  path: string;
  model: string;
  prompt_preview: string;
  requested_count: number;
  completed_count: number;
  state: string;
  created_at?: number | null;
  updated_at?: number | null;
  finished_at?: number | null;
  elapsed_seconds: number;
  error?: string | null;
  outputs: ImageQueueOutput[];
}

export interface ImageQueueResponse {
  status: "ok" | "partial";
  summary: {
    instances: number;
    instances_ok: number;
    instances_error: number;
    requests: number;
    outputs: number;
    in_progress: number;
    queued: number;
    waiting_poll: number;
    rate_limited: number;
    download_retry: number;
  };
  instances: {
    instance_id: string;
    instance_name: string;
    state: "ok" | "error";
    summary: Record<string, number>;
    error: string;
  }[];
  items: ImageQueueRequest[];
  errors: { instance_id: string; instance_name: string; detail: string }[];
  updated_at: number;
}

export interface FleetCreditsRefreshResponse {
  status: "ok" | "partial";
  summary: {
    instances: number;
    succeeded_instances: number;
    partial_instances: number;
    failed_instances: number;
    refreshed_count: number;
    failed_count: number;
  };
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
