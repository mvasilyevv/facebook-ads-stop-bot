// Шаг: дублирование ad set. Идемпотентен если в дереве уже есть newName.
import { createDuplicateStep } from './_helpers/tree-actions.js';

export const DuplicateAdsetStep = createDuplicateStep('duplicate_adset', 'adset');
