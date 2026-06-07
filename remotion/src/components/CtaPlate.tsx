// CTA-плашка: появляется на startSec с пружиной + лёгкий пульс (момент призыва к действию).
import type {FC} from 'react';
import {interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';

export const CtaPlate: FC<{text: string; startSec: number}> = ({text, startSec}) => {
  const frame = useCurrentFrame();
  const {fps, width} = useVideoConfig();
  const start = Math.round(startSec * fps);
  if (frame < start) {
    return null;
  }
  const local = frame - start;
  const s = spring({frame: local, fps, config: {damping: 12, stiffness: 200}});
  const pulse = 1 + 0.04 * Math.sin(local / 5);
  return (
    <div
      style={{
        transform: `scale(${(0.8 + 0.2 * s) * pulse})`,
        opacity: interpolate(s, [0, 1], [0, 1]),
        alignSelf: 'center',
      }}
    >
      <div
        style={{
          background: '#ff2d55',
          color: '#fff',
          borderRadius: width * 0.5,
          padding: `${width * 0.035}px ${width * 0.09}px`,
          fontWeight: 900,
          fontSize: width * 0.06,
          boxShadow: '0 8px 30px rgba(255,45,85,0.5)',
        }}
      >
        {text}
      </div>
    </div>
  );
};
