type ToggleLikeHandle = {
    getAttribute(name: string): Promise<string | null>;
    $(selector: string): Promise<ToggleLikeHandle | null>;
};
export declare const TOGGLE_SELECTOR = "[role=\"switch\"]";
export declare function resolveToggleHandleFromCell<T extends ToggleLikeHandle>(cell: T | null): Promise<T | null>;
export {};
//# sourceMappingURL=toggle-utils.d.ts.map