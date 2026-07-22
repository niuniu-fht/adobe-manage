export function StatusBadge({ status }: { status: string }) {
  const normalized = String(status || "unknown").toLowerCase();
  const labelMap: Record<string, string> = {
    online: "在线",
    offline: "离线",
    unknown: "待检测",
    active: "生效",
    disabled: "停用",
    exhausted: "已耗尽",
    invalid: "失效",
    error: "异常",
    firing: "告警中",
    pending: "待确认",
    resolved: "已恢复",
    success: "成功",
    failed: "失败"
  };
  return <span className={`status-badge status-${normalized}`}>{labelMap[normalized] || status}</span>;
}
