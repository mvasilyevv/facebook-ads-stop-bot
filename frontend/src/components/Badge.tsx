type BadgeProps = {
  tone?: "neutral" | "good" | "warn" | "bad" | "info";
  children: string;
};

export function Badge({ tone = "neutral", children }: BadgeProps) {
  return <span className={`badge badge--${tone}`}>{children}</span>;
}
