// Гуманизированные паузы и (далее в задачах 3-5) DOM-события.
export const IdleRange = {
  SHORT: [80, 250] as const,
  BETWEEN_STEPS: [600, 2500] as const,
  BETWEEN_SCENES: [3000, 8000] as const,
  TYPING: [40, 180] as const,
  TYPING_BURST_PAUSE: [200, 800] as const,
} as const;

export type IdleRangeKey = readonly [number, number];

function rand(min: number, max: number): number {
  return min + Math.random() * (max - min);
}

export function humanIdle(range: IdleRangeKey): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, rand(range[0], range[1])));
}
