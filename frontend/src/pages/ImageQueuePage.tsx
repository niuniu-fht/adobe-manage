import { useQuery } from "@tanstack/react-query";
import { Download, Gauge, ListTodo, RefreshCw, Server, TimerReset } from "lucide-react";
import { useMemo, useState } from "react";
import { apiFetch, formatDuration, formatTime } from "../lib/api";
import type { ImageQueueRequest, ImageQueueResponse } from "../types";

const stateLabels: Record<string, string> = {
  QUEUED: "排队",
  UPLOADING: "上传中",
  SUBMITTING: "提交中",
  WAITING_POLL: "等待 Poll",
  RATE_LIMITED: "429 等待",
  DOWNLOADING: "下载中",
  DOWNLOAD_RETRY: "下载重试",
  COMPLETED: "已完成",
  FAILED: "失败"
};

function stateBadge(state: string) {
  const normalized = String(state || "QUEUED").toUpperCase();
  return <span className={`queue-state queue-state-${normalized.toLowerCase().replaceAll("_", "-")}`}>{stateLabels[normalized] || normalized}</span>;
}

function nextRunLabel(nextRunAt?: number | null) {
  if (!nextRunAt) return "-";
  const remaining = nextRunAt - Date.now() / 1000;
  return remaining <= 0 ? "即将执行" : `${remaining.toFixed(1)} 秒`;
}

function QueueRequestRow({ item }: { item: ImageQueueRequest }) {
  const terminal = item.state === "COMPLETED" || item.state === "FAILED";
  const [open, setOpen] = useState(!terminal);
  return <details className="manager-queue-request" open={open} onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary>
      <div className="manager-queue-title">
        {stateBadge(item.state)}
        <strong>{item.instance_name}</strong>
        <span>{item.model || "-"}</span>
        <code>{item.path || "-"}</code>
      </div>
      <div className="manager-queue-progress">
        <strong>{item.completed_count} / {item.requested_count}</strong>
        <span>{formatDuration(item.elapsed_seconds)}</span>
      </div>
    </summary>
    <div className="manager-queue-meta">
      <span>Log ID <code>{item.log_id}</code></span>
      <span>创建 {formatTime(item.created_at)}</span>
      <span className="manager-queue-prompt">提示词 {item.prompt_preview || "-"}</span>
      {item.error && <span className="text-danger">{item.error}</span>}
    </div>
    <div className="table-scroll manager-queue-output-scroll">
      <table className="manager-queue-output-table">
        <thead><tr><th>Output</th><th>状态</th><th>账号</th><th>Adobe Job</th><th>重试</th><th>下次执行</th><th>429 累计</th><th>下载次数</th><th>最近错误</th></tr></thead>
        <tbody>
          {item.outputs.map((output) => <tr key={`${item.id}-${output.index}`}>
            <td>#{output.index + 1}</td>
            <td>{stateBadge(output.state)}</td>
            <td title={output.account_name || output.token_id || ""}>{output.account_name || output.token_id || "-"}</td>
            <td title={output.upstream_job_id || ""}><code>{output.upstream_job_id || "-"}</code></td>
            <td>{output.retry_count}</td>
            <td>{nextRunLabel(output.next_run_at)}</td>
            <td>{output.rate_limit_wait_seconds.toFixed(1)} 秒</td>
            <td>{output.download_attempt}</td>
            <td title={output.last_error || ""}>{output.last_error || "-"}</td>
          </tr>)}
          {!item.outputs.length && <tr><td colSpan={9} className="empty-row">暂无 output</td></tr>}
        </tbody>
      </table>
    </div>
  </details>;
}

export function ImageQueuePage() {
  const [instanceId, setInstanceId] = useState("");
  const queue = useQuery({
    queryKey: ["image-queue"],
    queryFn: () => apiFetch<ImageQueueResponse>("/image-queue?limit_per_instance=200"),
    refetchInterval: 2000,
    refetchIntervalInBackground: false
  });
  const rows = useMemo(
    () => (queue.data?.items || []).filter((item) => !instanceId || item.instance_id === instanceId),
    [queue.data?.items, instanceId]
  );
  const summary = queue.data?.summary;

  return <div className="page-stack">
    <section className="page-toolbar">
      <div><strong>跨实例图片队列</strong><span>更新于 {formatTime(queue.data?.updated_at)} · 2 秒刷新</span></div>
      <button className="secondary-btn" onClick={() => queue.refetch()} disabled={queue.isFetching}><RefreshCw size={16} className={queue.isFetching ? "spin" : ""} />刷新</button>
    </section>

    <section className="queue-metric-band">
      <div><Server size={18} /><span>正常实例</span><strong>{summary ? `${summary.instances_ok}/${summary.instances}` : "-"}</strong></div>
      <div><ListTodo size={18} /><span>请求</span><strong>{summary?.requests ?? "-"}</strong></div>
      <div><Gauge size={18} /><span>正在进行</span><strong>{summary?.in_progress ?? "-"}</strong></div>
      <div><TimerReset size={18} /><span>等待 Poll</span><strong>{summary?.waiting_poll ?? "-"}</strong></div>
      <div className={(summary?.rate_limited || 0) > 0 ? "queue-metric-warn" : ""}><TimerReset size={18} /><span>429 等待</span><strong>{summary?.rate_limited ?? "-"}</strong></div>
      <div className={(summary?.download_retry || 0) > 0 ? "queue-metric-warn" : ""}><Download size={18} /><span>下载重试</span><strong>{summary?.download_retry ?? "-"}</strong></div>
    </section>

    <section className="filter-bar manager-queue-filter">
      <select value={instanceId} onChange={(event) => setInstanceId(event.target.value)}>
        <option value="">全部实例</option>
        {queue.data?.instances.map((instance) => <option key={instance.instance_id} value={instance.instance_id}>{instance.instance_name}</option>)}
      </select>
      <span>{rows.length} 个请求 · {summary?.outputs ?? 0} 个 output</span>
    </section>

    {queue.data?.errors.map((error) => <div key={error.instance_id} className="warning-banner">{error.instance_name}: {error.detail}</div>)}
    {queue.isError && <div className="error-banner">{queue.error.message}</div>}
    {queue.isLoading && <div className="empty-row">正在读取所有实例队列...</div>}
    {!queue.isLoading && !rows.length && <div className="empty-row">当前没有图片队列任务</div>}
    <section className="manager-queue-list">
      {rows.map((item) => <QueueRequestRow key={`${item.instance_id}-${item.id}`} item={item} />)}
    </section>
  </div>;
}
