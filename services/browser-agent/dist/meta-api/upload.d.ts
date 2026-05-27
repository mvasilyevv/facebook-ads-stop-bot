import type { Page } from 'playwright';
export interface UploadImageResult {
    ok: boolean;
    imageHash: string;
    url: string;
    error: string;
    durationMs: number;
}
export interface UploadVideoInitParams {
    adAccountId: string;
    filename: string;
    fileSize: number;
}
export interface VideoStartPhase {
    uploadSessionId: string;
    videoId: string;
    startOffset: number;
    endOffset: number;
}
export interface VideoTransferPhase {
    startOffset: number;
    endOffset: number;
}
export interface UploadVideoResult {
    ok: boolean;
    videoId: string;
    error: string;
    durationMs: number;
    chunksProcessed: number;
}
/**
 * Загружает картинку в /act_X/adimages через multipart/form-data.
 * Возвращает image_hash для последующего использования в AdCreative.
 *
 * Meta endpoint: POST /v22.0/act_{id}/adimages
 *   body: FormData с полем `source` = File(bytes, filename, content_type)
 *   ответ: { images: { <filename>: { hash, url } } }
 */
export declare function uploadImage(page: Page, params: {
    adAccountId: string;
    filename: string;
    contentType: string;
    fileBytes: Uint8Array | Buffer;
    timeoutMs?: number;
}): Promise<UploadImageResult>;
/**
 * Видео-upload-сессия: держит state между client streaming chunks.
 *
 * Lifecycle:
 *   1. start(): POST upload_phase=start с file_size → возвращает upload_session_id + video_id
 *   2. transfer(bytes): POST upload_phase=transfer с file_chunk + start_offset
 *      → возвращает следующий start_offset (если ещё не всё)
 *   3. finish(): POST upload_phase=finish → возвращает {success: true, video_id}
 *
 * Все вызовы — через page.evaluate(fetch FormData), внутри одного browser-context.
 */
export declare class VideoUploadSession {
    private page;
    private adAccountId;
    private filename;
    private fileSize;
    private apiVersion;
    private timeoutMs;
    private uploadSessionId;
    private videoId;
    private currentOffset;
    private expectedNextOffset;
    private started;
    private finished;
    constructor(page: Page, params: UploadVideoInitParams & {
        timeoutMs?: number;
    });
    /** Фаза start: получить upload_session_id + video_id. */
    start(): Promise<VideoStartPhase>;
    /** Фаза transfer: загрузить чанк начиная с текущего offset. */
    transfer(chunkBytes: Uint8Array | Buffer): Promise<VideoTransferPhase>;
    /** Фаза finish: подтвердить завершение → Meta склеит чанки и вернёт video_id. */
    finish(): Promise<string>;
    get sessionId(): string;
    get id(): string;
    get isStarted(): boolean;
    get isFinished(): boolean;
    get nextOffset(): number;
}
//# sourceMappingURL=upload.d.ts.map