import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BellOff, RefreshCw, Save } from "lucide-react";
import { useState } from "react";
import { StatusBadge } from "../components/StatusBadge";
import { apiFetch, emitToast, formatTime } from "../lib/api";
import type { AlertItem } from "../types";

interface AlertRule {
  id: string;
  name: string;
  severity: string;
  enabled: boolean;
  threshold: number;
  minimum_requests: number;
  pending_samples: number;
  recovery_samples: number;
}

const ruleLabels: Record<string, string> = {
  instance_offline: "实例离线",
  high_latency: "采集延迟过高",
  high_error_rate: "请求错误率过高",
  no_active_tokens: "无可用账号",
  low_credits: "剩余积分过低",
  token_expiring: "账号凭证即将到期",
  refresh_failures: "Cookie 账号连续刷新失败"
};

function displayRule(ruleId: string, fallback: string) {
  return ruleLabels[ruleId] || fallback;
}

export function AlertsPage() {
  const queryClient = useQueryClient();
  const [state, setState] = useState("");
  const alerts = useQuery({
    queryKey: ["alerts", state],
    queryFn: () => apiFetch<{ alerts: AlertItem[] }>(`/alerts${state ? `?state=${state}` : ""}`),
    refetchInterval: 30000
  });
  const rules = useQuery({
    queryKey: ["alert-rules"],
    queryFn: () => apiFetch<{ rules: AlertRule[] }>("/alert-rules")
  });

  async function silence(alert: AlertItem, seconds: number) {
    await apiFetch(`/instances/${alert.instance_id}/silences`, {
      method: "POST",
      body: JSON.stringify({ duration_seconds: seconds, rule_id: alert.rule_id, reason: "console silence" })
    });
    emitToast(`${alert.instance_name} 告警已静默`, "success");
  }

  const updateRule = useMutation({
    mutationFn: ({ id, changes }: { id: string; changes: Partial<AlertRule> }) => apiFetch(`/alert-rules/${id}`, { method: "PUT", body: JSON.stringify(changes) }),
    onSuccess: () => { emitToast("告警规则已更新", "success"); queryClient.invalidateQueries({ queryKey: ["alert-rules"] }); },
    onError: (error) => emitToast(error.message, "error")
  });

  return (
    <div className="page-stack">
      <section className="page-toolbar">
        <div><strong>告警事件</strong><span>{alerts.data?.alerts.filter((item) => item.state === "firing").length || 0} 条触发中</span></div>
        <div className="inline-actions"><select value={state} onChange={(event) => setState(event.target.value)}><option value="">全部状态</option><option value="firing">告警中</option><option value="pending">待确认</option><option value="resolved">已恢复</option></select><button className="icon-btn" onClick={() => alerts.refetch()} title="刷新"><RefreshCw size={17} /></button></div>
      </section>
      <section className="table-section">
        <div className="table-scroll"><table><thead><tr><th>状态</th><th>实例</th><th>规则</th><th>级别</th><th>消息</th><th>开始时间</th><th>更新时间</th><th>静默</th></tr></thead><tbody>
          {alerts.data?.alerts.map((item) => <tr key={item.id}><td><StatusBadge status={item.state} /></td><td><strong>{item.instance_name}</strong></td><td>{displayRule(item.rule_id, item.rule_name)}</td><td><span className={`severity severity-${item.severity}`}>{item.severity}</span></td><td>{item.message}</td><td>{formatTime(item.opened_at)}</td><td>{formatTime(item.updated_at)}</td><td>{item.state !== "resolved" ? <div className="row-actions"><button className="compact-btn" onClick={() => silence(item, 3600)}><BellOff size={14} />1h</button><button className="compact-btn" onClick={() => silence(item, 86400)}><BellOff size={14} />24h</button></div> : "-"}</td></tr>)}
          {!alerts.isLoading && !alerts.data?.alerts.length && <tr><td colSpan={8} className="empty-row">当前没有告警事件</td></tr>}
        </tbody></table></div>
      </section>

      <section className="settings-section">
        <div className="section-head"><div><h2>告警规则</h2><span>连续采样确认与恢复阈值</span></div></div>
        <div className="rules-list">
          {rules.data?.rules.map((rule) => <RuleRow key={rule.id} rule={rule} onSave={(changes) => updateRule.mutate({ id: rule.id, changes })} />)}
        </div>
      </section>
    </div>
  );
}

function RuleRow({ rule, onSave }: { rule: AlertRule; onSave: (changes: Partial<AlertRule>) => void }) {
  const [threshold, setThreshold] = useState(String(rule.threshold));
  const [pending, setPending] = useState(String(rule.pending_samples));
  const [recovery, setRecovery] = useState(String(rule.recovery_samples));
  return <div className="rule-row"><label className="switch"><input type="checkbox" checked={rule.enabled} onChange={(event) => onSave({ enabled: event.target.checked })} /><span /></label><div className="rule-name"><strong>{displayRule(rule.id, rule.name)}</strong><span>{rule.id}</span></div><label>阈值<input type="number" step="0.01" value={threshold} onChange={(event) => setThreshold(event.target.value)} /></label><label>触发采样<input type="number" min="1" max="20" value={pending} onChange={(event) => setPending(event.target.value)} /></label><label>恢复采样<input type="number" min="1" max="20" value={recovery} onChange={(event) => setRecovery(event.target.value)} /></label><button className="icon-btn" title="保存规则" onClick={() => onSave({ threshold: Number(threshold), pending_samples: Number(pending), recovery_samples: Number(recovery) })}><Save size={16} /></button></div>;
}
