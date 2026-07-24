import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { apiFetch, emitToast } from "../lib/api";
import type { CookieImportItem } from "../lib/cookies";
import type { FleetInstance } from "../types";
import { CookieSourceInput } from "./CookieSourceInput";
import { Modal } from "./Modal";

interface ImportSummary {
  total: number;
  imported: number;
  failed: number;
  refreshed: number;
  refreshFailed: number;
}

export function CookieImportModal({
  open,
  onClose,
  fixedInstanceId,
  fixedInstanceName,
  onImported
}: {
  open: boolean;
  onClose: () => void;
  fixedInstanceId?: string;
  fixedInstanceName?: string;
  onImported?: () => void;
}) {
  const queryClient = useQueryClient();
  const [targetId, setTargetId] = useState(fixedInstanceId || "");
  const [selectedItems, setSelectedItems] = useState<CookieImportItem[]>([]);
  const [parseError, setParseError] = useState("");
  const [result, setResult] = useState<ImportSummary | null>(null);
  const instances = useQuery({
    queryKey: ["instances"],
    queryFn: () => apiFetch<{ instances: FleetInstance[] }>("/instances"),
    enabled: open && !fixedInstanceId
  });

  useEffect(() => {
    if (!open) return;
    setTargetId(fixedInstanceId || "");
    setSelectedItems([]);
    setParseError("");
    setResult(null);
  }, [open, fixedInstanceId]);

  const importCookies = useMutation({
    mutationFn: async () => {
      if (!targetId) throw new Error("请选择目标实例");
      if (!selectedItems.length) throw new Error(parseError || "请粘贴或选择 Cookie 文件");
      const body = selectedItems.length === 1 ? selectedItems[0] : { items: selectedItems };
      return apiFetch<Record<string, unknown>>(`/instances/${targetId}/refresh-profiles/import`, {
        method: "POST",
        body: JSON.stringify(body)
      });
    },
    onSuccess: async (payload) => {
      const isBatch = selectedItems.length > 1;
      const refreshError = String(payload.refresh_error || "").trim();
      const summary: ImportSummary = isBatch ? {
        total: selectedItems.length,
        imported: Number(payload.imported_count || 0),
        failed: Number(payload.failed_count || 0),
        refreshed: Number(payload.refreshed_count || 0),
        refreshFailed: Number(payload.refresh_failed_count || 0)
      } : {
        total: 1,
        imported: 1,
        failed: 0,
        refreshed: refreshError ? 0 : 1,
        refreshFailed: refreshError ? 1 : 0
      };
      setResult(summary);
      emitToast(summary.failed || summary.refreshFailed ? "Cookie 已导入，部分账号需要处理" : "Cookie 账号导入完成", summary.failed || summary.refreshFailed ? "info" : "success");
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["accounts"] }),
        queryClient.invalidateQueries({ queryKey: ["instance-accounts", targetId] }),
        queryClient.invalidateQueries({ queryKey: ["dashboard"] }),
        queryClient.invalidateQueries({ queryKey: ["instance", targetId] })
      ]);
      onImported?.();
    },
    onError: (error) => emitToast(error.message, "error")
  });

  const footer = result ? (
    <button className="primary-btn" onClick={onClose}>完成</button>
  ) : (
    <><button className="secondary-btn" onClick={onClose}>取消</button><button className="primary-btn" disabled={!targetId || !selectedItems.length || Boolean(parseError) || importCookies.isPending} onClick={() => importCookies.mutate()}>{importCookies.isPending ? "正在导入" : (selectedItems.length ? `导入 ${selectedItems.length} 个账号` : "导入账号")}</button></>
  );

  return <Modal open={open} title="导入 Cookie 账号" onClose={onClose} footer={footer}>
    <div className="cookie-import">
      <label>目标实例
        {fixedInstanceId ? <strong className="fixed-target">{fixedInstanceName || fixedInstanceId}</strong> : <select value={targetId} onChange={(event) => setTargetId(event.target.value)}><option value="">选择实例</option>{instances.data?.instances.filter((item) => item.enabled).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select>}
      </label>
      {!result && <CookieSourceInput onChange={(items, error) => { setSelectedItems(items); setParseError(error); }} />}
      {result && <div className="import-result"><div><span>解析</span><strong>{result.total}</strong></div><div><span>导入成功</span><strong>{result.imported}</strong></div><div><span>导入失败</span><strong className={result.failed ? "text-danger" : ""}>{result.failed}</strong></div><div><span>刷新成功</span><strong>{result.refreshed}</strong></div><div><span>刷新失败</span><strong className={result.refreshFailed ? "text-danger" : ""}>{result.refreshFailed}</strong></div></div>}
    </div>
  </Modal>;
}
