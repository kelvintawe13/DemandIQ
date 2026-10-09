
interface SparklineProps {
  values: number[];
  isPositive: boolean;
}

export function Sparkline({ values, isPositive }: SparklineProps) {
  const max = Math.max(...values);
  const min = Math.min(...values);
  const range = max - min || 1;
  const width = 64;
  const height = 20;

  const points = values.
  map((value, index) => {
    const x = index / (values.length - 1) * width;
    const y = height - (value - min) / range * height;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).
  join(' ');

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      className="h-5 w-16"
      fill="none"
      aria-hidden="true">
      
      <polyline
        points={points}
        stroke={isPositive ? '#0f6d67' : '#dc2626'}
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round" />
      
    </svg>);

}