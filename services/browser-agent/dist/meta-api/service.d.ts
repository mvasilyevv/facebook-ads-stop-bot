import { SessionManager } from '../session-manager.js';
export declare function createMetaApiServiceHandlers(sessionManager: SessionManager): {
    executeGraphCall: (call: any, callback: any) => Promise<void>;
    checkMetaApiHealth: (call: any, callback: any) => Promise<void>;
};
//# sourceMappingURL=service.d.ts.map