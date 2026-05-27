import { SessionManager } from '../session-manager.js';
export declare function createMetaApiServiceHandlers(sessionManager: SessionManager): {
    executeGraphCall: (call: any, callback: any) => Promise<void>;
    checkMetaApiHealth: (call: any, callback: any) => Promise<void>;
    uploadImage: (call: any, callback: any) => Promise<void>;
    uploadVideo: (call: any, callback: any) => void;
};
//# sourceMappingURL=service.d.ts.map