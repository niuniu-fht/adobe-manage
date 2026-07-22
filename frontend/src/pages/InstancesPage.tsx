import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FlaskConical, Pencil, Plus, Power, Trash2 } from "lucide-react";
import { FormEvent, useState } from "react";
import { Link } from "react-router-dom";
import { Modal } from "../components/Modal";
import { StatusBadge } from "../components/StatusBadge";
import { apiFetch, emitToast, formatDuration, formatTime } from "../lib/api";
import type { FleetInstance } from "../types";

const emptyForm = { name: "", location: "", base_url: "", tags: "", enabled: true };

export function InstancesPage() {
  const queryClient = useQueryClient();
  const [form, setForm] = useState(emptyForm);
  const [editing, setEditing] = useState<FleetInstance | null>(null);
  const [open, setOpen] = useState(false);
  const instances = useQuery({
    queryKey: ["instances"],
    queryFn: () => apiFetch<{ instances: FleetInstance[] }>("/instances")
  });
  const save = useMutation({
    mutationFn: () => apiFetch<FleetInstance>(editing ? `/instances/${editing.id}` : "/instances", {
      method: editing ? "PUT" : "POST",
      body: JSON.stringify({
        name: form.name,
        location: form.location,
        base_url: form.base_url,
        enabled: form.enabled,
        tags: form.tags.split(",").map((value) => value.trim()).filter(Boolean)
      })
    }),
    onSuccess: () => {
      emitToast(editing ? "实例已更新" : "实例已添加", "success");
      setOpen(false);
      queryClient.invalidateQueries({ queryKey: ["instances"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
    },
    onError: (error) => emitToast(error.message, "error")
  });

  function startCreate() {
    setEditing(null);
    setForm(emptyForm);
    setOpen(true);
  }

  function startEdit(item: FleetInstance) {
    setEditing(item);
    setForm({
      name: item.name,
      location: item.location,
      base_url: item.base_url,
      tags: item.tags.join(", "),
      enabled: item.enabled
    });
    setOpen(true);
  }

  async function test(item: FleetInstance) {
    try {
      const result = await apiFetch<{ ops_api_version: number }>(`/instances/${item.id}/test`, { method: "POST" });
      emitToast(`${item.name} 连接正常 · Ops API v${result.ops_api_version}`, "success");
    } catch (error) {
      emitToast(error instanceof Error ? error.message : "连接失败", "error");
    }
  }

  async function toggle(item: FleetInstance) {
    await apiFetch(`/instances/${item.id}`, { method: "PUT", body: JSON.stringify({ enabled: !item.enabled }) });
    emitToast(`${item.name} 已${item.enabled ? "停用" : "启用"}`, "success");
    queryClient.invalidateQueries({ queryKey: ["instances"] });
  }

  async function remove(item: FleetInstance) {
    if (!window.confirm(`确认删除实例「${item.name}」？其监控历史也会删除。`)) return;
    await apiFetch(`/instances/${item.id}`, { method: "DELETE" });
    emitToast(`${item.name} 已删除`, "success");
    queryClient.invalidateQueries({ queryKey: ["instances"] });
  }

  return (
    <div className="page-stack">
      <section className="page-toolbar">
        <div><strong>受管实例</strong><span>{instances.data?.instances.length || 0} 个连接</span></div>
        <button className="primary-btn" onClick={startCreate}><Plus size={16} />添加实例</button>
      </section>
      <section className="table-section">
        <div className="table-scroll">
          <table>
            <thead><tr><th>实例</th><th>位置</th><th>状态</th><th>延迟</th><th>Ops API</th><th>最后在线</th><th>标签</th><th>操作</th></tr></thead>
            <tbody>
              {instances.data?.instances.map((item) => (
                <tr key={item.id}>
                  <td><Link className="table-primary" to={`/instances/${item.id}`}>{item.name}</Link><small>{item.base_url}</small></td>
                  <td>{item.location || "-"}</td>
                  <td><StatusBadge status={item.enabled ? item.state : "disabled"} /></td>
                  <td>{formatDuration(item.latency_seconds)}</td>
                  <td>{item.ops_api_version ? `v${item.ops_api_version}${item.ops_api_version !== 1 ? " · 不兼容" : ""}` : "-"}</td>
                  <td>{formatTime(item.last_seen_at)}</td>
                  <td><div className="tag-list">{item.tags.map((tag) => <span key={tag}>{tag}</span>)}</div></td>
                  <td><div className="row-actions">
                    <button className="icon-btn" title="测试连接" onClick={() => test(item)}><FlaskConical size={16} /></button>
                    <button className="icon-btn" title="编辑" onClick={() => startEdit(item)}><Pencil size={16} /></button>
                    <button className="icon-btn" title={item.enabled ? "停用" : "启用"} onClick={() => toggle(item)}><Power size={16} /></button>
                    <button className="icon-btn danger-icon" title="删除" onClick={() => remove(item)}><Trash2 size={16} /></button>
                  </div></td>
                </tr>
              ))}
              {!instances.isLoading && !instances.data?.instances.length && <tr><td colSpan={8} className="empty-row">尚未登记实例</td></tr>}
            </tbody>
          </table>
        </div>
      </section>

      <Modal
        open={open}
        title={editing ? `编辑 ${editing.name}` : "添加实例"}
        onClose={() => setOpen(false)}
        footer={<><button className="secondary-btn" onClick={() => setOpen(false)}>取消</button><button className="primary-btn" onClick={() => save.mutate()} disabled={save.isPending}>保存</button></>}
      >
        <form className="form-grid" onSubmit={(event: FormEvent) => { event.preventDefault(); save.mutate(); }}>
          <label>实例名称<input value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} required /></label>
          <label>部署位置<input value={form.location} onChange={(event) => setForm({ ...form, location: event.target.value })} /></label>
          <label className="form-span">HTTPS 地址<input type="url" value={form.base_url} onChange={(event) => setForm({ ...form, base_url: event.target.value })} placeholder="https://adobe-east.example.com" required /></label>
          <label className="form-span">标签<input value={form.tags} onChange={(event) => setForm({ ...form, tags: event.target.value })} placeholder="production, image" /></label>
          <label className="check-row form-span"><input type="checkbox" checked={form.enabled} onChange={(event) => setForm({ ...form, enabled: event.target.checked })} />启用监控</label>
        </form>
      </Modal>
    </div>
  );
}
