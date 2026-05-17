"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.interpolate = interpolate;
exports.runPlan = runPlan;
// runPlan: последовательно выполняет шаги плана с подстановкой переменных
// шаблонами {{var}} / {{obj.path}}, эмитит step_started/finished/failed/skipped,
// между шагами добавляет гуманизированную паузу.
const registry_js_1 = require("./registry.js");
const humanizer_js_1 = require("./humanizer.js");
const TEMPLATE_RE = /\{\{\s*([\w.]+)\s*\}\}/g;
function resolvePath(obj, path) {
    return path.split('.').reduce((acc, key) => {
        if (acc && typeof acc === 'object' && key in acc) {
            return acc[key];
        }
        return undefined;
    }, obj);
}
function interpolate(input, vars) {
    if (typeof input === 'string') {
        return input.replace(TEMPLATE_RE, (_, p) => {
            const v = resolvePath(vars, p);
            return v == null ? '' : String(v);
        });
    }
    if (Array.isArray(input)) {
        return input.map((x) => interpolate(x, vars));
    }
    if (input && typeof input === 'object') {
        const out = {};
        for (const [k, v] of Object.entries(input)) {
            out[k] = interpolate(v, vars);
        }
        return out;
    }
    return input;
}
async function runPlan(plan, variables, emit) {
    const ctx = { variables, emit };
    for (const step of plan.steps) {
        const impl = (0, registry_js_1.getStep)(step.step);
        if (!impl) {
            emit('step_failed', { step: step.step, error: 'unknown step' });
            return { ok: false, error: `unknown step: ${step.step}` };
        }
        const input = interpolate(step.input, variables);
        emit('step_started', { step: step.step });
        try {
            const state = await impl.detect(ctx);
            await impl.execute(state, input, ctx);
            emit('step_finished', { step: step.step });
        }
        catch (e) {
            const msg = String(e?.message ?? e);
            emit('step_failed', { step: step.step, error: msg });
            return { ok: false, error: msg };
        }
        await (0, humanizer_js_1.humanIdle)(humanizer_js_1.IdleRange.BETWEEN_STEPS);
    }
    return { ok: true };
}
//# sourceMappingURL=executor.js.map