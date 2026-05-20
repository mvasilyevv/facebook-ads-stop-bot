import { SessionManager } from './session-manager.js';
export declare function createCreatorServiceHandlers(sessionManager: SessionManager): {
    runPlan: (call: any) => Promise<void>;
    startRecording: (call: any, callback: any) => Promise<void>;
    stopRecording: (call: any, callback: any) => Promise<void>;
    getRecorderStatus: (call: any, callback: any) => Promise<void>;
};
//# sourceMappingURL=creator-service.d.ts.map