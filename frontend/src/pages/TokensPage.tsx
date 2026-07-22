import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, Plus, RefreshCw, RotateCcw, Search, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";
import { Modal } from "../components/Modal";
import { StatusBadge } from "../components/StatusBadge";
import { apiDownload, apiFetch, emitToast, formatNumber, formatTime } from "../lib/api";
import type { FleetInstance, TokenItem } from "../types";

export function TokensPage({ fixedInstanceId }: { fixedInstanceId?: string }) {
  const queryClient = useQueryClient();
  const [instanceFilter, setInstanceFilter] = useState(fixedInstanceId || "");
  const [statusFilter, setStatusFilter] = useState("");
  const [search, setSearch] = useState("");
  const [addOpen, setAddOpen] = useState(false);
  const [targetId, setTargetId] = useState(fixedInstanceId || "");
  const [tokenText, setTokenText] = useState("");
  const instances = useQuery({
    queryKey: ["instances"],
    queryFn: () => apiFetch<{ instances: FleetInstance[] }>("/instances")
  });
  const tokens = useQuery({
    queryKey: ["tokens", fixedInstanceId],
    queryFn: () => apiFetch<{ status: string; tokens: TokenItem[]; errors: unknown[] }>(
      `/tokens${fixedInstanceId ? `?instance_ids=${fixedInstanceId}` : ""}`
    ),
    refetchInterval: 30000
  });
  const add = useMutation({
    mutationFn: async () => {
      const values = tokenText.split(/\r?\n/).map((value) => value.trim()).filter(Boolean);
      if (!targetId || values.length === 0) throw new Error("请选择实例并填写 Token");
      return apiFetch(`/instances/${targetId}/tokens`, {
        method: "POST",
        body: JSON.stringify(values.length > 1 ? { tokens: values } : { token: values[0] })
      });
    },
    onSuccess: () => {
      emitToast("Token 已添加", "success");
      setAddOpen(false);
      setTokenText("");
      queryClient.invalidateQueries({ queryKey: ["tokens"] });
    },
    onError: (error) => emitToast(error.message, "error")
  });

  const rows = useMemo(() => (tokens.data?.tokens || []).filter((item) => {
    if (instanceFilter && item.instance_id !== instanceFilter) return false;
    if (statusFilter && item.status !== statusFilter) return false;
    if (search) {
      const haystack = `${item.instance_name} ${item.refresh_profile_name || ""} ${item.refresh_profile_email || ""} ${item.value}`.toLowerCase();
      if (!haystack.includes(search.toLowerCase())) return false;
    }
    return true;
  }), [tokens.data, instanceFilter, statusFilter, search]);

  async function action(path: string, method: string, body?: unknown, success = "操作完成") {
    try {
      await apiFetch(path, { method, body: body === undefined ? undefined : JSON.stringify(body) });
      emitToast(success, "success");
      queryClient.invalidateQueries({ queryKey: ["tokens"] });
    } catch (error) {
      emitToast(error instanceof Error ? error.message : "操作失败", "error");
    }
  }

  async function remove(item: TokenItem) {
    if (!window.confirm(`确认从「${item.instance_name}」删除 Token ${item.value}？`)) return;
    await action(`/instances/${item.instance_id}/tokens/${item.id}`, "DELETE", undefined, "Token 已删除");
  }

  return (
    <div className="page-stack">
      {!fixedInstanceId && (
        <section className="page-toolbar">
          <div><strong>统一 Token 池</strong><span>{rows.length} 条当前结果</span></div>
          <button className="primary-btn" onClick={() => { setTargetId(instanceFilter); setAddOpen(true); }}><Plus size={16} />添加 Token</button>
        </section>
      )}
      {fixedInstanceId && <div className="inline-actions"><button className="primary-btn" onClick={() => { setTargetId(fixedInstanceId); setAddOpen(true); }}><Plus size={16} />添加 Token</button></div>}
      <section className="filter-bar">
        {!fixedInstanceId && <select value={instanceFilter} onChange={(event) => setInstanceFilter(event.target.value)}><option value="">全部实例</option>{instances.data?.instances.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select>}
        <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}><option value="">全部状态</option><option value="active">生效</option><option value="disabled">停用</option><option value="exhausted">耗尽</option><option value="invalid">失效</option><option value="error">异常</option></select>
        <label className="search-box"><Search size={16} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="账号或 Token" /></label>
        <button className="icon-btn" title="刷新" onClick={() => tokens.refetch()}><RefreshCw size={17} /></button>
      </section>
      {tokens.data?.status === "partial" && <div className="warning-banner">部分实例读取失败，当前展示其余实例数据。</div>}
      <section className="table-section">
        <div className="table-scroll">
          <table>
            <thead><tr><th>实例</th><th>账号</th><th>Token</th><th>状态</th><th>自动刷新</th><th>积分</th><th>到期时间</th><th>失败</th><th>操作</th></tr></thead>
            <tbody>
              {rows.map((item) => (
                <tr key={`${item.instance_id}-${item.id}`}>
                  <td><strong>{item.instance_name}</strong></td>
                  <td><span>{item.refresh_profile_name || "手动 Token"}</span><small>{item.refresh_profile_email || item.source}</small></td>
                  <td><code>{item.value}</code></td>
                  <td><StatusBadge status={item.status} /></td>
                  <td>{item.auto_refresh ? (item.auto_refresh_enabled === false ? "已暂停" : "已开启") : "-"}</td>
                  <td><strong>{formatNumber(item.credits_available, 1)}</strong><small>/ {formatNumber(item.credits_total, 1)}</small></td>
                  <td>{formatTime(item.expires_at)}</td>
                  <td>{item.fails}</td>
                  <td><div className="row-actions">
                    <button className="icon-btn" title={item.status === "disabled" ? "启用" : "停用"} onClick={() => action(`/instances/${item.instance_id}/tokens/${item.id}/status?status=${item.status === "disabled" ? "active" : "disabled"}`, "PUT", undefined, "Token 状态已更新")}><RotateCcw size={16} /></button>
                    <button className="icon-btn" title="刷新积分" onClick={() => action(`/instances/${item.instance_id}/tokens/${item.id}/credits`, "POST", undefined, "积分已刷新")}><RefreshCw size={16} /></button>
                    <button className="icon-btn danger-icon" title="删除" onClick={() => remove(item)}><Trash2 size={16} /></button>
                  </div></td>
                </tr>
              ))}
              {!tokens.isLoading && rows.length === 0 && <tr><td colSpan={9} className="empty-row">没有匹配的 Token</td></tr>}
            </tbody>
          </table>
        </div>
      </section>

      <Modal
        open={addOpen}
        title="添加 Token"
        onClose={() => setAddOpen(false)}
        footer={<><button className="secondary-btn" onClick={() => setAddOpen(false)}>取消</button><button className="primary-btn" onClick={() => add.mutate()} disabled={add.isPending}>确认添加</button></>}
      >
        <div className="form-grid">
          <label className="form-span">目标实例<select value={targetId} onChange={(event) => setTargetId(event.target.value)} disabled={Boolean(fixedInstanceId)}><option value="">选择实例</option>{instances.data?.instances.filter((item) => item.enabled).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
          <label className="form-span">Token<textarea rows={8} value={tokenText} onChange={(event) => setTokenText(event.target.value)} placeholder="每行一个 Token" /></label>
        </div>
      </Modal>
    </div>
  );
}
