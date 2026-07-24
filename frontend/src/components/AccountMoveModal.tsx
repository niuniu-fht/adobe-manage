import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, Server } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { apiFetch, emitToast } from "../lib/api";
import type { AccountItem, AccountMoveResponse, FleetInstance } from "../types";
import { Modal } from "./Modal";

export function AccountMoveModal({
  open,
  onClose,
  source,
  instances,
  accounts,
  onMoved
}: {
  open: boolean;
  onClose: () => void;
  source: FleetInstance;
  instances: FleetInstance[];
  accounts: AccountItem[];
  onMoved?: () => void;
}) {
  const queryClient = useQueryClient();
  const [targetId, setTargetId] = useState("");
  const [result, setResult] = useState<AccountMoveResponse | null>(null);
  const targets = useMemo(
    () => instances.filter((item) =>
      item.id !== source.id
      && item.enabled
      && item.state !== "offline"
      && (!item.capabilities.length || item.capabilities.includes("refresh_profiles"))
    ),
    [instances, source.id]
  );
  const target = targets.find((item) => item.id === targetId);

  useEffect(() => {
    if (!open) return;
    setTargetId("");
    setResult(null);
  }, [open]);

  const moveAccounts = useMutation({
    mutationFn: () => {
      if (!targetId) throw new Error("请选择目标实例");
      return apiFetch<AccountMoveResponse>(`/instances/${source.id}/refresh-profiles/move`, {
        method: "POST",
        body: JSON.stringify({
          ids: accounts.map((item) => item.id),
          target_instance_id: targetId
        })
      });
    },
    onSuccess: async (payload) => {
      setResult(payload);
      if (payload.source_state_unknown_count) {
        emitToast(`已写入目标实例，${payload.source_state_unknown_count} 个源账号删除状态待确认`, "error");
      } else if (payload.status === "ok") {
        emitToast(`已移动 ${payload.moved_count} 个账号到 ${payload.target.name}`, "success");
      } else if (payload.moved_count) {
        emitToast(`已移动 ${payload.moved_count} 个账号，${payload.retained_count} 个仍保留在源实例`, "info");
      } else {
        emitToast("本次没有账号完成移动，源账号已保留", "error");
      }
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["accounts"] }),
        queryClient.invalidateQueries({ queryKey: ["instance-accounts", source.id] }),
        queryClient.invalidateQueries({ queryKey: ["instance-accounts", targetId] }),
        queryClient.invalidateQueries({ queryKey: ["dashboard"] }),
        queryClient.invalidateQueries({ queryKey: ["instance", source.id] }),
        queryClient.invalidateQueries({ queryKey: ["instance", targetId] })
      ]);
      onMoved?.();
    },
    onError: (error) => emitToast(error.message, "error")
  });

  const footer = result ? (
    <button className="primary-btn" onClick={onClose}>完成</button>
  ) : (
    <>
      <button className="secondary-btn" disabled={moveAccounts.isPending} onClick={onClose}>取消</button>
      <button className="primary-btn" disabled={!targetId || !accounts.length || moveAccounts.isPending} onClick={() => moveAccounts.mutate()}>
        <ArrowRight size={16} />{moveAccounts.isPending ? "正在移动" : `移动 ${accounts.length} 个账号`}
      </button>
    </>
  );

  return <Modal open={open} title="批量移动 Cookie 账号" onClose={() => { if (!moveAccounts.isPending) onClose(); }} footer={footer}>
    <div className="account-move-modal">
      <div className="account-move-route">
        <div><Server size={16} /><span>源实例</span><strong>{source.name}</strong><small>{source.location || source.base_url}</small></div>
        <ArrowRight className="account-move-arrow" size={20} />
        <div><Server size={16} /><span>目标实例</span><strong>{target?.name || "待选择"}</strong><small>{target ? (target.location || target.base_url) : `${targets.length} 个可用实例`}</small></div>
      </div>
      {!result && <>
        <label className="account-move-target">目标实例
          <select aria-label="目标实例" value={targetId} onChange={(event) => setTargetId(event.target.value)}>
            <option value="">选择目标实例</option>
            {targets.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.location || item.base_url}{item.state !== "online" ? ` · ${item.state === "offline" ? "离线" : "状态未知"}` : ""}</option>)}
          </select>
        </label>
        {!targets.length && <div className="error-banner">没有其他可接收 Cookie 账号的实例</div>}
        <div className="account-move-selection"><strong>已选 {accounts.length} 个账号</strong><span>{accounts.slice(0, 5).map((item) => item.display_name || item.name).join("、")}{accounts.length > 5 ? ` 等 ${accounts.length} 个` : ""}</span></div>
        <div className="warning-banner">目标实例写入成功后才删除源账号；导入失败的账号继续保留在源实例。</div>
      </>}
      {result && <>
        <div className="account-move-result">
          <div><span>选择</span><strong>{result.requested_count}</strong></div>
          <div><span>移动完成</span><strong className="text-success">{result.moved_count}</strong></div>
          <div><span>源端保留</span><strong className={result.retained_count ? "text-danger" : ""}>{result.retained_count}</strong></div>
          <div><span>刷新异常</span><strong className={result.refresh_failed_count ? "text-danger" : ""}>{result.refresh_failed_count}</strong></div>
        </div>
        {(result.cleanup_failed_count > 0 || result.source_state_unknown_count > 0) && <div className="error-banner">有 {result.cleanup_failed_count + result.source_state_unknown_count} 个账号需要刷新源、目标列表后确认位置。</div>}
      </>}
    </div>
  </Modal>;
}
