import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Download, Plus, RefreshCw, Save, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { MetricChart } from "../components/MetricChart";
import { Modal } from "../components/Modal";
import { StatusBadge } from "../components/StatusBadge";
import { apiDownload, apiFetch, emitToast, formatDuration, formatNumber, formatTime } from "../lib/api";
import type { FleetInstance } from "../types";
import { LogsPage } from "./LogsPage";
import { TokensPage } from "./TokensPage";

const tabs = [
  { id: "overview", label: "概览" },
  { id: "tokens", label: "Token" },
  { id: "profiles", label: "刷新配置" },
  { id: "config", label: "运行配置" },
  { id: "logs", label: "日志" }
];

export function InstanceDetailPage() {
  const { instanceId = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const activeTab = params.get("tab") || "overview";
  const instance = useQuery({
    queryKey: ["instance", instanceId],
    queryFn: () => apiFetch<FleetInstance>(`/instances/${instanceId}`),
    refetchInterval: 30000
  });
  const metrics = useQuery({
    queryKey: ["metrics", instanceId],
    queryFn: () => apiFetch<{ items: { ts: number; latency_seconds?: number | null; error_rate: number; active_tokens: number }[] }>(`/instances/${instanceId}/metrics?hours=24`),
    enabled: activeTab === "overview",
    refetchInterval: 30000
  });
  const item = instance.data;
  const snapshot = item?.snapshot;

  if (instance.isLoading) return <div className="empty-row">正在加载实例...</div>;
  if (instance.isError || !item) return <div className="error-banner">{instance.error?.message || "实例不存在"}</div>;
  const compatible = item.ops_api_version == null || item.ops_api_version === 1;

  return (
    <div className="page-stack">
      <section className="instance-titlebar">
        <Link className="icon-btn" to="/instances" title="返回实例列表"><ArrowLeft size={18} /></Link>
        <div><div className="title-line"><h2>{item.name}</h2><StatusBadge status={item.enabled ? item.state : "disabled"} /></div><span>{item.location || "未设置位置"} · {item.base_url}</span></div>
        <div className="instance-version">Ops API <strong>{item.ops_api_version ? `v${item.ops_api_version}` : "-"}</strong></div>
      </section>
      {!compatible && <div className="warning-banner">当前实例使用 Ops API v{item.ops_api_version}，中心仅支持 v1。管理操作已停用。</div>}
      <nav className="detail-tabs">{tabs.map((tab) => <button key={tab.id} disabled={!compatible && tab.id !== "overview"} className={activeTab === tab.id ? "active" : ""} onClick={() => setParams({ tab: tab.id })}>{tab.label}</button>)}</nav>
      {activeTab === "overview" && <div className="page-stack">
        <section className="metric-band compact-metrics">
          <div><span>运行时间</span><strong>{formatDuration(snapshot?.instance.uptime_seconds)}</strong></div>
          <div><span>活跃 Token</span><strong>{snapshot ? `${snapshot.tokens.active}/${snapshot.tokens.total}` : "-"}</strong></div>
          <div><span>剩余积分</span><strong>{formatNumber(snapshot?.tokens.credits_available, 1)}</strong></div>
          <div><span>当前进行中</span><strong>{snapshot?.requests.in_progress ?? "-"}</strong></div>
          <div><span>今日成功</span><strong className="text-success">{snapshot?.requests.today?.successful ?? snapshot?.requests.successful ?? "-"}</strong></div>
          <div><span>今日失败</span><strong className={(snapshot?.requests.today?.failed ?? snapshot?.requests.failed ?? 0) > 0 ? "text-danger" : ""}>{snapshot?.requests.today?.failed ?? snapshot?.requests.failed ?? "-"}</strong></div>
          <div><span>5 分钟错误率</span><strong>{snapshot ? `${(snapshot.requests.error_rate * 100).toFixed(1)}%` : "-"}</strong></div>
          <div><span>P95 耗时</span><strong>{formatDuration(snapshot?.requests.duration_p95_seconds)}</strong></div>
        </section>
        {item.last_error && <div className="error-banner">{item.last_error}</div>}
        <section className="chart-section"><div className="section-head"><div><h2>24 小时趋势</h2><span>采集延迟与请求错误率</span></div><span>最后在线 {formatTime(item.last_seen_at)}</span></div><MetricChart points={metrics.data?.items || []} /></section>
        <section className="instance-facts"><div><span>应用版本</span><strong>{snapshot?.instance.version || "-"}</strong></div><div><span>构建 SHA</span><strong>{snapshot?.instance.build_sha || "-"}</strong></div><div><span>刷新配置</span><strong>{snapshot?.refresh_profiles.total ?? "-"}</strong></div><div><span>刷新异常</span><strong>{snapshot?.refresh_profiles.failing ?? "-"}</strong></div><div><span>生成文件</span><strong>{formatNumber(snapshot?.storage.generated_file_count)}</strong></div><div><span>存储占用</span><strong>{snapshot ? `${snapshot.storage.generated_usage_mb} MB` : "-"}</strong></div></section>
      </div>}
      {compatible && activeTab === "tokens" && <TokensPage fixedInstanceId={instanceId} />}
      {compatible && activeTab === "profiles" && <ProfilesPanel instanceId={instanceId} instanceName={item.name} />}
      {compatible && activeTab === "config" && <ConfigPanel instanceId={instanceId} />}
      {compatible && activeTab === "logs" && <LogsPage fixedInstanceId={instanceId} />}
    </div>
  );
}

interface RefreshProfile {
  id: string;
  name: string;
  enabled: boolean;
  account?: { display_name?: string; email?: string };
  state?: { last_success_at?: number; last_error?: string; consecutive_failures?: number; next_retry_at?: number };
}

function ProfilesPanel({ instanceId, instanceName }: { instanceId: string; instanceName: string }) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [cookie, setCookie] = useState("");
  const profiles = useQuery({
    queryKey: ["profiles", instanceId],
    queryFn: () => apiFetch<{ profiles: RefreshProfile[] }>(`/instances/${instanceId}/refresh-profiles`)
  });
  const importProfile = useMutation({
    mutationFn: () => {
      let parsed: unknown = cookie;
      try { parsed = JSON.parse(cookie); } catch { /* Plain Cookie header. */ }
      return apiFetch(`/instances/${instanceId}/refresh-profiles/import`, { method: "POST", body: JSON.stringify({ name: name || undefined, cookie: parsed }) });
    },
    onSuccess: () => { emitToast("Cookie 已导入", "success"); setOpen(false); setCookie(""); queryClient.invalidateQueries({ queryKey: ["profiles", instanceId] }); },
    onError: (error) => emitToast(error.message, "error")
  });

  async function action(path: string, method: string, success: string) {
    try { await apiFetch(path, { method }); emitToast(success, "success"); queryClient.invalidateQueries({ queryKey: ["profiles", instanceId] }); }
    catch (error) { emitToast(error instanceof Error ? error.message : "操作失败", "error"); }
  }

  return <div className="page-stack"><div className="inline-actions"><button className="primary-btn" onClick={() => setOpen(true)}><Plus size={16} />导入 Cookie</button><button className="secondary-btn" onClick={() => apiDownload(`/instances/${instanceId}/refresh-profiles/export`, {}, `${instanceName}-cookies.json`)}><Download size={16} />导出</button><button className="icon-btn" onClick={() => profiles.refetch()} title="刷新"><RefreshCw size={17} /></button></div><section className="table-section"><div className="table-scroll"><table><thead><tr><th>名称</th><th>账号</th><th>状态</th><th>连续失败</th><th>最后成功</th><th>下次刷新</th><th>错误</th><th>操作</th></tr></thead><tbody>{profiles.data?.profiles.map((item) => <tr key={item.id}><td><strong>{item.name}</strong><small>{item.id}</small></td><td>{item.account?.display_name || item.account?.email || "-"}</td><td><StatusBadge status={item.enabled ? "active" : "disabled"} /></td><td>{item.state?.consecutive_failures || 0}</td><td>{formatTime(item.state?.last_success_at)}</td><td>{formatTime(item.state?.next_retry_at)}</td><td className="truncate-cell" title={item.state?.last_error}>{item.state?.last_error || "-"}</td><td><div className="row-actions"><button className="icon-btn" title="立即刷新" onClick={() => action(`/instances/${instanceId}/refresh-profiles/${item.id}/refresh`, "POST", "刷新完成")}><RefreshCw size={16} /></button><button className="compact-btn" onClick={() => action(`/instances/${instanceId}/refresh-profiles/${item.id}/enabled?enabled=${!item.enabled}`, "PUT", item.enabled ? "已暂停" : "已启用")}>{item.enabled ? "暂停" : "启用"}</button><button className="icon-btn danger-icon" title="删除" onClick={() => { if (window.confirm(`确认删除刷新配置「${item.name}」？`)) action(`/instances/${instanceId}/refresh-profiles/${item.id}`, "DELETE", "刷新配置已删除"); }}><Trash2 size={16} /></button></div></td></tr>)}{!profiles.isLoading && !profiles.data?.profiles.length && <tr><td colSpan={8} className="empty-row">尚无刷新配置</td></tr>}</tbody></table></div></section><Modal open={open} title="导入 Cookie" onClose={() => setOpen(false)} footer={<><button className="secondary-btn" onClick={() => setOpen(false)}>取消</button><button className="primary-btn" onClick={() => importProfile.mutate()} disabled={!cookie || importProfile.isPending}>导入并刷新</button></>}><div className="form-grid"><label className="form-span">账号名称<input value={name} onChange={(event) => setName(event.target.value)} /></label><label className="form-span">Cookie 或导出 JSON<textarea rows={10} value={cookie} onChange={(event) => setCookie(event.target.value)} /></label></div></Modal></div>;
}

type ConfigForm = Record<string, string | boolean>;

function ConfigPanel({ instanceId }: { instanceId: string }) {
  const [form, setForm] = useState<ConfigForm>({});
  const config = useQuery({ queryKey: ["config", instanceId], queryFn: () => apiFetch<Record<string, unknown>>(`/instances/${instanceId}/config`) });
  useEffect(() => {
    if (!config.data) return;
    setForm(Object.fromEntries(Object.entries(config.data).map(([key, value]) => [key, Array.isArray(value) ? value.join(",") : typeof value === "object" && value ? JSON.stringify(value) : value == null ? "" : value])) as ConfigForm);
  }, [config.data]);
  const save = useMutation({
    mutationFn: () => {
      const payload: Record<string, unknown> = {
        public_base_url: form.public_base_url || "",
        proxy: form.proxy || "",
        use_proxy: Boolean(form.use_proxy),
        generate_timeout: Number(form.generate_timeout || 300),
        refresh_interval_hours: Number(form.refresh_interval_hours || 15),
        retry_enabled: Boolean(form.retry_enabled),
        retry_max_attempts: Number(form.retry_max_attempts || 3),
        retry_backoff_seconds: Number(form.retry_backoff_seconds || 1),
        retry_on_status_codes: String(form.retry_on_status_codes || "").split(",").map(Number).filter(Boolean),
        retry_on_error_types: String(form.retry_on_error_types || "").split(",").map((value) => value.trim()).filter(Boolean),
        token_rotation_strategy: form.token_rotation_strategy || "round_robin",
        batch_concurrency: Number(form.batch_concurrency || 5),
        generated_max_size_mb: Number(form.generated_max_size_mb || 1024),
        generated_prune_size_mb: Number(form.generated_prune_size_mb || 200),
        gpt_image_quality: form.gpt_image_quality || "low"
      };
      if (form.api_key_new) payload.api_key = form.api_key_new;
      if (form.admin_username) payload.admin_username = form.admin_username;
      if (form.admin_password_new) payload.admin_password = form.admin_password_new;
      return apiFetch(`/instances/${instanceId}/config`, { method: "PUT", body: JSON.stringify(payload) });
    },
    onSuccess: () => { emitToast("运行配置已保存", "success"); config.refetch(); },
    onError: (error) => emitToast(error.message, "error")
  });
  const field = (key: string, value: string | boolean) => setForm((old) => ({ ...old, [key]: value }));
  return <div className="page-stack"><section className="config-section"><div className="section-head"><div><h2>网络与请求</h2></div></div><div className="form-grid config-grid"><label className="form-span">公开访问地址<input value={String(form.public_base_url || "")} onChange={(event) => field("public_base_url", event.target.value)} /></label><label className="check-row form-span"><input type="checkbox" checked={Boolean(form.use_proxy)} onChange={(event) => field("use_proxy", event.target.checked)} />启用上游代理</label><label className="form-span">代理地址<input value={String(form.proxy || "")} onChange={(event) => field("proxy", event.target.value)} /></label><label>生成超时（秒）<input type="number" value={String(form.generate_timeout || 300)} onChange={(event) => field("generate_timeout", event.target.value)} /></label><label>批处理并发<input type="number" value={String(form.batch_concurrency || 5)} onChange={(event) => field("batch_concurrency", event.target.value)} /></label><label>Token 策略<select value={String(form.token_rotation_strategy || "round_robin")} onChange={(event) => field("token_rotation_strategy", event.target.value)}><option value="round_robin">轮询</option><option value="random">随机</option></select></label><label>GPT Image 质量<select value={String(form.gpt_image_quality || "low")} onChange={(event) => field("gpt_image_quality", event.target.value)}><option value="low">low</option><option value="medium">medium</option><option value="high">high</option></select></label></div></section><section className="config-section"><div className="section-head"><div><h2>重试与刷新</h2></div></div><div className="form-grid config-grid"><label className="check-row form-span"><input type="checkbox" checked={Boolean(form.retry_enabled)} onChange={(event) => field("retry_enabled", event.target.checked)} />启用自动重试</label><label>最大尝试次数<input type="number" value={String(form.retry_max_attempts || 3)} onChange={(event) => field("retry_max_attempts", event.target.value)} /></label><label>退避秒数<input type="number" step="0.1" value={String(form.retry_backoff_seconds || 1)} onChange={(event) => field("retry_backoff_seconds", event.target.value)} /></label><label className="form-span">重试状态码<input value={String(form.retry_on_status_codes || "")} onChange={(event) => field("retry_on_status_codes", event.target.value)} /></label><label className="form-span">重试错误类型<input value={String(form.retry_on_error_types || "")} onChange={(event) => field("retry_on_error_types", event.target.value)} /></label><label>刷新间隔（小时）<input type="number" value={String(form.refresh_interval_hours || 15)} onChange={(event) => field("refresh_interval_hours", event.target.value)} /></label></div></section><section className="config-section"><div className="section-head"><div><h2>存储与敏感设置</h2></div></div><div className="form-grid config-grid"><label>存储上限（MB）<input type="number" value={String(form.generated_max_size_mb || 1024)} onChange={(event) => field("generated_max_size_mb", event.target.value)} /></label><label>清理批次（MB）<input type="number" value={String(form.generated_prune_size_mb || 200)} onChange={(event) => field("generated_prune_size_mb", event.target.value)} /></label><label className="form-span">新服务 API Key<input type="password" value={String(form.api_key_new || "")} onChange={(event) => field("api_key_new", event.target.value)} placeholder={form.api_key_configured ? "已配置，留空保持不变" : "输入新密钥"} /></label><label>子平台管理员账号<input value={String(form.admin_username || "")} onChange={(event) => field("admin_username", event.target.value)} /></label><label>子平台管理员新密码<input type="password" value={String(form.admin_password_new || "")} onChange={(event) => field("admin_password_new", event.target.value)} /></label></div></section><div className="save-bar"><button className="primary-btn" onClick={() => save.mutate()} disabled={save.isPending}><Save size={16} />保存配置</button></div></div>;
}
