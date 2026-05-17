// Шаг: переименование объявления. Идемпотентен если уже есть to и нет from.
import { createRenameStep } from './_helpers/tree-actions.js';

export const RenameAdStep = createRenameStep('rename_ad', 'ad');
