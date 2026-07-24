import { useQueryClient } from "@tanstack/react-query";
import { CirclePause, CirclePlay, MoveRight, Trash2, X } from "lucide-react";
import { useMemo, useState } from "react";
import { apiFetch, emitToast } from "../lib/api";
import type { AccountItem } from "../types";

export function AccountBatchBar({
  accounts,
  selected,
  onSelectionChange,
  onMove,
  compact = false
}: {
  accounts: AccountItem[];
  selected: Set<string>;
  onSelectionChange: (selected: Set<string>) => void;
  onMove?: () => void;
  compact?: boolean;
}) {
  const queryClient = useQueryClient();
  const [busy, setBusy] = useState("");
  const selectedAccounts = useMemo(
    () => accounts.filter((item) => selected.has(`${item.instance_id}:${item.id}`)),
    [accounts, selected]
  );

  const groups = useMemo(() => {
    const result = new Map<string, { instanceName: string; ids: string[] }>();
    for (const item of selectedAccounts) {
      const group = result.get(item.instance_id) || { instanceName: item.instance_name, ids: [] };
      group.ids.push(item.id);
      result.set(item.instance_id, group);
    }
    return result;
  }, [selectedAccounts]);

  if (!selectedAccounts.length) return null;
  const targetSummary = Array.from(groups.values()).map(
    (group) => `${group.instanceName} × ${group.ids.length}`
  ).join("、");

  async function run(action: "enable" | "disable" | "delete") {
    const actionLabel = action === "delete" ? "删除" : action === "disable" ? "暂停" : "启用";
    if (!window.confirm(`确认批量${actionLabel} ${selectedAccounts.length} 个 Cookie 账号？\n目标：${targetSummary}`)) return;
    setBusy(action);
    const results = await Promise.allSettled(Array.from(groups.entries()).map(([instanceId, group]) => {
      const deleting = action === "delete";
      return apiFetch(`/instances/${instanceId}/refresh-profiles/${deleting ? "delete-batch" : "enabled-batch"}`, {
        method: deleting ? "POST" : "PUT",
        body: JSON.stringify(deleting ? { ids: group.ids } : { ids: group.ids, enabled: action === "enable" })
      });
    }));
    const failed = results.filter((result) => result.status === "rejected").length;
    if (failed) emitToast(`批量${actionLabel}完成，${failed} 个实例操作失败`, "error");
    else emitToast(`已批量${actionLabel} ${selectedAccounts.length} 个账号`, "success");
    onSelectionChange(new Set());
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["accounts"] }),
      queryClient.invalidateQueries({ queryKey: ["instance-accounts"] }),
      queryClient.invalidateQueries({ queryKey: ["dashboard"] }),
      queryClient.invalidateQueries({ queryKey: ["instance"] })
    ]);
    setBusy("");
  }

  return <div className={`batch-toolbar${compact ? " batch-toolbar-compact" : ""}`}>
    <strong>已选 {selectedAccounts.length}</strong><span>{targetSummary}</span>
    <div className="inline-actions">
      {onMove && <button className="secondary-btn compact-action" disabled={Boolean(busy)} onClick={onMove}><MoveRight size={15} />批量移动</button>}
      <button className="secondary-btn compact-action" disabled={Boolean(busy)} onClick={() => run("enable")}><CirclePlay size={15} />批量启用</button>
      <button className="secondary-btn compact-action" disabled={Boolean(busy)} onClick={() => run("disable")}><CirclePause size={15} />批量暂停</button>
      <button className="secondary-btn compact-action batch-danger" disabled={Boolean(busy)} onClick={() => run("delete")}><Trash2 size={15} />批量删除</button>
      <button className="icon-btn" title="清除选择" disabled={Boolean(busy)} onClick={() => onSelectionChange(new Set())}><X size={15} /></button>
    </div>
  </div>;
}
