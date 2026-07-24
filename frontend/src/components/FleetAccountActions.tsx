import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { apiFetch, emitToast, formatNumber } from "../lib/api";
import type { CookieImportItem } from "../lib/cookies";
import type { AccountsResponse, FleetInstance } from "../types";
import { CookieSourceInput } from "./CookieSourceInput";
import { Modal } from "./Modal";

interface FleetImportInstanceResult {
  instance_id: string;
  instance_name: string;
  before_count: number;
  target_count: number;
  deficit: number;
  assigned_count: number;
  imported_count: number;
  failed_count: number;
  refreshed_count: number;
  refresh_failed_count: number;
  status: string;
  error: string;
}

interface FleetImportResponse {
  status: "ok" | "partial" | "failed";
  total: number;
  assigned: number;
  imported: number;
  failed: number;
  refreshed: number;
  refresh_failed: number;
  instances: FleetImportInstanceResult[];
}

interface FleetDeleteInstanceResult {
  instance_id: string;
  instance_name: string;
  matched_count: number;
  deleted_count: number;
  missing_count: number;
  status: string;
  error: string;
}

interface FleetDeleteResponse {
  status: "ok" | "partial";
  credit_threshold: number;
  matched_count: number;
  deleted_count: number;
  missing_count: number;
  failed_instances: number;
  instances: FleetDeleteInstanceResult[];
}

function currentAccountCount(instance: FleetInstance) {
  return instance.snapshot?.accounts?.total
    ?? instance.snapshot?.refresh_profiles.total
    ?? instance.snapshot?.tokens.total
    ?? 0;
}

function supportsFleetAccounts(instance: FleetInstance) {
  return instance.enabled && (
    !instance.capabilities.length
    || (instance.capabilities.includes("accounts") && instance.capabilities.includes("refresh_profiles"))
  );
}

async function invalidateFleetQueries(queryClient: ReturnType<typeof useQueryClient>) {
  await Promise.all([
    queryClient.invalidateQueries({ queryKey: ["dashboard"] }),
    queryClient.invalidateQueries({ queryKey: ["accounts"] }),
    queryClient.invalidateQueries({ queryKey: ["instance-accounts"] }),
    queryClient.invalidateQueries({ queryKey: ["instance"] }),
    queryClient.invalidateQueries({ queryKey: ["fleet-delete-preview"] })
  ]);
}

export function FleetImportModal({
  open,
  onClose,
  instances,
  savedTargets
}: {
  open: boolean;
  onClose: () => void;
  instances: FleetInstance[];
  savedTargets: Record<string, number>;
}) {
  const queryClient = useQueryClient();
  const eligibleInstances = useMemo(
    () => instances.filter(supportsFleetAccounts),
    [instances]
  );
  const [targetValues, setTargetValues] = useState<Record<string, string>>({});
  const [items, setItems] = useState<CookieImportItem[]>([]);
  const [parseError, setParseError] = useState("");
  const [result, setResult] = useState<FleetImportResponse | null>(null);

  useEffect(() => {
    if (!open) return;
    setTargetValues(Object.fromEntries(eligibleInstances.map((instance) => [
      instance.id,
      String(savedTargets[instance.id] ?? currentAccountCount(instance))
    ])));
    setItems([]);
    setParseError("");
    setResult(null);
    // Only reset when the modal opens; dashboard polling must not erase active input.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const targetRows = eligibleInstances.map((instance) => {
    const current = currentAccountCount(instance);
    const parsed = Number(targetValues[instance.id]);
    const target = Number.isInteger(parsed) && parsed >= 0 ? parsed : null;
    return {
      instance,
      current,
      target,
      deficit: target == null ? 0 : Math.max(0, target - current)
    };
  });
  const invalidTargets = targetRows.some((row) => row.target == null);
  const totalDeficit = targetRows.reduce((sum, row) => sum + row.deficit, 0);
  const overflow = Math.max(0, items.length - totalDeficit);

  const importAccounts = useMutation({
    mutationFn: () => {
      if (!eligibleInstances.length) throw new Error("没有可管理的实例");
      if (!items.length) throw new Error(parseError || "请粘贴或选择 Cookie 文件");
      if (invalidTargets) throw new Error("目标账号数需要填写非负整数");
      return apiFetch<FleetImportResponse>("/fleet/accounts/import", {
        method: "POST",
        body: JSON.stringify({
          items,
          targets: targetRows.map((row) => ({
            instance_id: row.instance.id,
            target_count: row.target
          }))
        })
      });
    },
    onSuccess: async (payload) => {
      setResult(payload);
      emitToast(
        payload.status === "ok"
          ? `已统一导入 ${payload.imported} 个账号`
          : `导入完成，${payload.failed + payload.refresh_failed} 个账号需要处理`,
        payload.status === "ok" ? "success" : "info"
      );
      await invalidateFleetQueries(queryClient);
    },
    onError: (error) => emitToast(error.message, "error")
  });

  const footer = result ? <button className="primary-btn" onClick={onClose}>完成</button> : <>
    <button className="secondary-btn" onClick={onClose}>取消</button>
    <button className="primary-btn" disabled={!items.length || Boolean(parseError) || invalidTargets || !eligibleInstances.length || importAccounts.isPending} onClick={() => importAccounts.mutate()}>{importAccounts.isPending ? "正在分配并导入" : (items.length ? `分配并导入 ${items.length} 个账号` : "分配并导入")}</button>
  </>;

  return <Modal open={open} title="统一导入 Cookie 账号" onClose={onClose} footer={footer} wide>
    <div className="fleet-import-modal">
      {!result && <>
        <section className="fleet-target-editor">
          <div className="fleet-target-head"><strong>实例目标数量</strong><span>当前账号</span><span>目标账号</span><span>待补足</span></div>
          <div className="fleet-target-list">
            {targetRows.map(({ instance, current, target, deficit }) => <label key={instance.id}>
              <span><strong>{instance.name}</strong><small>{instance.state === "online" ? "在线" : instance.state}</small></span>
              <b>{current}</b>
              <input type="number" min="0" step="1" value={targetValues[instance.id] ?? ""} onChange={(event) => setTargetValues((currentValues) => ({ ...currentValues, [instance.id]: event.target.value }))} />
              <b className={deficit > 0 ? "text-danger" : ""}>{target == null ? "-" : deficit}</b>
            </label>)}
          </div>
          <div className="fleet-distribution-summary"><span>优先补足 <strong>{Math.min(items.length, totalDeficit)}</strong></span><span>轮询分配 <strong>{overflow}</strong></span><span>参与实例 <strong>{targetRows.length}</strong></span></div>
        </section>
        <section className="cookie-import fleet-cookie-source">
          <CookieSourceInput onChange={(nextItems, error) => { setItems(nextItems); setParseError(error); }} />
        </section>
      </>}
      {result && <>
        <div className="import-result fleet-import-result"><div><span>解析</span><strong>{result.total}</strong></div><div><span>导入成功</span><strong>{result.imported}</strong></div><div><span>导入失败</span><strong className={result.failed ? "text-danger" : ""}>{result.failed}</strong></div><div><span>刷新成功</span><strong>{result.refreshed}</strong></div><div><span>刷新失败</span><strong className={result.refresh_failed ? "text-danger" : ""}>{result.refresh_failed}</strong></div></div>
        <div className="fleet-operation-results">
          <div className="fleet-operation-head"><span>实例</span><span>导入前 / 目标</span><span>分配</span><span>导入</span><span>失败</span></div>
          {result.instances.map((item) => <div key={item.instance_id}><span><strong>{item.instance_name}</strong>{item.error && <small>{item.error}</small>}</span><span>{item.before_count} / {item.target_count}</span><span>{item.assigned_count}</span><span>{item.imported_count}</span><span className={item.failed_count || item.refresh_failed_count ? "text-danger" : ""}>{item.failed_count + item.refresh_failed_count}</span></div>)}
        </div>
      </>}
    </div>
  </Modal>;
}

export function FleetLowCreditDeleteModal({
  open,
  onClose,
  defaultThreshold
}: {
  open: boolean;
  onClose: () => void;
  defaultThreshold: number;
}) {
  const queryClient = useQueryClient();
  const [threshold, setThreshold] = useState(String(defaultThreshold));
  const [result, setResult] = useState<FleetDeleteResponse | null>(null);
  const accounts = useQuery({
    queryKey: ["fleet-delete-preview"],
    queryFn: () => apiFetch<AccountsResponse>("/accounts"),
    enabled: open && !result,
    staleTime: 0
  });

  useEffect(() => {
    if (!open) return;
    setThreshold(String(defaultThreshold));
    setResult(null);
  }, [open, defaultThreshold]);

  const thresholdNumber = Number(threshold);
  const thresholdValid = Number.isFinite(thresholdNumber) && thresholdNumber >= 0;
  const matched = useMemo(() => (accounts.data?.accounts || []).filter(
    (item) => thresholdValid
      && item.credits_available != null
      && item.credits_available < thresholdNumber
  ), [accounts.data, thresholdNumber, thresholdValid]);
  const unknownCount = (accounts.data?.accounts || []).filter(
    (item) => item.credits_available == null
  ).length;
  const instanceCounts = useMemo(() => {
    const grouped = new Map<string, { name: string; count: number }>();
    for (const item of matched) {
      const row = grouped.get(item.instance_id) || { name: item.instance_name, count: 0 };
      row.count += 1;
      grouped.set(item.instance_id, row);
    }
    return Array.from(grouped.entries());
  }, [matched]);

  const deleteAccounts = useMutation({
    mutationFn: () => {
      if (!thresholdValid) throw new Error("积分阈值需要填写非负数字");
      if (!window.confirm(`确认删除所有实例中积分低于 ${thresholdNumber} 的 ${matched.length} 个账号？`)) {
        throw new Error("操作已取消");
      }
      return apiFetch<FleetDeleteResponse>("/fleet/accounts/delete-low-credit", {
        method: "POST",
        body: JSON.stringify({ credit_threshold: thresholdNumber })
      });
    },
    onSuccess: async (payload) => {
      setResult(payload);
      emitToast(
        payload.status === "ok"
          ? `已删除 ${payload.deleted_count} 个低积分账号`
          : `删除完成，${payload.failed_instances} 个实例操作失败`,
        payload.status === "ok" ? "success" : "error"
      );
      await invalidateFleetQueries(queryClient);
    },
    onError: (error) => {
      if (error.message !== "操作已取消") emitToast(error.message, "error");
    }
  });

  const previewReady = !accounts.isLoading && !accounts.isError && accounts.data?.status !== "partial";
  const footer = result ? <button className="primary-btn" onClick={onClose}>完成</button> : <>
    <button className="secondary-btn" onClick={onClose}>取消</button>
    <button className="danger-btn" disabled={!previewReady || !thresholdValid || !matched.length || deleteAccounts.isPending} onClick={() => deleteAccounts.mutate()}><Trash2 size={15} />{deleteAccounts.isPending ? "正在删除" : `删除 ${matched.length} 个账号`}</button>
  </>;

  return <Modal open={open} title="清理低积分账号" onClose={onClose} footer={footer}>
    <div className="fleet-delete-modal">
      {!result && <>
        <label className="delete-threshold-field"><span>积分低于</span><input type="number" min="0" value={threshold} onChange={(event) => setThreshold(event.target.value)} /></label>
        {accounts.isLoading && <div className="empty-row">正在读取所有实例账号...</div>}
        {accounts.isError && <div className="error-banner">{accounts.error.message}</div>}
        {accounts.data?.status === "partial" && <div className="warning-banner"><AlertTriangle size={15} />部分实例读取失败，删除操作已暂停</div>}
        {!accounts.isLoading && !accounts.isError && <div className="delete-preview-band"><div><span>匹配账号</span><strong className={matched.length ? "text-danger" : ""}>{matched.length}</strong></div><div><span>涉及实例</span><strong>{instanceCounts.length}</strong></div><div><span>余额未知保留</span><strong>{unknownCount}</strong></div></div>}
        <div className="delete-instance-list">{instanceCounts.map(([instanceId, row]) => <div key={instanceId}><span>{row.name}</span><strong>{row.count}</strong></div>)}</div>
      </>}
      {result && <>
        <div className="delete-result-summary"><Trash2 size={20} /><span>已删除</span><strong>{result.deleted_count}</strong><small>阈值 {formatNumber(result.credit_threshold, 1)}</small></div>
        <div className="fleet-operation-results compact-results">
          <div className="fleet-operation-head"><span>实例</span><span>匹配</span><span>删除</span><span>遗漏</span></div>
          {result.instances.map((item) => <div key={item.instance_id}><span><strong>{item.instance_name}</strong>{item.error && <small>{item.error}</small>}</span><span>{item.matched_count}</span><span>{item.deleted_count}</span><span className={item.missing_count || item.error ? "text-danger" : ""}>{item.missing_count}</span></div>)}
        </div>
      </>}
    </div>
  </Modal>;
}
