import { useMutation, useQuery } from "@tanstack/react-query";
import { CheckCircle2, Mail, Send, ShieldCheck, TimerReset, Webhook } from "lucide-react";
import { apiFetch, emitToast } from "../lib/api";

interface SettingsResponse {
  poll_interval_seconds: number;
  request_timeout_seconds: number;
  metrics_retention_days: number;
  event_retention_days: number;
  ops_key_configured: boolean;
  webhook_enabled: boolean;
  email_enabled: boolean;
}

export function SettingsPage() {
  const settings = useQuery({ queryKey: ["settings"], queryFn: () => apiFetch<SettingsResponse>("/settings") });
  const test = useMutation({
    mutationFn: () => apiFetch("/settings/notifications/test", { method: "POST" }),
    onSuccess: () => emitToast("测试通知已发送", "success"),
    onError: (error) => emitToast(error.message, "error")
  });
  const data = settings.data;
  return <div className="page-stack"><section className="settings-section"><div className="section-head"><div><h2>运行参数</h2><span>当前容器环境配置</span></div></div><div className="setting-lines"><div><TimerReset size={18} /><span>采集周期</span><strong>{data?.poll_interval_seconds || "-"} 秒</strong></div><div><TimerReset size={18} /><span>远程超时</span><strong>{data?.request_timeout_seconds || "-"} 秒</strong></div><div><CheckCircle2 size={18} /><span>指标保留</span><strong>{data?.metrics_retention_days || "-"} 天</strong></div><div><CheckCircle2 size={18} /><span>告警与审计</span><strong>{data?.event_retention_days || "-"} 天</strong></div><div><ShieldCheck size={18} /><span>实例运维密钥</span><strong>{data?.ops_key_configured ? "已配置" : "未配置"}</strong></div></div></section><section className="settings-section"><div className="section-head"><div><h2>通知通道</h2><span>凭据由环境变量加载</span></div><button className="secondary-btn" onClick={() => test.mutate()} disabled={test.isPending}><Send size={16} />发送测试</button></div><div className="channel-list"><div><Webhook size={20} /><div><strong>通用 Webhook</strong><span>JSON POST</span></div><StatusDot enabled={Boolean(data?.webhook_enabled)} /></div><div><Mail size={20} /><div><strong>SMTP 邮件</strong><span>故障、恢复与提醒</span></div><StatusDot enabled={Boolean(data?.email_enabled)} /></div></div></section></div>;
}

function StatusDot({ enabled }: { enabled: boolean }) {
  return <span className={`channel-status ${enabled ? "enabled" : "disabled"}`}>{enabled ? "已启用" : "未配置"}</span>;
}
