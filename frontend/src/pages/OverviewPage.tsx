import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, ArrowRight, CheckCircle2, ChevronDown, Gauge, LoaderCircle, RefreshCw, Save, Server, Trash2, Upload, UsersRound, WalletCards } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { AccountTable } from "../components/AccountTable";
import { AutoReplacementConsole } from "../components/AutoReplacementConsole";
import { AccountBatchBar } from "../components/AccountBatchBar";
import { AccountMoveModal } from "../components/AccountMoveModal";
import { CookieImportModal } from "../components/CookieImportModal";
import { FleetImportModal, FleetLowCreditDeleteModal } from "../components/FleetAccountActions";
import { Heartbeat } from "../components/Heartbeat";
import { StatusBadge } from "../components/StatusBadge";
import { apiFetch, emitToast, formatDuration, formatNumber, formatTime } from "../lib/api";
import type { AccountsResponse, FleetCreditsRefreshResponse, FleetInstance } from "../types";

interface DashboardResponse {
  instances: FleetInstance[];
  summary: { total: number; online: number; offline: number; active_alerts: number; total_success: number; total_in_progress: number };
  preferences: { low_credit_threshold: number; account_targets: Record<string, number> };
  updated_at: number;
}

export function OverviewPage() {
  const queryClient = useQueryClient();
  const [expandedId, setExpandedId] = useState("");
  const [threshold, setThreshold] = useState("100");
  const [fleetImportOpen, setFleetImportOpen] = useState(false);
  const [lowCreditDeleteOpen, setLowCreditDeleteOpen] = useState(false);
  const dashboard = useQuery({
    queryKey: ["dashboard"],
    queryFn: () => apiFetch<DashboardResponse>("/dashboard"),
    refetchInterval: 30000
  });
  useEffect(() => {
    if (dashboard.data?.preferences.low_credit_threshold != null) {
      setThreshold(String(dashboard.data.preferences.low_credit_threshold));
    }
  }, [dashboard.data?.preferences.low_credit_threshold]);

  const poll = useMutation({
    mutationFn: () => apiFetch("/poll", { method: "POST" }),
    onSuccess: () => {
      emitToast("采集已完成", "success");
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
    },
    onError: (error) => emitToast(error.message, "error")
  });
  const refreshFleetCredits = useMutation({
    mutationFn: () => apiFetch<FleetCreditsRefreshResponse>("/fleet/tokens/credits-batch", { method: "POST" }),
    onSuccess: async (payload) => {
      const summary = payload.summary;
      emitToast(
        `额度刷新完成：成功 ${summary.refreshed_count}，失败 ${summary.failed_count}，异常实例 ${summary.failed_instances}`,
        payload.status === "ok" ? "success" : "info"
      );
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["dashboard"] }),
        queryClient.invalidateQueries({ queryKey: ["accounts"] }),
        queryClient.invalidateQueries({ queryKey: ["instance-accounts"] })
      ]);
    },
    onError: (error) => emitToast(error.message, "error")
  });
  const saveThreshold = useMutation({
    mutationFn: () => {
      const value = Number(threshold);
      if (!Number.isFinite(value) || value < 0) throw new Error("低积分阈值需要填写非负数字");
      return apiFetch<{ low_credit_threshold: number }>("/settings/preferences", {
        method: "PUT",
        body: JSON.stringify({ low_credit_threshold: value })
      });
    },
    onSuccess: async (payload) => {
      setThreshold(String(payload.low_credit_threshold));
      emitToast("低积分阈值已保存", "success");
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["dashboard"] }),
        queryClient.invalidateQueries({ queryKey: ["accounts"] }),
        queryClient.invalidateQueries({ queryKey: ["instance-accounts"] })
      ]);
    },
    onError: (error) => emitToast(error.message, "error")
  });

  const data = dashboard.data;
  const aggregate = (data?.instances || []).reduce(
    (acc, instance) => {
      const snapshot = instance.snapshot;
      acc.accounts += snapshot?.accounts?.available ?? snapshot?.tokens.active ?? 0;
      acc.credits += snapshot?.accounts?.credits_available ?? snapshot?.tokens.credits_available ?? 0;
      return acc;
    },
    { accounts: 0, credits: 0 }
  );

  return <div className="page-stack">
    <section className="page-toolbar">
      <div><strong>{data ? `${data.summary.online}/${data.summary.total} 个实例在线` : "正在读取实例状态"}</strong><span>最后采集 {formatTime(data?.updated_at)}</span></div>
      <div className="inline-actions">
        <button className="primary-btn" onClick={() => setFleetImportOpen(true)}><Upload size={16} />统一导入</button>
        <button className="secondary-btn" onClick={() => refreshFleetCredits.mutate()} disabled={refreshFleetCredits.isPending}><WalletCards size={16} />刷新全部额度</button>
        <button className="secondary-btn batch-danger" onClick={() => setLowCreditDeleteOpen(true)}><Trash2 size={16} />低积分清理</button>
        <button className="secondary-btn" onClick={() => poll.mutate()} disabled={poll.isPending}><RefreshCw size={16} className={poll.isPending ? "spin" : ""} />立即采集</button>
      </div>
    </section>

    <AutoReplacementConsole />

    <section className="metric-band">
      <div><Server size={18} /><span>在线实例</span><strong>{data?.summary.online ?? "-"}</strong></div>
      <div><UsersRound size={18} /><span>可用账号</span><strong>{aggregate.accounts}</strong></div>
      <div><Gauge size={18} /><span>账号剩余积分</span><strong>{formatNumber(aggregate.credits, 1)}</strong></div>
      <div><CheckCircle2 size={18} /><span>总成功数</span><strong className="text-success">{data?.summary.total_success ?? "-"}</strong></div>
      <div><LoaderCircle size={18} /><span>总进行中数</span><strong>{data?.summary.total_in_progress ?? "-"}</strong></div>
      <div className={(data?.summary.active_alerts || 0) > 0 ? "metric-alert" : ""}><AlertTriangle size={18} /><span>当前告警</span><strong>{data?.summary.active_alerts ?? "-"}</strong></div>
    </section>

    <section className="fleet-section">
      <div className="section-head fleet-section-head">
        <div><h2>实例运行带</h2><span>最近 7 天逐小时可用性</span></div>
        <label className="threshold-control"><span>低积分阈值</span><input type="number" min="0" value={threshold} onChange={(event) => setThreshold(event.target.value)} /><button className="icon-btn" title="保存低积分阈值" disabled={saveThreshold.isPending} onClick={() => saveThreshold.mutate()}><Save size={15} /></button></label>
      </div>
      {dashboard.isLoading && <div className="empty-row">正在加载...</div>}
      {dashboard.isError && <div className="error-banner">{dashboard.error.message}</div>}
      {data?.instances.length === 0 && <div className="empty-row">尚未登记实例 <Link to="/instances">添加实例</Link></div>}
      <div className="fleet-list">
        {data?.instances.map((instance) => <OverviewInstanceRow
          key={instance.id}
          instance={instance}
          instances={data.instances}
          threshold={data.preferences.low_credit_threshold}
          expanded={expandedId === instance.id}
          onToggle={() => setExpandedId((current) => current === instance.id ? "" : instance.id)}
        />)}
      </div>
    </section>
    <FleetImportModal open={fleetImportOpen} onClose={() => setFleetImportOpen(false)} instances={data?.instances || []} savedTargets={data?.preferences.account_targets || {}} />
    <FleetLowCreditDeleteModal open={lowCreditDeleteOpen} onClose={() => setLowCreditDeleteOpen(false)} defaultThreshold={data?.preferences.low_credit_threshold ?? 100} />
  </div>;
}

function OverviewInstanceRow({
  instance,
  instances,
  threshold,
  expanded,
  onToggle
}: {
  instance: FleetInstance;
  instances: FleetInstance[];
  threshold: number;
  expanded: boolean;
  onToggle: () => void;
}) {
  const queryClient = useQueryClient();
  const [importOpen, setImportOpen] = useState(false);
  const [moveOpen, setMoveOpen] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const supportsAccounts = !instance.capabilities.length || instance.capabilities.includes("accounts");
  const accounts = useQuery({
    queryKey: ["instance-accounts", instance.id, threshold],
    queryFn: () => apiFetch<AccountsResponse>(`/instances/${instance.id}/accounts`),
    enabled: expanded && supportsAccounts,
    staleTime: 15000
  });
  const refreshBalances = useMutation({
    mutationFn: () => apiFetch(`/instances/${instance.id}/tokens/credits-batch`, { method: "POST", body: JSON.stringify({}) }),
    onSuccess: async () => {
      emitToast(`${instance.name} 账号余额已刷新`, "success");
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["instance-accounts", instance.id] }),
        queryClient.invalidateQueries({ queryKey: ["dashboard"] })
      ]);
    },
    onError: (error) => emitToast(error.message, "error")
  });
  useEffect(() => {
    if (!expanded) {
      setSelected(new Set());
      setMoveOpen(false);
    }
  }, [expanded]);

  const selectedAccounts = (accounts.data?.accounts || []).filter(
    (item) => selected.has(`${item.instance_id}:${item.id}`)
  );

  const snapshot = instance.snapshot;
  const accountStats = snapshot?.accounts;
  const todaySuccessful = snapshot?.requests.today?.successful
    ?? snapshot?.requests.successful
    ?? Math.max(0, (snapshot?.requests.total || 0) - (snapshot?.requests.failed || 0));
  const todayFailed = snapshot?.requests.today?.failed ?? snapshot?.requests.failed;
  const safetyReviewFailed = snapshot?.requests.today?.safety_review_failed;
  const availableAccounts = accountStats?.available ?? snapshot?.tokens.active;
  const totalAccounts = accountStats?.total ?? snapshot?.refresh_profiles.total ?? snapshot?.tokens.total;

  return <article className={`fleet-item${expanded ? " fleet-item-expanded" : ""}`}>
    <div className="fleet-row">
      <div className="fleet-identity"><StatusBadge status={instance.state} /><div><Link to={`/instances/${instance.id}`}>{instance.name}</Link><span>{instance.location || instance.base_url}</span></div></div>
      <div className="fleet-stat fleet-secondary"><span>延迟</span><strong>{formatDuration(instance.latency_seconds)}</strong></div>
      <div className="fleet-stat fleet-secondary account-count-stat"><span>账号</span><strong>{snapshot ? `${availableAccounts}/${totalAccounts}` : "-"}</strong><small className={(accountStats?.low_credit || 0) > 0 ? "text-danger" : ""}>{supportsAccounts ? `低积分 ${accountStats?.low_credit ?? "-"}` : "待升级"}</small></div>
      <div className="fleet-stat fleet-secondary"><span>积分</span><strong>{formatNumber(accountStats?.credits_available ?? snapshot?.tokens.credits_available, 1)}</strong></div>
      <div className="fleet-stat"><span>进行中</span><strong>{snapshot?.requests.in_progress ?? "-"}</strong></div>
      <div className="fleet-stat"><span>今日成功</span><strong className="text-success">{snapshot ? todaySuccessful : "-"}</strong></div>
      <div className="fleet-stat"><span>今日失败</span><strong className={Number(todayFailed || 0) > 0 ? "text-danger" : ""}>{snapshot ? todayFailed : "-"}</strong></div>
      <div className="fleet-stat"><span>审核失败</span><strong className={Number(safetyReviewFailed || 0) > 0 ? "text-danger" : ""}>{snapshot ? safetyReviewFailed ?? "-" : "-"}</strong></div>
      <div className="fleet-stat"><span>错误率</span><strong className={(snapshot?.requests.error_rate || 0) > 0.2 ? "text-danger" : ""}>{snapshot ? `${(snapshot.requests.error_rate * 100).toFixed(1)}%` : "-"}</strong></div>
      <div className="fleet-mobile-counts"><span>进行中 <strong>{snapshot?.requests.in_progress ?? "-"}</strong></span><span>成功 <strong>{snapshot ? todaySuccessful : "-"}</strong></span><span>失败 <strong className={Number(todayFailed || 0) > 0 ? "text-danger" : ""}>{snapshot ? todayFailed : "-"}</strong></span><span>审核失败 <strong className={Number(safetyReviewFailed || 0) > 0 ? "text-danger" : ""}>{snapshot ? safetyReviewFailed ?? "-" : "-"}</strong></span><span>低积分 <strong className={(accountStats?.low_credit || 0) > 0 ? "text-danger" : ""}>{accountStats?.low_credit ?? "-"}</strong></span></div>
      <div className="fleet-heartbeat"><Heartbeat points={instance.heartbeat} /></div>
      <div className="fleet-row-actions"><button className={`icon-btn expand-btn${expanded ? " expanded" : ""}`} title={supportsAccounts ? (expanded ? "收起账号" : "展开账号") : "实例端需要升级"} disabled={!supportsAccounts} onClick={onToggle}><ChevronDown size={18} /></button><Link className="icon-btn" to={`/instances/${instance.id}`} title="查看实例"><ArrowRight size={18} /></Link></div>
    </div>
    {expanded && <section className="account-drawer">
      <div className="account-drawer-head"><div><strong>Cookie 账号</strong><span>按剩余积分从低到高</span></div><div className="inline-actions"><button className="secondary-btn compact-action" onClick={() => setImportOpen(true)}><Upload size={15} />导入</button><button className="secondary-btn compact-action" disabled={refreshBalances.isPending} onClick={() => refreshBalances.mutate()}><WalletCards size={15} />刷新余额</button><button className="icon-btn" title="刷新账号列表" onClick={() => accounts.refetch()}><RefreshCw size={16} className={accounts.isFetching ? "spin" : ""} /></button><Link className="text-link" to={`/instances/${instance.id}?tab=accounts`}>完整管理</Link></div></div>
      <AccountBatchBar accounts={accounts.data?.accounts || []} selected={selected} onSelectionChange={setSelected} onMove={() => setMoveOpen(true)} compact />
      {accounts.isError && <div className="drawer-error">{accounts.error.message}</div>}
      <div className="account-drawer-scroll"><AccountTable accounts={accounts.data?.accounts || []} compact loading={accounts.isLoading} selected={selected} onSelectionChange={setSelected} /></div>
    </section>}
    <CookieImportModal open={importOpen} onClose={() => setImportOpen(false)} fixedInstanceId={instance.id} fixedInstanceName={instance.name} />
    <AccountMoveModal open={moveOpen} onClose={() => setMoveOpen(false)} source={instance} instances={instances} accounts={selectedAccounts} onMoved={() => { setSelected(new Set()); accounts.refetch(); }} />
  </article>;
}
