import type { VisionProfile } from './types.js';
/** HTTP-клиент для локального антидетект-браузера Vision на localhost:3030. */
export declare class VisionClient {
    private readonly baseUrl;
    private readonly xToken;
    constructor(xToken: string, baseUrl?: string);
    private request;
    listProfiles(): Promise<VisionProfile[]>;
    getProfile(profileId: string): Promise<VisionProfile | null>;
    waitUntilProfileStopped(profileId: string, timeoutSec?: number, pollIntervalSec?: number): Promise<boolean>;
    waitUntilProfileHasPort(profileId: string, timeoutSec?: number, pollIntervalSec?: number): Promise<number | null>;
    waitUntilCdpReady(port: number, timeoutSec?: number, pollIntervalSec?: number): Promise<boolean>;
    resolveFolderId(profileId: string): Promise<string>;
    startProfile(folderId: string, profileId: string, options?: {
        portWaitTimeoutSec?: number;
    }): Promise<VisionProfile>;
    stopProfile(folderId: string, profileId: string): Promise<void>;
    restartProfileToRecoverPort(folderId: string, profileId: string, options?: {
        stopTimeoutSec?: number;
        portWaitTimeoutSec?: number;
        settleAfterStopMs?: number;
    }): Promise<VisionProfile>;
    cdpUrl(port: number): string;
}
//# sourceMappingURL=vision-client.d.ts.map