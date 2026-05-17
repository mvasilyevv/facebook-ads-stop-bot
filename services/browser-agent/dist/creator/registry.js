"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.registerStep = registerStep;
exports.getStep = getStep;
exports.listSteps = listSteps;
exports.clearRegistry = clearRegistry;
const _registry = new Map();
function registerStep(step) {
    if (_registry.has(step.name)) {
        throw new Error(`Step ${step.name} already registered`);
    }
    _registry.set(step.name, step);
}
function getStep(name) {
    return _registry.get(name);
}
function listSteps() {
    return Array.from(_registry.values());
}
function clearRegistry() {
    _registry.clear();
}
//# sourceMappingURL=registry.js.map