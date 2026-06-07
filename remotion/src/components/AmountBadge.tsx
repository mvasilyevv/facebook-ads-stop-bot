// Зелёная плашка зачисления (формула CR005: пруф денег на M-Pesa) + соц-пруф комментарии.
import type {FC} from 'react';
import {spring, useCurrentFrame, useVideoConfig} from 'remotion';

type Comment = {author: string; text: string};

export const AmountBadge: FC<{
  amount: string;
  currency: string;
  bonus?: string;
  comments?: Comment[];
}> = ({amount, currency, bonus, comments}) => {
  const frame = useCurrentFrame();
  const {fps, width} = useVideoConfig();
  const s = spring({frame: frame - 15, fps, config: {damping: 200}});

  return (
    <div style={{transform: `scale(${0.9 + 0.1 * s})`, opacity: s}}>
      <div
        style={{
          background: '#10d36b',
          color: '#04210f',
          borderRadius: width * 0.04,
          padding: `${width * 0.03}px ${width * 0.05}px`,
          display: 'inline-block',
          fontWeight: 900,
          fontSize: width * 0.07,
        }}
      >
        +{amount} {currency}
        <span style={{display: 'block', fontSize: width * 0.028, fontWeight: 700, opacity: 0.8}}>
          received · M-Pesa
        </span>
      </div>
      {bonus ? (
        <div style={{color: '#fff', fontSize: width * 0.038, marginTop: width * 0.025, fontWeight: 700}}>
          {bonus}
        </div>
      ) : null}
      {comments?.length ? (
        <div
          style={{
            marginTop: width * 0.04,
            display: 'flex',
            flexDirection: 'column',
            gap: width * 0.018,
          }}
        >
          {comments.map((c, i) => (
            <div
              key={i}
              style={{
                background: 'rgba(255,255,255,0.10)',
                borderRadius: width * 0.03,
                padding: width * 0.025,
                color: '#eee',
                fontSize: width * 0.03,
              }}
            >
              <b style={{color: '#fff'}}>{c.author}</b> {c.text}
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
};
