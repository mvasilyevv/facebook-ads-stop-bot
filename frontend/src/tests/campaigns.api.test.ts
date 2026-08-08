import { afterEach, describe, expect, it, vi } from "vitest";
import { uploadConcepts } from "@/lib/api/campaigns";

describe("campaign upload generated transport", () => {
  afterEach(() => vi.restoreAllMocks());

  it("uses multipart only for files and keeps the generated UploadConceptsOut result", async () => {
    const append = vi.spyOn(FormData.prototype, "append");
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({ upload_id: "up-1", upload_dir: "/tmp/up-1", concepts: [], added_refs: [], total_bytes: 0 }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(uploadConcepts([new File(["x"], "creative.png", { type: "image/png" })], "up-1"))
      .resolves.toMatchObject({ upload_id: "up-1", total_bytes: 0 });

    const [request] = fetchMock.mock.calls[0] as [Request];
    expect(request).toBeInstanceOf(Request);
    expect(new URL(request.url).pathname).toBe("/api/tools/campaigns/upload");
    expect(request.headers.get("content-type")).toMatch(/^multipart\/form-data; boundary=/);
    expect(append).toHaveBeenCalledWith("upload_id", "up-1");
    expect(append).toHaveBeenCalledWith("files", expect.any(File));
  });
});
