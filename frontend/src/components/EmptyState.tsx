type EmptyStateProps = {
  title: string;
  description: string;
  action?: { label: string; href: string };
};

export function EmptyState({ title, description, action }: EmptyStateProps) {
  return (
    <div className="empty-state">
      <strong>{title}</strong>
      <span>{description}</span>
      {action && (
        <a href={action.href} className="button button--primary button--small" style={{ marginTop: "8px" }}>
          {action.label}
        </a>
      )}
    </div>
  );
}
