"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.BaseStep = void 0;
class BaseStep {
    async execute(state, input, ctx) {
        if (this.isSatisfied(state, input)) {
            ctx.emit('step_skipped', { step: this.name, reason: 'already_satisfied' });
            return undefined;
        }
        return await this.run(state, input, ctx);
    }
}
exports.BaseStep = BaseStep;
//# sourceMappingURL=base.js.map