/**
 * BlueprintBg — фоновая «чертёжная» текстура под контентом Dashboard.
 *
 * Портировано из design_handoff/dashboard-shared.jsx (BlueprintBG). Чистый CSS:
 * слой точек (radial-gradient) + слой линий (96px-сетка), маскированный к верху.
 * Decorative-only (aria-hidden, pointer-events-none). Без «уголков» (ticks=false).
 */

interface BlueprintBgProps {
  /** Рисовать точечный слой. */
  dots?: boolean;
}

export function BlueprintBg({ dots = true }: BlueprintBgProps) {
  return (
    <div aria-hidden="true" className="pointer-events-none absolute inset-0 overflow-hidden">
      {dots && (
        <div
          className="absolute inset-0"
          style={{
            backgroundImage:
              "radial-gradient(circle, var(--bg-6) 0.75px, transparent 0.75px)",
            backgroundSize: "24px 24px",
            opacity: 0.4,
          }}
        />
      )}
      <div
        className="absolute inset-0"
        style={{
          backgroundImage:
            "linear-gradient(var(--bg-5) 1px, transparent 1px), linear-gradient(90deg, var(--bg-5) 1px, transparent 1px)",
          backgroundSize: "96px 96px",
          opacity: 0.5,
          maskImage:
            "radial-gradient(ellipse 80% 70% at 50% 0%, #000 40%, transparent 100%)",
          WebkitMaskImage:
            "radial-gradient(ellipse 80% 70% at 50% 0%, #000 40%, transparent 100%)",
        }}
      />
    </div>
  );
}
