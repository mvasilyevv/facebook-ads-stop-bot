// Хук 0-3с: крупный текст с pop-in сверху (привлечь внимание в ленте).
import type {FC} from 'react';
import {interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';

export const Hook: FC<{text: string; sub?: string}> = ({text, sub}) => {
  const frame = useCurrentFrame();
  const {fps, width} = useVideoConfig();
  const s = spring({frame, fps, config: {damping: 200}});
  const y = interpolate(s, [0, 1], [-40, 0]);
  return (
    <div style={{transform: `translateY(${y}px)`, opacity: s}}>
      <div
        style={{
          color: '#fff',
          fontSize: width * 0.072,
          fontWeight: 800,
          lineHeight: 1.05,
          textShadow: '0 2px 12px rgba(0,0,0,0.65)',
        }}
      >
        {text}
      </div>
      {sub ? (
        <div
          style={{
            color: '#10d36b',
            fontSize: width * 0.045,
            fontWeight: 700,
            marginTop: width * 0.02,
            textShadow: '0 2px 10px rgba(0,0,0,0.6)',
          }}
        >
          {sub}
        </div>
      ) : null}
    </div>
  );
};
