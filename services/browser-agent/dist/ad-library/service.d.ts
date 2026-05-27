import { SessionManager } from '../session-manager.js';
export declare function createAdLibraryServiceHandlers(sessionManager: SessionManager): {
    searchAds: (call: any, callback: any) => Promise<void>;
    searchAdsBatch: (call: any, callback: any) => Promise<void>;
    checkAdLibraryHealth: (call: any, callback: any) => Promise<void>;
};
//# sourceMappingURL=service.d.ts.map