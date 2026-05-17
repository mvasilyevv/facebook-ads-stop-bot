// Шаг: дублирование объявления. Идемпотентен если в дереве уже есть newName.
import { createDuplicateStep } from './_helpers/tree-actions.js';

export const DuplicateAdStep = createDuplicateStep('duplicate_ad', 'ad');
