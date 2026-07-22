import { useQuery } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { StatusBadge } from "../components/StatusBadge";
import { apiFetch, formatDuration, formatTime } from "../lib/api";
import type { AuditItem } from "../types";

export function AuditPage() {
  const audit = useQuery({
    queryKey: ["audit"],
    queryFn: () => apiFetch<{ events: AuditItem[] }>("/audit?limit=200"),
    refetchInterval: 30000
  });
  return <div className="page-stack"><section className="page-toolbar"><div><strong>操作记录</strong><span>最近 {audit.data?.events.length || 0} 条</span></div><button className="icon-btn" onClick={() => audit.refetch()} title="刷新"><RefreshCw size={17} /></button></section><section className="table-section"><div className="table-scroll"><table><thead><tr><th>时间</th><th>实例</th><th>操作</th><th>资源</th><th>结果</th><th>耗时</th><th>请求 ID</th><th>摘要</th></tr></thead><tbody>{audit.data?.events.map((item) => <tr key={item.id}><td>{formatTime(item.ts)}</td><td>{item.instance_name}</td><td><code>{item.action}</code></td><td>{item.resource_type}{item.resource_id ? ` · ${item.resource_id}` : ""}</td><td><StatusBadge status={item.outcome} /></td><td>{formatDuration(item.duration_seconds)}</td><td><code>{item.request_id.slice(0, 12)}</code></td><td><code>{JSON.stringify(item.detail)}</code></td></tr>)}{!audit.isLoading && !audit.data?.events.length && <tr><td colSpan={8} className="empty-row">尚无操作记录</td></tr>}</tbody></table></div></section></div>;
}
