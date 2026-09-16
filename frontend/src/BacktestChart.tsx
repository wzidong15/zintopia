import { useEffect, useRef } from "react";
import {
  ColorType,
  PriceScaleMode,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from "lightweight-charts";
import { formatChartTick, formatChartTime } from "./format";

export type BtLine = {
  id: string;
  label: string;
  values: number[];
  color: string;
  width?: 1 | 2 | 3;
  dashed?: boolean;
};

export default function BacktestChart({
  dates,
  lines,
  logScale,
}: {
  dates: number[];
  lines: BtLine[];
  logScale: boolean;
}) {
  const host = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const series = useRef<ISeriesApi<"Line">[]>([]);

  useEffect(() => {
    if (!host.current) return;
    const c = createChart(host.current, {
      layout: {
        background: { type: ColorType.Solid, color: "#161b22" },
        textColor: "#8b949e",
        fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
      },
      grid: {
        vertLines: { color: "#21262d" },
        horzLines: { color: "#21262d" },
      },
      rightPriceScale: { borderColor: "#30363d" },
      localization: { timeFormatter: formatChartTime },
      timeScale: {
        borderColor: "#30363d",
        timeVisible: false,
        tickMarkFormatter: formatChartTick,
      },
    });
    chart.current = c;
    const ro = new ResizeObserver(() => {
      if (!host.current) return;
      c.applyOptions({ width: host.current.clientWidth, height: host.current.clientHeight });
    });
    ro.observe(host.current);
    return () => {
      ro.disconnect();
      c.remove();
      chart.current = null;
      series.current = [];
    };
  }, []);

  useEffect(() => {
    chart.current?.priceScale("right").applyOptions({
      mode: logScale ? PriceScaleMode.Logarithmic : PriceScaleMode.Normal,
    });
  }, [logScale]);

  useEffect(() => {
    const c = chart.current;
    if (!c) return;
    for (const s of series.current) c.removeSeries(s);
    series.current = [];
    if (!dates.length) return;
    for (const line of lines) {
      const s = c.addLineSeries({
        color: line.color,
        lineWidth: line.width ?? 1,
        lineStyle: line.dashed ? 2 : 0,
        title: line.label,
        priceLineVisible: false,
        lastValueVisible: true,
      });
      const n = Math.min(dates.length, line.values.length);
      const data = [];
      for (let i = 0; i < n; i++) {
        const v = line.values[i];
        if (v == null || !Number.isFinite(v)) continue;
        data.push({ time: dates[i] as UTCTimestamp, value: v });
      }
      s.setData(data);
      series.current.push(s);
    }
    c.timeScale().fitContent();
  }, [dates, lines]);

  return <div className="chart mc-chart bt-chart" ref={host} />;
}
