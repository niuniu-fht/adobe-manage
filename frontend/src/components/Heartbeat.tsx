import type { HeartbeatPoint } from "../types";

export function Heartbeat({ points = [] }: { points?: HeartbeatPoint[] }) {
  return (
    <div className="heartbeat" aria-label="最近 7 天可用性">
      {points.map((point) => {
        const tone = point.availability == null
          ? "empty"
          : point.availability >= 0.99
            ? "up"
            : point.availability >= 0.5
              ? "warn"
              : "down";
        return (
          <i
            key={point.ts}
            className={`heartbeat-tick heartbeat-${tone}`}
            title={`${new Date(point.ts * 1000).toLocaleString("zh-CN")} · ${
              point.availability == null ? "无数据" : `${(point.availability * 100).toFixed(0)}%`
            }`}
          />
        );
      })}
    </div>
  );
}
