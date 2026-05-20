"use strict";
var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || (function () {
    var ownKeys = function(o) {
        ownKeys = Object.getOwnPropertyNames || function (o) {
            var ar = [];
            for (var k in o) if (Object.prototype.hasOwnProperty.call(o, k)) ar[ar.length] = k;
            return ar;
        };
        return ownKeys(o);
    };
    return function (mod) {
        if (mod && mod.__esModule) return mod;
        var result = {};
        if (mod != null) for (var k = ownKeys(mod), i = 0; i < k.length; i++) if (k[i] !== "default") __createBinding(result, mod, k[i]);
        __setModuleDefault(result, mod);
        return result;
    };
})();
Object.defineProperty(exports, "__esModule", { value: true });
exports.createCreatorServiceHandlers = createCreatorServiceHandlers;
// gRPC-обработчики CreatorService: RunPlan (стрим), StartRecording, StopRecording,
// GetRecorderStatus. Вся логика выполнения/записи живёт в browser-bundle
// (window.__fbAgent), а здесь — только мост между gRPC и page.evaluate().
const grpc = __importStar(require("@grpc/grpc-js"));
const session_manager_js_1 = require("./session-manager.js");
const creator_injector_js_1 = require("./creator-injector.js");
const CHECKPOINT_MARKERS = ['/checkpoint/', 'checkpoint?next=', 'security/checkpoint'];
function grpcCodeForError(err) {
    const message = String(err?.message || '').toLowerCase();
    return message.includes('not found') || message.includes('не найден')
        ? grpc.status.NOT_FOUND
        : grpc.status.INTERNAL;
}
function getPage(session) {
    const preferredPage = (0, session_manager_js_1.findPreferredPrimaryPage)(session.browser);
    if (preferredPage && preferredPage !== session.primaryPage) {
        session.primaryPage = preferredPage;
    }
    const page = session.primaryPage;
    const closed = typeof page?.isClosed === 'function' && page.isClosed();
    if (!page || closed) {
        throw new Error('Основная страница браузера недоступна');
    }
    return page;
}
function getContextFromPage(page) {
    // Playwright Page.context() есть всегда у реального CDP-подключения.
    return page.context();
}
function isCheckpointUrl(url) {
    const normalized = String(url || '').toLowerCase();
    return CHECKPOINT_MARKERS.some((marker) => normalized.includes(marker));
}
function safeParseJson(raw, fallback) {
    try {
        if (!raw)
            return fallback;
        return JSON.parse(raw);
    }
    catch {
        return fallback;
    }
}
function createCreatorServiceHandlers(sessionManager) {
    function resolveSession(sessionId) {
        const normalizedSessionId = String(sessionId || '').trim();
        return normalizedSessionId
            ? sessionManager.getSession(normalizedSessionId)
            : sessionManager.getPreferredSession();
    }
    async function prepareCreator(sessionId) {
        const session = resolveSession(sessionId);
        const page = getPage(session);
        const context = getContextFromPage(page);
        // Инъекция идемпотентна — повторный вызов на том же контексте — no-op.
        await (0, creator_injector_js_1.injectCreator)(context);
        return { session, page, context };
    }
    async function startRecording(call, callback) {
        try {
            const req = call.request;
            const { page } = await prepareCreator(req.session_id);
            const planName = String(req.plan_name || '').trim() || 'unnamed';
            await page.evaluate((name) => {
                const api = window.__fbAgent;
                if (!api || typeof api.startRecording !== 'function') {
                    throw new Error('__fbAgent.startRecording не доступен в странице');
                }
                return api.startRecording(name);
            }, planName);
            callback(null, { started: true, message: `Запись начата: ${planName}` });
        }
        catch (err) {
            callback({ code: grpcCodeForError(err), message: err.message || 'Не удалось начать запись' });
        }
    }
    async function stopRecording(call, callback) {
        try {
            const req = call.request;
            const { page } = await prepareCreator(req.session_id);
            const result = await page.evaluate(() => {
                const api = window.__fbAgent;
                if (!api || typeof api.stopRecording !== 'function') {
                    throw new Error('__fbAgent.stopRecording не доступен в странице');
                }
                return api.stopRecording();
            });
            const planJson = JSON.stringify({
                schema_version: 1,
                plan_name: result?.planName ?? '',
                steps: Array.isArray(result?.steps) ? result.steps : [],
            });
            const recordedSteps = Array.isArray(result?.steps) ? result.steps.length : 0;
            callback(null, { stopped: true, plan_json: planJson, recorded_steps: recordedSteps });
        }
        catch (err) {
            callback({ code: grpcCodeForError(err), message: err.message || 'Не удалось остановить запись' });
        }
    }
    async function getRecorderStatus(call, callback) {
        try {
            const req = call.request;
            const { page } = await prepareCreator(req.session_id);
            const status = await page.evaluate(() => {
                const api = window.__fbAgent;
                if (!api || typeof api.getRecorderStatus !== 'function') {
                    return { active: false, planName: '', recordedSteps: 0 };
                }
                return api.getRecorderStatus();
            });
            callback(null, {
                recording: Boolean(status?.active),
                plan_name: String(status?.planName || ''),
                recorded_steps: Number(status?.recordedSteps || 0),
            });
        }
        catch (err) {
            callback({ code: grpcCodeForError(err), message: err.message || 'Не удалось получить статус recorder' });
        }
    }
    async function runPlan(call) {
        let cancelled = false;
        let unsubscribe = null;
        let frameNavHandler = null;
        let attachedPage = null;
        const safeWrite = (event) => {
            if (cancelled || call.destroyed || call.writableEnded)
                return false;
            try {
                call.write(event);
                return true;
            }
            catch {
                return false;
            }
        };
        const endIfActive = () => {
            if (!call.destroyed && !call.writableEnded) {
                try {
                    call.end();
                }
                catch {
                    // Закрытие может уже произойти со стороны клиента — это не критично.
                }
            }
        };
        const cleanup = () => {
            if (unsubscribe) {
                try {
                    unsubscribe();
                }
                catch { /* noop */ }
                unsubscribe = null;
            }
            if (frameNavHandler && attachedPage) {
                try {
                    attachedPage.off('framenavigated', frameNavHandler);
                }
                catch { /* noop */ }
            }
            frameNavHandler = null;
            attachedPage = null;
        };
        call.on('cancelled', () => { cancelled = true; cleanup(); });
        call.on('close', () => { cancelled = true; cleanup(); });
        call.on('error', () => { cancelled = true; cleanup(); });
        const startedAt = Date.now();
        let totalSteps = 0;
        let stepIndex = -1;
        try {
            const req = call.request;
            const { page, context } = await prepareCreator(req.session_id);
            attachedPage = page;
            const plan = safeParseJson(String(req.plan_json || ''), null);
            const variables = safeParseJson(String(req.variables_json || ''), {});
            if (!plan || !Array.isArray(plan.steps)) {
                throw new Error('plan_json пустой или не содержит steps');
            }
            totalSteps = plan.steps.length;
            const listener = (event, payload) => {
                if (cancelled)
                    return;
                const p = (payload || {});
                const tsMs = Date.now();
                if (event === 'step_started') {
                    stepIndex += 1;
                    safeWrite({
                        started: {
                            step: String(p.step || ''),
                            index: stepIndex,
                            timestamp_ms: tsMs,
                        },
                    });
                }
                else if (event === 'step_finished') {
                    safeWrite({
                        finished: {
                            step: String(p.step || ''),
                            index: stepIndex,
                            timestamp_ms: tsMs,
                            detail_json: p.detail ? JSON.stringify(p.detail) : '',
                        },
                    });
                }
                else if (event === 'step_failed') {
                    safeWrite({
                        failed: {
                            step: String(p.step || ''),
                            index: stepIndex,
                            error: String(p.error || ''),
                            timestamp_ms: tsMs,
                        },
                    });
                }
                else if (event === 'step_skipped') {
                    safeWrite({
                        skipped: {
                            step: String(p.step || ''),
                            index: stepIndex,
                            reason: String(p.reason || ''),
                            timestamp_ms: tsMs,
                        },
                    });
                }
            };
            unsubscribe = (0, creator_injector_js_1.addCreatorEventListener)(context, listener);
            frameNavHandler = (frame) => {
                if (cancelled)
                    return;
                if (frame !== page.mainFrame())
                    return;
                const url = frame.url();
                if (isCheckpointUrl(url)) {
                    safeWrite({
                        checkpoint: { url, detail: 'FB checkpoint detected' },
                    });
                }
            };
            page.on('framenavigated', frameNavHandler);
            const result = await page.evaluate((args) => {
                const api = window.__fbAgent;
                if (!api || typeof api.run !== 'function') {
                    return { ok: false, error: '__fbAgent.run не доступен в странице' };
                }
                return api.run(args.plan, args.vars);
            }, { plan, vars: variables });
            safeWrite({
                complete: {
                    ok: Boolean(result?.ok),
                    error: String(result?.error || ''),
                    total_steps: totalSteps,
                    duration_ms: Date.now() - startedAt,
                },
            });
        }
        catch (err) {
            safeWrite({
                complete: {
                    ok: false,
                    error: err?.message || 'Ошибка выполнения плана',
                    total_steps: totalSteps,
                    duration_ms: Date.now() - startedAt,
                },
            });
        }
        finally {
            cleanup();
            endIfActive();
        }
    }
    return {
        runPlan,
        startRecording,
        stopRecording,
        getRecorderStatus,
    };
}
//# sourceMappingURL=creator-service.js.map