// Шаг: переименование ad set. Идемпотентен если уже есть to и нет from.
import { createRenameStep } from './_helpers/tree-actions.js';

export const RenameAdsetStep = createRenameStep('rename_adset', 'adset');
