import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Check,
  CircleAlert,
  CircleStop,
  Clock3,
  LoaderCircle,
  RefreshCcw,
  Server,
  UserRound
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { apiFetch, emitToast } from "../lib/api";
import type { AccountItem, AccountSafeReplaceOperation } from "../types";
import { Modal } from "./Modal";

const TERMINAL_STATUSES = new Set(["done", "partial", "failed", "cancelled"]);

const PHASES = [
  { key: "starting", label: "目标校验" },
  { key: "pulling", label: "母号拉号" },
  { key: "importing", label: "Cookie 回写" },
  { key: "cleanup", label: "旧号清理" }
] as const;

function phaseIndex(phase?: AccountSafeReplaceOperation["phase"]) {
  if (phase === "pulling") return 1;
  if (phase === "importing") return 2;
  if (phase === "cleanup") return 3;
  if (phase === "complete") return 4;
  return 0;
}

export function AccountSafeReplaceModal({
  open,
  account,
  onClose
}: {
  open: boolean;
  account: AccountItem;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const logRef = useRef<HTMLDivElement | null>(null);
  const [operation, setOperation] = useState<AccountSafeReplaceOperation | null>(null);
  const [failure, setFailure] = useState("");
  const sourceEmail = account.email || (account.name.includes("@") ? account.name : "");
  const terminal = Boolean(operation && TERMINAL_STATUSES.has(operation.status));
  const active = Boolean(operation && !terminal);

  const refreshManagerData = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["accounts"] }),
      queryClient.invalidateQueries({ queryKey: ["instance-accounts", account.instance_id] }),
      queryClient.invalidateQueries({ queryKey: ["dashboard"] }),
      queryClient.invalidateQueries({ queryKey: ["instance", account.instance_id] })
    ]);
  };

  const handleTerminal = async (payload: AccountSafeReplaceOperation) => {
    if (payload.status === "done") {
      emitToast("移除并安全补号已完成", "success");
      await refreshManagerData();
    } else if (payload.status === "partial") {
      emitToast(payload.result?.message || "补号完成，部分回写步骤需要确认", "error");
      await refreshManagerData();
    } else if (payload.status === "cancelled") {
      emitToast("拉号任务已停止", "info");
    } else if (payload.status === "failed") {
      emitToast(payload.error || "移除并安全补号出现错误", "error");
    }
  };

  const startOperation = useMutation({
    mutationFn: () => {
      if (!sourceEmail) throw new Error("该 Cookie 账号缺少可用于母号定位的邮箱");
      return apiFetch<AccountSafeReplaceOperation>(
        `/instances/${account.instance_id}/refresh-profiles/${account.id}/replace-safe/start`,
        {
          method: "POST",
          body: JSON.stringify({ email: sourceEmail })
        }
      );
    },
    onSuccess: (payload) => {
      setOperation(payload);
      setFailure("");
    },
    onError: (error) => {
      const message = error instanceof Error ? error.message : "启动补号流程失败";
      setFailure(message);
      emitToast(message, "error");
    }
  });

  const pollOperation = useMutation({
    mutationFn: (operationId: string) => apiFetch<AccountSafeReplaceOperation>(
      `/safe-replacements/${operationId}/poll`,
      { method: "POST" }
    ),
    onSuccess: async (payload) => {
      const wasTerminal = operation ? TERMINAL_STATUSES.has(operation.status) : false;
      setOperation(payload);
      if (!wasTerminal && TERMINAL_STATUSES.has(payload.status)) {
        await handleTerminal(payload);
      }
    },
    onError: (error) => {
      const message = error instanceof Error ? error.message : "读取拉号进度失败";
      setFailure(message);
    }
  });

  const cancelOperation = useMutation({
    mutationFn: (operationId: string) => apiFetch<AccountSafeReplaceOperation>(
      `/safe-replacements/${operationId}/cancel`,
      { method: "POST" }
    ),
    onSuccess: (payload) => {
      setOperation(payload);
      emitToast("已请求停止拉号", "info");
    },
    onError: (error) => {
      const message = error instanceof Error ? error.message : "停止拉号失败";
      setFailure(message);
      emitToast(message, "error");
    }
  });

  useEffect(() => {
    if (!open) return;
    setOperation(null);
    setFailure("");
  }, [open, account.id]);

  useEffect(() => {
    if (!operation || TERMINAL_STATUSES.has(operation.status) || pollOperation.isPending) return;
    const timer = window.setTimeout(() => pollOperation.mutate(operation.id), 900);
    return () => window.clearTimeout(timer);
  }, [operation?.id, operation?.status, operation?.updated_at, pollOperation.isPending]);

  useEffect(() => {
    const node = logRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [operation?.logs.length]);

  const progress = useMemo(() => {
    if (!operation) return 0;
    if (["done", "partial"].includes(operation.status)) return 100;
    return Math.min(99, Math.round(((operation.success + operation.fail) / Math.max(1, operation.target)) * 100));
  }, [operation]);

  const close = () => {
    if (!active && !startOperation.isPending && !cancelOperation.isPending) onClose();
  };

  const acknowledged = Boolean(terminal || (!operation && failure));
  const footer = acknowledged ? (
    <button className="primary-btn" onClick={close}>确认</button>
  ) : active ? (
    <button
      className="danger-btn"
      disabled={!operation?.can_cancel || cancelOperation.isPending}
      onClick={() => operation && cancelOperation.mutate(operation.id)}
    >
      {cancelOperation.isPending || operation?.cancel_requested
        ? <LoaderCircle className="spin" size={16} />
        : <CircleStop size={16} />}
      {operation?.cancel_requested ? "正在停止" : "停止拉号"}
    </button>
  ) : (
    <>
      <button className="secondary-btn" disabled={startOperation.isPending} onClick={close}>取消</button>
      <button className="primary-btn" disabled={startOperation.isPending} onClick={() => startOperation.mutate()}>
        {startOperation.isPending ? <LoaderCircle className="spin" size={16} /> : <RefreshCcw size={16} />}
        {startOperation.isPending ? "正在启动" : "移除并安全补号"}
      </button>
    </>
  );

  const currentPhase = phaseIndex(operation?.phase);
  const operationError = operation && ["failed", "cancelled"].includes(operation.status)
    ? operation.error
    : "";

  return <Modal
    open={open}
    title={operation?.status === "cancelled"
      ? "拉号任务已停止"
      : operationError || failure
        ? "移除并安全补号出现错误"
        : "移除并安全补号"}
    onClose={close}
    footer={footer}
    wide={Boolean(operation)}
  >
    <div className="safe-replace-modal">
      <div className="safe-replace-target">
        <UserRound size={18} />
        <span>当前账号</span>
        <strong>{account.display_name || account.name}</strong>
        <small>{sourceEmail || "缺少邮箱"}</small>
        <Server size={18} />
        <span>回写实例</span>
        <strong>{account.instance_name}</strong>
        <small>{account.id}</small>
      </div>

      {!operation && !failure && !startOperation.isPending && (
        <div className="warning-banner">
          确认后将移除当前子号，安全补入一个新子号，并用新 Cookie 替换此实例中的账号。
        </div>
      )}

      {startOperation.isPending && (
        <div className="safe-replace-running" role="status">
          <LoaderCircle className="spin" size={22} />
          <div><strong>正在校验并创建母号任务</strong><span>目标账号尚未发生变更</span></div>
        </div>
      )}

      {operation && (
        <>
          <div className="safe-replace-phases">
            {PHASES.map((phase, index) => {
              const complete = currentPhase > index;
              const current = currentPhase === index && !terminal;
              const stopped = terminal && currentPhase === index && operation.status === "cancelled";
              return <div className={`${complete ? "complete" : ""}${current ? " current" : ""}${stopped ? " stopped" : ""}`} key={phase.key}>
                <span>{complete ? <Check size={14} /> : current ? <LoaderCircle className="spin" size={14} /> : stopped ? <CircleStop size={14} /> : <Clock3 size={14} />}</span>
                <strong>{phase.label}</strong>
              </div>;
            })}
          </div>

          <div className="safe-replace-progress">
            <div>
              <span>母号任务 {operation.upstream_job_id ? `#${operation.upstream_job_id}` : "创建中"}</span>
              <strong>{operation.cancel_requested ? "停止请求已发送" : operation.status === "finalizing" ? "正在回写" : `${progress}%`}</strong>
            </div>
            <div className="safe-replace-progress-track"><i style={{ width: `${progress}%` }} /></div>
            <small>成功 {operation.success} · 失败 {operation.fail} · 目标 {operation.target}</small>
          </div>

          <section className="safe-replace-log-section">
            <header><strong>拉号流程</strong><span>{operation.logs.length} 条</span></header>
            <div className="safe-replace-logs" ref={logRef} role="log" aria-live="polite">
              {operation.logs.map((line, index) => <div key={`${index}-${line}`}>{line}</div>)}
              {!operation.logs.length && <div className="muted-log">等待母号系统返回日志...</div>}
            </div>
          </section>
        </>
      )}

      {(failure || operationError) && (
        <div className="error-banner safe-replace-error" role="alert">
          <CircleAlert size={18} /><span>{failure || operationError}</span>
        </div>
      )}

      {operation?.result && (
        <>
          <div className="safe-replace-result">
            <div><span>新子号</span><strong>{operation.result.replacement_email || "已补号"}</strong></div>
            <div><span>Cookie 导入</span><strong>{operation.result.imported_count ? "成功" : "异常"}</strong></div>
            <div><span>首次刷新</span><strong className={operation.result.refresh_failed_count ? "text-danger" : "text-success"}>{operation.result.refresh_failed_count ? "失败" : "成功"}</strong></div>
            <div><span>旧账号清理</span><strong className={operation.result.old_profile_removed ? "text-success" : "text-danger"}>{operation.result.old_profile_removed ? "完成" : "待确认"}</strong></div>
          </div>
          {operation.status === "partial" && (
            <div className="error-banner safe-replace-error" role="alert">
              <CircleAlert size={18} /><span>{operation.result.message}</span>
            </div>
          )}
        </>
      )}
    </div>
  </Modal>;
}
