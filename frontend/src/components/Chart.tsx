/** A small bar chart in plain SVG: no chart library, nothing to load. The data is always also shown as a table
 *  next to it, so the chart is a summary for the eye and never the only way to read a number. */
export interface Series { name: string; color: string; values: number[] }

/** Bar heights as whole pixels. Pure, so the arithmetic is tested without rendering. */
export function barHeights(values: number[], max: number, height: number): number[] {
  if (max <= 0) return values.map(() => 0);
  return values.map((v) => Math.round((Math.max(v, 0) / max) * height));
}

export function BarChart({ title, labels, series, format }: { title: string; labels: string[]; series: Series[]; format: (n: number) => string }) {
  const W = 560, H = 180, pad = 28;
  const max = Math.max(0, ...series.flatMap((s) => s.values));
  const group = (W - pad) / Math.max(labels.length, 1);
  const bar = Math.min(28, (group - 8) / series.length);
  return (
    <figure className="chart">
      <svg viewBox={`0 0 ${W} ${H + pad}`} role="img" aria-label={`${title}. ${series.map((s) => `${s.name}: ${s.values.map(format).join(", ")}`).join(". ")}`}>
        <line x1={pad} x2={W} y1={H} y2={H} style={{ stroke: "var(--axis)" }} />
        {series.map((s, si) => barHeights(s.values, max, H - 20).map((h, i) => (
          <rect key={`${s.name}${i}`} x={pad + i * group + (group - bar * series.length) / 2 + si * bar} y={H - h} width={bar - 2} height={h} style={{ fill: s.color }}>
            <title>{`${labels[i]} ${s.name}: ${format(s.values[i] ?? 0)}`}</title>
          </rect>
        )))}
        {labels.map((l, i) => <text key={l} x={pad + i * group + group / 2} y={H + 16} textAnchor="middle" fontSize="11" style={{ fill: "var(--muted)" }}>{l}</text>)}
      </svg>
      <figcaption>{series.map((s) => <span key={s.name} className="legend"><i style={{ background: s.color }} />{s.name}</span>)}</figcaption>
    </figure>
  );
}
