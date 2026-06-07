// Главный параметризованный шаблон: фон (solid/image/video) + хук + M-Pesa-плашка + CTA.
import type {FC} from 'react';
import {AbsoluteFill, Img, OffthreadVideo, staticFile, useVideoConfig} from 'remotion';
import {AmountBadge} from './components/AmountBadge';
import {CtaPlate} from './components/CtaPlate';
import {Hook} from './components/Hook';
import type {CreativeProps} from './schema';

export const CreativeVideo: FC<CreativeProps> = ({hook, offer, cta, bg, comments}) => {
  const {width} = useVideoConfig();
  const pad = Math.round(width * 0.06);
  const cover = {position: 'absolute', width: '100%', height: '100%', objectFit: 'cover'} as const;

  return (
    <AbsoluteFill
      style={{
        backgroundColor: bg.color ?? '#0b0b0f',
        fontFamily: 'Arial, Helvetica, sans-serif',
      }}
    >
      {bg.type === 'video' && bg.src ? (
        <OffthreadVideo src={staticFile(bg.src)} muted style={cover} />
      ) : null}
      {bg.type === 'image' && bg.src ? <Img src={staticFile(bg.src)} style={cover} /> : null}

      <AbsoluteFill style={{padding: pad, justifyContent: 'space-between'}}>
        <Hook text={hook.text} sub={hook.sub} />
        <AmountBadge
          amount={offer.amount}
          currency={offer.currency}
          bonus={offer.bonus}
          comments={comments}
        />
        <CtaPlate text={cta.text} startSec={cta.startSec} />
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
