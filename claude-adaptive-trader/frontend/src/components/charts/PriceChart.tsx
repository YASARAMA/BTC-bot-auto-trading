// Interactive candlestick chart (TradingView Lightweight Charts v5).
//
// Candles, volume and every indicator come from the backend, which computes them with the same
// tested functions the strategies use. A candle that is still forming is drawn but labelled as
// such; signal and trade markers are placed on the candles where they were actually recorded.
import { useEffect, useMemo, useRef, useState } from "react";
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  LineStyle,
  createChart,
  createSeriesMarkers,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type MouseEventParams,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import type { Candle, ChartPayload, Point } from "../../lib/types";
import { fmtCompact, fmtDateTime, fmtNum, fmtPrice, fmtTime, timeZonePref } from "../../lib/format";

export type ChartToggles = {
  volume: boolean;
  ema20: boolean;
  ema50: boolean;
  ema200: boolean;
  bb: boolean;
  rsi: boolean;
  atr: boolean;
  markers: boolean;
  levels: boolean;
};

export const DEFAULT_TOGGLES: ChartToggles = {
  volume: true, ema20: true, ema50: true, ema200: true, bb: false, rsi: true, atr: true, markers: true, levels: true,
};

export type ChartMarker = {
  time: number;
  position: "aboveBar" | "belowBar" | "inBar";
  shape: "arrowUp" | "arrowDown" | "circle" | "square";
  color: string;
  text?: string;
};

export type ChartLevel = { price: number; label: string; kind: "stop" | "target" | "entry" };

const C = {
  up: "#2ebd85",
  down: "#f6465d",
  upVol: "rgba(46,189,133,0.35)",
  downVol: "rgba(246,70,93,0.35)",
  ema20: "#f0b90b",
  ema50: "#4ea8ff",
  ema200: "#c084fc",
  bb: "rgba(143,153,171,0.55)",
  rsi: "#7c8cff",
  atr: "#2dd4bf",
  grid: "rgba(42,52,70,0.35)",
  border: "#1d2533",
  text: "#8f99ab",
};

type Line = ISeriesApi<"Line">;
type Api = {
  chart: IChartApi;
  candles: ISeriesApi<"Candlestick">;
  volume: ISeriesApi<"Histogram">;
  lines: Record<"ema20" | "ema50" | "ema200" | "bb_upper" | "bb_mid" | "bb_lower", Line>;
  rsi?: Line;
  atr?: Line;
  markers: ISeriesMarkersPluginApi<Time>;
  priceLines: IPriceLine[];
  lastTime: number;
  viewKey: string;
};

type Legend = {
  time: number;
  candle?: { open: number; high: number; low: number; close: number };
  volume?: number;
  values: Partial<Record<"ema20" | "ema50" | "ema200" | "rsi14" | "atr14", number>>;
  forming?: boolean;
};

const toLine = (points: Point[]) =>
  points.map((p) => (p.value === null ? { time: p.time as UTCTimestamp } : { time: p.time as UTCTimestamp, value: p.value }));

function tickFormatter(time: Time, tickType: number): string {
  const ms = Number(time) * 1000;
  const d = new Date(ms);
  const utc = timeZonePref() === "utc";
  // 0 year, 1 month, 2 day of month, 3 time, 4 time with seconds
  if (tickType === 0) return String(utc ? d.getUTCFullYear() : d.getFullYear());
  if (tickType === 1) return d.toLocaleString("en-GB", { month: "short", timeZone: utc ? "UTC" : undefined });
  if (tickType === 2) return d.toLocaleString("en-GB", { day: "2-digit", month: "short", timeZone: utc ? "UTC" : undefined });
  return fmtTime(ms, tickType === 4).replace(" UTC", "");
}

export function PriceChart({ payload, toggles, markers, levels, liveCandle, height = 560 }: {
  payload: ChartPayload;
  toggles: ChartToggles;
  markers?: ChartMarker[];
  levels?: ChartLevel[];
  liveCandle?: Candle | null;
  height?: number;
}) {
  const container = useRef<HTMLDivElement>(null);
  const api = useRef<Api | null>(null);
  const [legend, setLegend] = useState<Legend | null>(null);
  const payloadRef = useRef(payload);
  payloadRef.current = payload;

  // Create the chart once.
  useEffect(() => {
    const el = container.current;
    if (!el) return;
    const chart = createChart(el, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: C.text,
        fontFamily: "Inter, ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif",
        fontSize: 11,
        panes: { separatorColor: C.border, separatorHoverColor: "#2a3446", enableResize: true },
      },
      grid: { vertLines: { color: C.grid }, horzLines: { color: C.grid } },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: "#5d687a", width: 1, style: LineStyle.Dashed, labelBackgroundColor: "#182030" },
        horzLine: { color: "#5d687a", width: 1, style: LineStyle.Dashed, labelBackgroundColor: "#182030" },
      },
      rightPriceScale: { borderColor: C.border },
      timeScale: { borderColor: C.border, timeVisible: true, secondsVisible: false, rightOffset: 8, tickMarkFormatter: tickFormatter },
      localization: {
        timeFormatter: (t: Time) => fmtDateTime(Number(t) * 1000),
        priceFormatter: (p: number) => fmtPrice(p),
      },
    });
    const candles = chart.addSeries(CandlestickSeries, {
      upColor: C.up,
      downColor: C.down,
      borderVisible: false,
      wickUpColor: C.up,
      wickDownColor: C.down,
      priceLineVisible: true,
      priceLineStyle: LineStyle.Dotted,
    });
    candles.priceScale().applyOptions({ scaleMargins: { top: 0.08, bottom: 0.22 } });
    const volume = chart.addSeries(HistogramSeries, {
      priceFormat: { type: "volume" },
      priceScaleId: "vol",
      lastValueVisible: false,
      priceLineVisible: false,
    });
    chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    const line = (color: string, width: 1 | 2 = 1, style: LineStyle = LineStyle.Solid) =>
      chart.addSeries(LineSeries, {
        color,
        lineWidth: width,
        lineStyle: style,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      });
    const lines = {
      ema20: line(C.ema20),
      ema50: line(C.ema50),
      ema200: line(C.ema200, 2),
      bb_upper: line(C.bb, 1, LineStyle.Dashed),
      bb_mid: line(C.bb, 1, LineStyle.Dotted),
      bb_lower: line(C.bb, 1, LineStyle.Dashed),
    };
    const markersApi = createSeriesMarkers(candles, []);
    api.current = { chart, candles, volume, lines, markers: markersApi, priceLines: [], lastTime: 0, viewKey: "" };

    const onMove = (param: MouseEventParams<Time>) => {
      const a = api.current;
      if (!a) return;
      if (param.time === undefined) {
        setLegend(null);
        return;
      }
      const bar = param.seriesData.get(a.candles) as { open: number; high: number; low: number; close: number } | undefined;
      const vol = param.seriesData.get(a.volume) as { value: number } | undefined;
      const value = (s?: Line) => (s ? (param.seriesData.get(s) as { value?: number } | undefined)?.value : undefined);
      setLegend({
        time: Number(param.time),
        candle: bar && "open" in bar ? bar : undefined,
        volume: vol?.value,
        values: { ema20: value(a.lines.ema20), ema50: value(a.lines.ema50), ema200: value(a.lines.ema200), rsi14: value(a.rsi), atr14: value(a.atr) },
        forming: payloadRef.current.forming?.time === Number(param.time),
      });
    };
    chart.subscribeCrosshairMove(onMove);
    return () => {
      chart.unsubscribeCrosshairMove(onMove);
      chart.remove();
      api.current = null;
    };
  }, []);

  // RSI and ATR live in their own panes, rebuilt in a fixed order when toggled.
  useEffect(() => {
    const a = api.current;
    if (!a) return;
    if (a.rsi) a.chart.removeSeries(a.rsi);
    if (a.atr) a.chart.removeSeries(a.atr);
    a.rsi = undefined;
    a.atr = undefined;
    let pane = 1;
    if (toggles.rsi) {
      a.rsi = a.chart.addSeries(LineSeries, { color: C.rsi, lineWidth: 1, priceLineVisible: false, lastValueVisible: true, title: "RSI 14" }, pane);
      a.rsi.createPriceLine({ price: 70, color: "rgba(246,70,93,0.5)", lineStyle: LineStyle.Dashed, lineWidth: 1, axisLabelVisible: false, title: "" });
      a.rsi.createPriceLine({ price: 30, color: "rgba(46,189,133,0.5)", lineStyle: LineStyle.Dashed, lineWidth: 1, axisLabelVisible: false, title: "" });
      a.rsi.setData(toLine(payloadRef.current.indicators.rsi14));
      pane += 1;
    }
    if (toggles.atr) {
      a.atr = a.chart.addSeries(LineSeries, { color: C.atr, lineWidth: 1, priceLineVisible: false, lastValueVisible: true, title: "ATR 14" }, pane);
      a.atr.setData(toLine(payloadRef.current.indicators.atr14));
    }
    // Main pane gets the most room; each indicator pane a fixed share.
    const panes = a.chart.panes();
    panes[0]?.setStretchFactor(panes.length > 2 ? 0.64 : panes.length > 1 ? 0.78 : 1);
    for (let i = 1; i < panes.length; i++) panes[i].setStretchFactor(panes.length > 2 ? 0.18 : 0.22);
  }, [toggles.rsi, toggles.atr, height]);

  // Data.
  useEffect(() => {
    const a = api.current;
    if (!a) return;
    const candleData = payload.candles.map((c) => ({ time: c.time as UTCTimestamp, open: c.open, high: c.high, low: c.low, close: c.close }));
    a.candles.setData(candleData);
    a.volume.setData(
      payload.candles.map((c) => ({ time: c.time as UTCTimestamp, value: c.volume, color: c.close >= c.open ? C.upVol : C.downVol })),
    );
    a.lines.ema20.setData(toLine(payload.indicators.ema20));
    a.lines.ema50.setData(toLine(payload.indicators.ema50));
    a.lines.ema200.setData(toLine(payload.indicators.ema200));
    a.lines.bb_upper.setData(toLine(payload.indicators.bb_upper));
    a.lines.bb_mid.setData(toLine(payload.indicators.bb_mid));
    a.lines.bb_lower.setData(toLine(payload.indicators.bb_lower));
    a.rsi?.setData(toLine(payload.indicators.rsi14));
    a.atr?.setData(toLine(payload.indicators.atr14));
    a.lastTime = payload.candles.length ? payload.candles[payload.candles.length - 1].time : 0;
    if (payload.forming && payload.forming.time > a.lastTime) {
      const f = payload.forming;
      a.candles.update({ time: f.time as UTCTimestamp, open: f.open, high: f.high, low: f.low, close: f.close });
      a.volume.update({ time: f.time as UTCTimestamp, value: f.volume, color: f.close >= f.open ? C.upVol : C.downVol });
      a.lastTime = f.time;
    }
    const key = `${payload.exchange}|${payload.pair}|${payload.timeframe}`;
    if (a.viewKey !== key) {
      a.viewKey = key;
      const n = candleData.length;
      a.chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, n - 160), to: n + 6 });
    }
  }, [payload]);

  // Visibility toggles.
  useEffect(() => {
    const a = api.current;
    if (!a) return;
    a.volume.applyOptions({ visible: toggles.volume });
    a.lines.ema20.applyOptions({ visible: toggles.ema20 });
    a.lines.ema50.applyOptions({ visible: toggles.ema50 });
    a.lines.ema200.applyOptions({ visible: toggles.ema200 });
    for (const k of ["bb_upper", "bb_mid", "bb_lower"] as const) a.lines[k].applyOptions({ visible: toggles.bb });
  }, [toggles.volume, toggles.ema20, toggles.ema50, toggles.ema200, toggles.bb]);

  // Live candle updates from the WebSocket.
  useEffect(() => {
    const a = api.current;
    if (!a || !liveCandle || liveCandle.time < a.lastTime) return;
    a.candles.update({ time: liveCandle.time as UTCTimestamp, open: liveCandle.open, high: liveCandle.high, low: liveCandle.low, close: liveCandle.close });
    a.volume.update({ time: liveCandle.time as UTCTimestamp, value: liveCandle.volume, color: liveCandle.close >= liveCandle.open ? C.upVol : C.downVol });
    a.lastTime = liveCandle.time;
  }, [liveCandle]);

  // Trade and signal markers.
  useEffect(() => {
    const a = api.current;
    if (!a) return;
    const list: SeriesMarker<Time>[] = toggles.markers
      ? [...(markers ?? [])]
          .sort((x, y) => x.time - y.time)
          .map((m) => ({ time: m.time as UTCTimestamp, position: m.position, shape: m.shape, color: m.color, text: m.text }))
      : [];
    a.markers.setMarkers(list);
  }, [markers, toggles.markers]);

  // Stop-loss / take-profit / entry lines for open positions.
  useEffect(() => {
    const a = api.current;
    if (!a) return;
    a.priceLines.forEach((l) => a.candles.removePriceLine(l));
    a.priceLines = [];
    if (!toggles.levels) return;
    for (const lv of levels ?? []) {
      const color = lv.kind === "stop" ? C.down : lv.kind === "target" ? C.up : "#8f99ab";
      a.priceLines.push(
        a.candles.createPriceLine({ price: lv.price, color, lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: lv.label }),
      );
    }
  }, [levels, toggles.levels]);

  const shown = useMemo(() => {
    if (legend) return legend;
    const last = payload.forming ?? payload.candles[payload.candles.length - 1];
    if (!last) return null;
    const pick = (pts: Point[]) => {
      for (let i = pts.length - 1; i >= 0; i--) if (pts[i].value !== null) return pts[i].value ?? undefined;
      return undefined;
    };
    return {
      time: last.time,
      candle: last,
      volume: last.volume,
      values: { ema20: pick(payload.indicators.ema20), ema50: pick(payload.indicators.ema50), ema200: pick(payload.indicators.ema200), rsi14: pick(payload.indicators.rsi14), atr14: pick(payload.indicators.atr14) },
      forming: !!payload.forming,
    } as Legend;
  }, [legend, payload]);

  return (
    <div className="relative" style={{ height }}>
      <div ref={container} className="absolute inset-0" data-testid="price-chart" />
      {shown && (
        <div className="pointer-events-none absolute left-2 top-1 z-10 flex max-w-[calc(100%-5rem)] flex-wrap items-center gap-x-3 gap-y-0.5 rounded-md bg-bg/70 px-2 py-1 text-[11px] backdrop-blur-sm">
          <span className="text-muted">{fmtDateTime(shown.time * 1000)}</span>
          {shown.forming && <span className="rounded bg-warn/15 px-1 text-warn">forming</span>}
          {shown.candle && (
            <span className="num">
              <span className="text-faint">O</span> {fmtPrice(shown.candle.open)} <span className="text-faint">H</span> {fmtPrice(shown.candle.high)}{" "}
              <span className="text-faint">L</span> {fmtPrice(shown.candle.low)} <span className="text-faint">C</span>{" "}
              <span className={shown.candle.close >= shown.candle.open ? "text-up" : "text-down"}>{fmtPrice(shown.candle.close)}</span>
            </span>
          )}
          {toggles.volume && shown.volume !== undefined && <span className="num text-muted">Vol {fmtCompact(shown.volume)}</span>}
          {toggles.ema20 && <span className="num" style={{ color: C.ema20 }}>EMA20 {fmtPrice(shown.values.ema20)}</span>}
          {toggles.ema50 && <span className="num" style={{ color: C.ema50 }}>EMA50 {fmtPrice(shown.values.ema50)}</span>}
          {toggles.ema200 && <span className="num" style={{ color: C.ema200 }}>EMA200 {fmtPrice(shown.values.ema200)}</span>}
          {toggles.rsi && <span className="num" style={{ color: C.rsi }}>RSI {fmtNum(shown.values.rsi14, 1)}</span>}
          {toggles.atr && <span className="num" style={{ color: C.atr }}>ATR {fmtPrice(shown.values.atr14)}</span>}
        </div>
      )}
    </div>
  );
}
