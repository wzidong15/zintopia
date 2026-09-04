import { useEffect, useRef } from "react";
import { ColorType, createChart, type IChartApi, type ISeriesApi, type UTCTimestamp } from "lightweight-charts";
import { formatChartTick, formatChartTime } from "./format";

const COLORS: Record<string, string> = {
  "10": "#6e7681",
  "25": "#58a6ff",
  "50": "#56d364",
  "75": "#58a6ff",
  "90": "#6e7681",
};

export default function MonteCarloChart({
  yearsAxis,
  percentiles,
}: {
  yearsAxis: number[];
  percentiles: Record<string, number[]>;
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
        timeVisible: true,
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
    const c = chart.current;
    if (!c || !yearsAxis.length) return;
    for (const s of series.current) c.removeSeries(s);
    series.current = [];
    const t0 = Math.floor(Date.now() / 1000);
    const keys = Object.keys(percentiles).sort((a, b) => Number(a) - Number(b));
    for (const k of keys) {
      const vals = percentiles[k] || [];
      const line = c.addLineSeries({
        color: COLORS[k] || "#8b949e",
        lineWidth: k === "50" ? 2 : 1,
        title: `P${k}`,
        priceLineVisible: false,
      });
      line.setData(
        yearsAxis.slice(0, vals.length).map((y, i) => ({
          time: (t0 + y * 365.25 * 86400) as UTCTimestamp,
          value: vals[i],
        })),
      );
      series.current.push(line);
    }
    c.timeScale().fitContent();
  }, [yearsAxis, percentiles]);

  return <div className="chart mc-chart" ref={host} />;
}
