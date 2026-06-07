// Корень: одна композиция CreativeVideo в трёх форматах (9:16, 1:1, 16:9).
import type {FC} from 'react';
import {Composition} from 'remotion';
import {CreativeVideo} from './CreativeVideo';
import {creativeSchema, DURATION_IN_FRAMES, FPS, type CreativeProps} from './schema';

const defaultProps: CreativeProps = {
  code: 'KE_CR2_DEMO',
  geo: 'KE',
  lang: 'en',
  hook: {text: 'I cashed out 4,200 KES', sub: 'straight to my M-Pesa 🇰🇪'},
  offer: {amount: '153', currency: 'KES'},
  cta: {text: 'PLAY NOW', startSec: 3},
  bg: {type: 'solid', color: '#0b0b0f'},
  comments: [
    {author: 'Brian K.', text: 'Withdrew to M-Pesa in 2 min 🔥'},
    {author: 'Aisha', text: 'Ni safi! Started with just 153 😅'},
  ],
};

const FORMATS = [
  {id: 'Creative9x16', width: 1080, height: 1920},
  {id: 'Creative1x1', width: 1080, height: 1080},
  {id: 'Creative16x9', width: 1920, height: 1080},
] as const;

export const RemotionRoot: FC = () => {
  return (
    <>
      {FORMATS.map((f) => (
        <Composition
          key={f.id}
          id={f.id}
          component={CreativeVideo}
          durationInFrames={DURATION_IN_FRAMES}
          fps={FPS}
          width={f.width}
          height={f.height}
          schema={creativeSchema}
          defaultProps={defaultProps}
        />
      ))}
    </>
  );
};
