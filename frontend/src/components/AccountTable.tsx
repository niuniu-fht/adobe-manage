import { useQueryClient } from "@tanstack/react-query";
import { CirclePause, CirclePlay, RefreshCw, Trash2, UserRoundCog } from "lucide-react";
import { useState } from "react";
import { apiFetch, emitToast, formatNumber, formatTime } from "../lib/api";
import type { AccountItem } from "../types";
import { AccountHealthBadge } from "./AccountHealthBadge";
import { AccountSafeReplaceModal } from "./AccountSafeReplaceModal";

export function AccountTable({
  accounts,
  compact = false,
  loading = false,
  selected,
  onSelectionChange
}: {
  accounts: AccountItem[];
  compact?: boolean;
  loading?: boolean;
  selected?: Set<string>;
  onSelectionChange?: (selected: Set<string>) => void;
}) {
  const queryClient = useQueryClient();
  const [busy, setBusy] = useState("");
  const [replaceTarget, setReplaceTarget] = useState<AccountItem | null>(null);
  const selectable = Boolean(selected && onSelectionChange);
  const accountKey = (item: AccountItem) => `${item.instance_id}:${item.id}`;
  const allSelected = Boolean(
    selectable && accounts.length && accounts.every((item) => selected?.has(accountKey(item)))
  );

  function toggleAll() {
    if (!selected || !onSelectionChange) return;
    const next = new Set(selected);
    if (allSelected) accounts.forEach((item) => next.delete(accountKey(item)));
    else accounts.forEach((item) => next.add(accountKey(item)));
    onSelectionChange(next);
  }

  function toggle(item: AccountItem) {
    if (!selected || !onSelectionChange) return;
    const next = new Set(selected);
    const key = accountKey(item);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    onSelectionChange(next);
  }

  async function run(item: AccountItem, action: string, path: string, method: string, success: string) {
    const key = `${item.instance_id}:${item.id}:${action}`;
    setBusy(key);
    try {
      await apiFetch(path, { method });
      emitToast(success, "success");
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["accounts"] }),
        queryClient.invalidateQueries({ queryKey: ["instance-accounts", item.instance_id] }),
        queryClient.invalidateQueries({ queryKey: ["dashboard"] }),
        queryClient.invalidateQueries({ queryKey: ["instance", item.instance_id] })
      ]);
    } catch (error) {
      emitToast(error instanceof Error ? error.message : "操作失败", "error");
    } finally {
      setBusy("");
    }
  }

  function remove(item: AccountItem) {
    const accountName = item.display_name || item.email || item.name;
    if (!window.confirm(`确认从「${item.instance_name}」删除 Cookie 账号「${accountName}」？`)) return;
    void run(
      item,
      "delete",
      `/instances/${item.instance_id}/refresh-profiles/${item.id}`,
      "DELETE",
      "Cookie 账号已删除"
    );
  }

  return <>
    <table className={compact ? "account-table account-table-compact" : "account-table"}>
      <thead><tr>
        {selectable && <th className="selection-cell"><input type="checkbox" aria-label="选择当前账号列表" checked={allSelected} onChange={toggleAll} /></th>}
        {!compact && <th>实例</th>}
        <th>账号</th><th>健康状态</th><th>剩余积分</th><th>总积分</th>
        <th>最近刷新</th><th>下次刷新</th><th>凭证到期</th><th>错误</th><th>操作</th>
      </tr></thead>
      <tbody>
        {accounts.map((item) => {
          const accountName = item.display_name || item.email || item.name;
          const refreshKey = `${item.instance_id}:${item.id}:refresh`;
          const enabledKey = `${item.instance_id}:${item.id}:enabled`;
          const deleteKey = `${item.instance_id}:${item.id}:delete`;
          return <tr className={selected?.has(accountKey(item)) ? "row-selected" : ""} key={`${item.instance_id}-${item.id}`}>
            {selectable && <td className="selection-cell"><input type="checkbox" aria-label={`选择 ${accountName}`} checked={selected?.has(accountKey(item)) || false} onChange={() => toggle(item)} /></td>}
            {!compact && <td><strong>{item.instance_name}</strong></td>}
            <td className="account-identity"><strong>{accountName}</strong><small>{item.email || item.name}</small>{item.duplicate && <span className="duplicate-mark" title={`同时存在于 ${item.duplicate_instances?.join("、")}`}>重复账号</span>}</td>
            <td><div className="health-stack"><AccountHealthBadge health={item.health} />{item.low_credit && item.health !== "low_credit" && <span className="low-credit-note">同时低积分</span>}</div></td>
            <td><strong className={item.low_credit ? "text-danger" : ""}>{item.credits_available == null ? "-" : formatNumber(item.credits_available, 1)}</strong></td>
            <td>{item.credits_total == null ? "-" : formatNumber(item.credits_total, 1)}</td>
            <td>{formatTime(item.last_success_at)}</td>
            <td>{item.enabled ? formatTime(item.next_refresh_at) : "已暂停"}</td>
            <td>{formatTime(item.credential_expires_at)}</td>
            <td className="truncate-cell" title={item.last_error}>{item.last_error || "-"}</td>
            <td><div className="row-actions">
              {!compact && <button className="icon-btn safe-replace-icon" title="移除并安全补号" onClick={() => setReplaceTarget(item)}><UserRoundCog size={16} /></button>}
              <button className="icon-btn" title="立即刷新账号" disabled={busy === refreshKey} onClick={() => run(item, "refresh", `/instances/${item.instance_id}/refresh-profiles/${item.id}/refresh`, "POST", "账号已刷新")}><RefreshCw size={16} className={busy === refreshKey ? "spin" : ""} /></button>
              <button className="icon-btn" title={item.enabled ? "暂停自动刷新" : "启用自动刷新"} disabled={busy === enabledKey} onClick={() => run(item, "enabled", `/instances/${item.instance_id}/refresh-profiles/${item.id}/enabled?enabled=${!item.enabled}`, "PUT", item.enabled ? "自动刷新已暂停" : "自动刷新已启用")}>{item.enabled ? <CirclePause size={16} /> : <CirclePlay size={16} />}</button>
              {!compact && <button className="icon-btn danger-icon" title="删除 Cookie 账号" disabled={busy === deleteKey} onClick={() => remove(item)}><Trash2 size={16} /></button>}
            </div></td>
          </tr>;
        })}
        {!loading && accounts.length === 0 && <tr><td colSpan={(compact ? 9 : 10) + (selectable ? 1 : 0)} className="empty-row">没有匹配的 Cookie 账号</td></tr>}
        {loading && <tr><td colSpan={(compact ? 9 : 10) + (selectable ? 1 : 0)} className="empty-row">正在读取账号...</td></tr>}
      </tbody>
    </table>
    {replaceTarget && <AccountSafeReplaceModal open account={replaceTarget} onClose={() => setReplaceTarget(null)} />}
  </>;
}
