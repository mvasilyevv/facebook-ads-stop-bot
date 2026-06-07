# Remotion — программный видео-постпродакшн (Волна 2 контент-пайплайна)

Параметризованный React-шаблон креатива (hook / offer / cta / comments × форматы
9:16, 1:1, 16:9) для пакетной генерации текстовых вариантов на одном базовом ролике.
Решает боль «AI не держит текст в кадре»: текст рисуется детерминированно поверх
чистого видео из syntx.

## Установка
```bash
cd remotion && npm ci        # Node 22 (см. .nvmrc)
```

## Превью шаблона
```bash
npm run studio               # http://localhost:3000
```

## Рендер
```bash
# один ролик:
npm run render -- src/index.ts Creative9x16 out/test.mp4 --props=props.json

# пакет из реестра (через Python-мост, рекомендуемый путь):
make video-batch GEO=KE SLOT=CR2 BG=/path/to/clean.mp4
```

## Граница с `scripts/overlay_video.py` (ffmpeg) — дополняет, не заменяет
- **`overlay_video.py`** — дефолт для ПРОСТОГО текста на готовое видео (clean-video
  пайплайн): дёшево, попиксельно, правка без перегенерации.
- **Remotion (этот проект)** — для СЛОЖНОГО: анимированные капшены (pop-in/пульс),
  фейк-FB-пост с живыми комментариями (формула CR005), эмодзи/иконки, мульти-формат
  из одних данных.

См. `docs/playbooks/video-gen.md`.

## Связь с пайплайном
Выход `out/<code>_<format>.mp4` → `scripts/video_batch.py` прогоняет через
`core.creatives.video_uniquifier` (3 уникальные копии) в `~/Documents/FB_Agent_Creo/`.
`code` в имени файла = `sub3` (джойн-ключ трекера) — облегчает будущий матчинг
`Creative ↔ fb_ad_id` (см. `docs/roadmap/creative-analytics.md`).
