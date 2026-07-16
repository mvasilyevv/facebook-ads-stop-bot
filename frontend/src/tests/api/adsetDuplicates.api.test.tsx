/** API paths and terminal-aware polling contract for adset duplication. */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mockApiGet = vi.fn();
const mockApiSend = vi.fn();

vi.mock("@/lib/api/client", () => ({
  apiGet: (...args: unknown[]) => mockApiGet(...args),
  apiSend: (...args: unknown[]) => mockApiSend(...args),
}));

import {
  adsetDuplicatePollInterval,
  type AdsetDuplicatePreviewIn,
  useAdsetDuplicateStatus,
  useStartAdsetDuplicate,
  usePreviewAdsetDuplicate,
} from "@/lib/api/adsetDuplicates";

function wrapper(queryClient: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
  };
}

describe("adset duplicate API", () => {
  let queryClient: QueryClient;

  beforeEach(() => {
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    mockApiGet.mockReset();
    mockApiSend.mockReset();
  });

  it("posts preview and draft to canonical endpoints", async () => {
    const previewBody: AdsetDuplicatePreviewIn = {
      source_ad_id: "ad-1",
      selected_ad_ids: ["ad-1"],
      campaign_count: 3,
      adsets_per_campaign: 2,
      budget_level: "ABO",
      daily_budget_cents: 10_000,
      start_date: "2026-07-16",
      campaign_name_base: "CR2",
      adset_name_base: "CR2 set",
      idempotency_token: "preview-1",
    };
    mockApiSend
      .mockResolvedValueOnce({ preview_token: "pv-1" })
      .mockResolvedValueOnce({ task_id: 77, status: "pending_confirmation", expires_at: "x" });

    const previewHook = renderHook(() => usePreviewAdsetDuplicate(), {
      wrapper: wrapper(queryClient),
    });
    await act(() => previewHook.result.current.mutateAsync(previewBody));
    expect(mockApiSend).toHaveBeenNthCalledWith(
      1,
      "POST",
      "/tools/adset-duplicates/preview",
      previewBody,
    );

    const draftHook = renderHook(() => useStartAdsetDuplicate(), {
      wrapper: wrapper(queryClient),
    });
    await act(() => draftHook.result.current.mutateAsync({ preview_token: "pv-1" }));
    expect(mockApiSend).toHaveBeenNthCalledWith(2, "POST", "/tools/adset-duplicates/launch", {
      preview_token: "pv-1",
    });
  });

  it("polls canonical task endpoint and stops interval on terminal statuses", async () => {
    mockApiGet.mockResolvedValue({
      task_id: 77,
      status: "awaiting_confirmation",
      progress: null,
      created_meta_ids: {},
      error: null,
    });
    const { result } = renderHook(() => useAdsetDuplicateStatus(77), {
      wrapper: wrapper(queryClient),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(mockApiGet).toHaveBeenCalledWith(
      "/tools/adset-duplicates/77",
      undefined,
      expect.anything(),
    );
    expect(adsetDuplicatePollInterval("awaiting_confirmation")).toBe(2_000);
    expect(adsetDuplicatePollInterval("running")).toBe(2_000);
    for (const terminal of ["succeeded", "failed", "cancelled", "expired"]) {
      expect(adsetDuplicatePollInterval(terminal)).toBe(false);
    }
  });
});
