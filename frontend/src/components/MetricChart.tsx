import * as echarts from "echarts/core";
import { LineChart } from "echarts/charts";
import { GridComponent, LegendComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import { useEffect, useRef } from "react";

echarts.use([LineChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer]);

interface Point {
  ts: number;
  latency_seconds?: number | null;
  error_rate: number;
  active_tokens: number;
}

export function MetricChart({ points }: { points: Point[] }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current);
    chart.setOption({
      animation: false,
      grid: { left: 44, right: 44, top: 26, bottom: 32 },
      tooltip: { trigger: "axis" },
      legend: { data: ["采集延迟", "错误率"], top: 0, textStyle: { color: "#52616b" } },
      xAxis: { type: "time", axisLine: { lineStyle: { color: "#ccd4da" } }, axisLabel: { color: "#65737d" } },
      yAxis: [
        { type: "value", name: "秒", axisLabel: { color: "#65737d" }, splitLine: { lineStyle: { color: "#edf0f2" } } },
        { type: "value", name: "%", min: 0, max: 100, axisLabel: { formatter: "{value}%", color: "#65737d" }, splitLine: { show: false } }
      ],
      series: [
        { name: "采集延迟", type: "line", showSymbol: false, smooth: true, lineStyle: { color: "#246bce", width: 2 }, data: points.map((point) => [point.ts * 1000, point.latency_seconds || 0]) },
        { name: "错误率", type: "line", yAxisIndex: 1, showSymbol: false, smooth: true, lineStyle: { color: "#c94148", width: 2 }, data: points.map((point) => [point.ts * 1000, point.error_rate * 100]) }
      ]
    });
    const resize = () => chart.resize();
    window.addEventListener("resize", resize);
    return () => { window.removeEventListener("resize", resize); chart.dispose(); };
  }, [points]);
  return <div ref={ref} className="metric-chart" />;
}
