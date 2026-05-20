import type { BrowserContext } from 'playwright';
export type CreatorEventListener = (event: string, payload: unknown) => void;
export declare function addCreatorEventListener(context: BrowserContext, listener: CreatorEventListener): () => void;
export declare function injectCreator(context: BrowserContext): Promise<void>;
//# sourceMappingURL=creator-injector.d.ts.map