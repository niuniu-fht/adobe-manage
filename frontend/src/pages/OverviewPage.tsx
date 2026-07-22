import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, ArrowRight, Gauge, RefreshCw, Server, TicketCheck } from "lucide-react";
import { Link } from "react-router-dom";
import { Heartbeat } from "../components/Heartbeat";
import { StatusBadge } from "../components/StatusBadge";
import { apiFetch, emitToast, formatDuration, formatNumber, formatTime } from "../lib/api";
import type { FleetInstance } from "../types";

interface DashboardResponse {
  instances: FleetInstance[];
  summary: { total: number; online: number; offline: number; active_alerts: number };
  updated_at: number;
}

export function OverviewPage() {
  const queryClient = useQueryClient();
  const dashboard = useQuery({
    queryKey: ["dashboard"],
    queryFn: () => apiFetch<DashboardResponse>("/dashboard"),
    refetchInterval: 30000
  });
  const poll = useMutation({
    mutationFn: () => apiFetch("/poll", { method: "POST" }),
    onSuccess: () => {
      emitToast("采集已完成", "success");
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
    },
    onError: (error) => emitToast(error.message, "error")
  });

  const data = dashboard.data;
  const aggregate = (data?.instances || []).reduce(
    (acc, instance) => {
      const snapshot = instance.snapshot;
      acc.tokens += snapshot?.tokens.active || 0;
      acc.credits += snapshot?.tokens.credits_available || 0;
      acc.jobs += snapshot?.requests.in_progress || 0;
      return acc;
    },
    { tokens: 0, credits: 0, jobs: 0 }
  );

  return (
    <div className="page-stack">
      <section className="page-toolbar">
        <div>
          <strong>{data ? `${data.summary.online}/${data.summary.total} 个实例在线` : "正在读取实例状态"}</strong>
          <span>最后采集 {formatTime(data?.updated_at)}</span>
        </div>
        <button className="secondary-btn" onClick={() => poll.mutate()} disabled={poll.isPending}>
          <RefreshCw size={16} className={poll.isPending ? "spin" : ""} />立即采集
        </button>
      </section>

      <section className="metric-band">
        <div><Server size={18} /><span>在线实例</span><strong>{data?.summary.online ?? "-"}</strong></div>
        <div><TicketCheck size={18} /><span>活跃 Token</span><strong>{aggregate.tokens}</strong></div>
        <div><Gauge size={18} /><span>剩余积分</span><strong>{formatNumber(aggregate.credits, 1)}</strong></div>
        <div className={(data?.summary.active_alerts || 0) > 0 ? "metric-alert" : ""}>
          <AlertTriangle size={18} /><span>当前告警</span><strong>{data?.summary.active_alerts ?? "-"}</strong>
        </div>
      </section>

      <section className="fleet-section">
        <div className="section-head"><div><h2>实例运行带</h2><span>最近 7 天逐小时可用性</span></div></div>
        {dashboard.isLoading && <div className="empty-row">正在加载...</div>}
        {dashboard.isError && <div className="error-banner">{dashboard.error.message}</div>}
        {data?.instances.length === 0 && (
          <div className="empty-row">尚未登记实例 <Link to="/instances">添加实例</Link></div>
        )}
        <div className="fleet-list">
          {data?.instances.map((instance) => {
            const snapshot = instance.snapshot;
            const todaySuccessful = snapshot?.requests.today?.successful
              ?? snapshot?.requests.successful
              ?? Math.max(0, (snapshot?.requests.total || 0) - (snapshot?.requests.failed || 0));
            const todayFailed = snapshot?.requests.today?.failed ?? snapshot?.requests.failed;
            return (
              <article className="fleet-row" key={instance.id}>
                <div className="fleet-identity">
                  <StatusBadge status={instance.state} />
                  <div><Link to={`/instances/${instance.id}`}>{instance.name}</Link><span>{instance.location || instance.base_url}</span></div>
                </div>
                <div className="fleet-stat fleet-secondary"><span>延迟</span><strong>{formatDuration(instance.latency_seconds)}</strong></div>
                <div className="fleet-stat fleet-secondary"><span>Token</span><strong>{snapshot ? `${snapshot.tokens.active}/${snapshot.tokens.total}` : "-"}</strong></div>
                <div className="fleet-stat fleet-secondary"><span>积分</span><strong>{formatNumber(snapshot?.tokens.credits_available, 1)}</strong></div>
                <div className="fleet-stat"><span>进行中</span><strong>{snapshot?.requests.in_progress ?? "-"}</strong></div>
                <div className="fleet-stat"><span>今日成功</span><strong className="text-success">{snapshot ? todaySuccessful : "-"}</strong></div>
                <div className="fleet-stat"><span>今日失败</span><strong className={Number(todayFailed || 0) > 0 ? "text-danger" : ""}>{snapshot ? todayFailed : "-"}</strong></div>
                <div className="fleet-stat"><span>错误率</span><strong className={(snapshot?.requests.error_rate || 0) > 0.2 ? "text-danger" : ""}>{snapshot ? `${(snapshot.requests.error_rate * 100).toFixed(1)}%` : "-"}</strong></div>
                <div className="fleet-mobile-counts"><span>进行中 <strong>{snapshot?.requests.in_progress ?? "-"}</strong></span><span>成功 <strong>{snapshot ? todaySuccessful : "-"}</strong></span><span>失败 <strong className={Number(todayFailed || 0) > 0 ? "text-danger" : ""}>{snapshot ? todayFailed : "-"}</strong></span></div>
                <div className="fleet-heartbeat"><Heartbeat points={instance.heartbeat} /></div>
                <Link className="icon-btn" to={`/instances/${instance.id}`} title="查看实例"><ArrowRight size={18} /></Link>
              </article>
            );
          })}
        </div>
      </section>
    </div>
  );
}
