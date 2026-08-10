/**
 * Renderer-independent operator chart semantics shared by web and TMA.
 *
 * The server timestamp is authoritative.  A renderer may place the marker only
 * on a confirmed series timestamp at or before that boundary; client wall time
 * must never make stale data look current.
 */
export function serverSeriesMarker(
  points: readonly string[],
  generatedAt: string | null,
): string | null {
  const generatedTimestamp = generatedAt ? Date.parse(generatedAt) : Number.NaN;
  const valid = points
    .map((point) => ({ point, timestamp: Date.parse(point) }))
    .filter((item) => Number.isFinite(item.timestamp))
    .sort((left, right) => left.timestamp - right.timestamp);
  if (!valid.length || !Number.isFinite(generatedTimestamp)) return null;
  const atOrBefore = [...valid]
    .reverse()
    .find((item) => item.timestamp <= generatedTimestamp);
  return atOrBefore?.point ?? null;
}

/** Keep the current-time label inside either horizontal edge of the plot. */
export function currentMarkerLabelPosition(
  points: readonly string[],
  marker: string,
): "insideTopLeft" | "insideTopRight" {
  const markerTimestamp = Date.parse(marker);
  const timestamps = points
    .map((point) => Date.parse(point))
    .filter(Number.isFinite)
    .sort((left, right) => left - right);
  if (Number.isFinite(markerTimestamp) && timestamps.length >= 2) {
    const first = timestamps[0]!;
    const last = timestamps[timestamps.length - 1]!;
    return markerTimestamp >= first + (last - first) / 2
      ? "insideTopLeft"
      : "insideTopRight";
  }
  const markerIndex = points.indexOf(marker);
  if (markerIndex < 0 || points.length < 2) return "insideTopLeft";
  return markerIndex >= (points.length - 1) / 2
    ? "insideTopLeft"
    : "insideTopRight";
}
