import { useEffect, useState } from "react";
import { Badge } from "./Badge";
import { formatDateTime } from "../lib/format";
import type { RuleItem } from "../types";

type RuleEditorProps = {
  rule: RuleItem;
  draft: {
    title: string;
    description: string;
    is_enabled: boolean;
    priority: number;
    cpa_multiplier: string;
  };
  onSave: (payload: Partial<RuleItem>) => Promise<void>;
};

export function RuleEditor({ rule, draft, onSave }: RuleEditorProps) {
  const [title, setTitle] = useState(draft.title);
  const [description, setDescription] = useState(draft.description);
  const [isEnabled, setIsEnabled] = useState(draft.is_enabled);
  const [priority, setPriority] = useState(String(draft.priority));
  const [cpaMultiplier, setCpaMultiplier] = useState(draft.cpa_multiplier);

  useEffect(() => {
    setTitle(draft.title);
    setDescription(draft.description);
    setIsEnabled(draft.is_enabled);
    setPriority(String(draft.priority));
    setCpaMultiplier(draft.cpa_multiplier);
  }, [draft.title, draft.description, draft.is_enabled, draft.priority, draft.cpa_multiplier]);

  return (
    <form
      className="rule-editor"
      onSubmit={async (event) => {
        event.preventDefault();
        await onSave({
          title,
          description,
          is_enabled: isEnabled,
          priority: Number(priority),
          cpa_multiplier: cpaMultiplier,
        });
      }}
    >
      <div className="rule-editor__head">
        <div>
          <strong>{rule.title}</strong>
          <div className="muted">
            {rule.code} · {formatDateTime(rule.updated_at)}
          </div>
        </div>
        <Badge tone={isEnabled ? "good" : "warn"}>{isEnabled ? "включено" : "выключено"}</Badge>
      </div>
      <div className="form-grid">
        <input className="input" value={title} onChange={(e) => setTitle(e.target.value)} />
        <input className="input" value={priority} onChange={(e) => setPriority(e.target.value)} placeholder="Приоритет" />
      </div>
      <div className="form-grid">
        <input className="input" value={description} onChange={(e) => setDescription(e.target.value)} placeholder="Описание" />
        <input className="input" value={cpaMultiplier} onChange={(e) => setCpaMultiplier(e.target.value)} placeholder="CPA множитель" />
      </div>
      <label className="checkbox">
        <input type="checkbox" checked={isEnabled} onChange={(e) => setIsEnabled(e.target.checked)} />
        <span>Правило активно</span>
      </label>
      <div className="row-actions">
        <button type="submit" className="button button--small button--primary">Сохранить правило</button>
      </div>
    </form>
  );
}
