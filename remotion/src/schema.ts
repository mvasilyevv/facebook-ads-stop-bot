// Контракт пропсов креатива (zod) — единый для шаблона, Studio и пакетного рендера.
import {z} from 'zod';

export const creativeSchema = z.object({
  // code == sub3 в трекере (джойн-ключ). Попадает в имя выходного файла.
  code: z.string(),
  geo: z.string(),
  lang: z.string(),
  hook: z.object({text: z.string(), sub: z.string().optional()}),
  offer: z.object({amount: z.string(), currency: z.string(), bonus: z.string().optional()}),
  cta: z.object({text: z.string(), startSec: z.number()}),
  bg: z.object({
    type: z.enum(['solid', 'image', 'video']),
    src: z.string().optional(), // имя файла в public/ (для image/video)
    color: z.string().optional(),
  }),
  comments: z.array(z.object({author: z.string(), text: z.string()})).optional(),
});

export type CreativeProps = z.infer<typeof creativeSchema>;

export const FPS = 30;
export const DURATION_IN_FRAMES = 300; // 10 секунд
