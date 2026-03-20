import type { ReactNode } from "react";

type SectionCardProps = {
  title: string;
  subtitle?: string;
  actions?: ReactNode;
  children: ReactNode;
};

export function SectionCard({ title, subtitle, actions, children }: SectionCardProps) {
  return (
    <section className="section-card">
      <div className="section-card__head">
        <div>
          <h2>{title}</h2>
          {subtitle ? <p>{subtitle}</p> : null}
        </div>
        {actions ? <div className="section-card__actions">{actions}</div> : null}
      </div>
      {children}
    </section>
  );
}
