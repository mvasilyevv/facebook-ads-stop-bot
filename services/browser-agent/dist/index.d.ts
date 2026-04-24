import type { BrowserSession } from './types.js';
type SessionStatusLookup = (sessionId: string) => BrowserSession;
export declare function writeSessionStatusEvent(call: any, sessionId: string, lookup: SessionStatusLookup): boolean;
export declare function streamSessionStatusWithLookup(call: any, lookup: SessionStatusLookup): void;
export {};
//# sourceMappingURL=index.d.ts.map