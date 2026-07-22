import type { AccountHealth } from "../types";

const labels: Record<AccountHealth, string> = {
  healthy: "正常",
  low_credit: "低积分",
  balance_unknown: "余额未知",
  refresh_failed: "刷新失败",
  credential_error: "凭证异常",
  disabled: "已暂停"
};

export function AccountHealthBadge({ health }: { health: AccountHealth }) {
  return <span className={`account-health health-${health}`}>{labels[health] || health}</span>;
}
