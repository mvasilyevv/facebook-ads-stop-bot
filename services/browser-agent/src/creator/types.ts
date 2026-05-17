export type StepKind = 'unknown' | 'present' | 'absent' | 'matched' | 'missing';

export interface StepState {
  kind: StepKind;
  current?: unknown;
  meta?: Record<string, unknown>;
}

export interface RecordedEvent {
  type: 'click' | 'input' | 'change';
  selector: string;
  text: string;
  value: string | number | boolean | null;
  reactProps?: Record<string, unknown>;
}

export interface DomState {
  url: string;
  title: string;
}

export interface PlanContext {
  variables: Record<string, unknown>;
  emit(event: string, payload?: unknown): void;
}

export interface Step<I = unknown, O = unknown> {
  name: string;
  match?(ev: RecordedEvent, dom: DomState): boolean;
  detect(ctx: PlanContext): Promise<StepState> | StepState;
  isSatisfied(state: StepState, input: I): boolean;
  execute(state: StepState, input: I, ctx: PlanContext): Promise<O>;
}

export interface PlanStep<I = unknown> {
  step: string;
  input: I;
}

export interface Plan {
  schema_version: number;
  steps: PlanStep[];
}
