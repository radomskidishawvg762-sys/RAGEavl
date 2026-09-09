/**
 * Thin ECharts wrapper. ECharts is imported lazily (dynamic import) so the heavy
 * charting library is code-split out of the main bundle and only loaded when a
 * chart actually mounts. Callers render an empty state instead of a chart when
 * there is no real data.
 */

import { useEffect, useRef } from 'react';
import type { EChartsCoreOption, ECharts } from 'echarts/core';

let echartsPromise: Promise<typeof import('echarts/core')> | null = null;

function loadECharts() {
  if (!echartsPromise) {
    echartsPromise = Promise.all([
      import('echarts/core'),
      import('echarts/charts'),
      import('echarts/components'),
      import('echarts/renderers'),
    ]).then(([core, charts, components, renderers]) => {
      core.use([
        charts.LineChart,
        components.TitleComponent,
        components.TooltipComponent,
        components.GridComponent,
        components.DatasetComponent,
        components.TransformComponent,
        renderers.CanvasRenderer,
      ]);
      return core;
    });
  }
  return echartsPromise;
}

export function EChart({
  option,
  height = 220,
  style,
}: {
    option: EChartsCoreOption;
  height?: number;
  style?: React.CSSProperties;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<ECharts | null>(null);

  useEffect(() => {
    let disposed = false;
    let onResize: (() => void) | null = null;
    (async () => {
      const echarts = await loadECharts();
      if (disposed || !ref.current) return;
      const chart = echarts.init(ref.current, undefined, { renderer: 'canvas' });
      chartRef.current = chart;
      chart.setOption(option, true);
      onResize = () => chart.resize();
      window.addEventListener('resize', onResize);
    })();
    return () => {
      disposed = true;
      if (onResize) window.removeEventListener('resize', onResize);
      chartRef.current?.dispose();
      chartRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    chartRef.current?.setOption(option, true);
  }, [option]);

  return <div ref={ref} style={{ height, width: '100%', ...style }} aria-label="chart" />;
}
