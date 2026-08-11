import * as grpc from "@grpc/grpc-js";
import * as protoLoader from "@grpc/proto-loader";
import path from "node:path";

const options = {
  keepCase: true,
  longs: String,
  enums: String,
  defaults: true,
  oneofs: true,
};
const load = (name) => grpc.loadPackageDefinition(
  protoLoader.loadSync(path.join("/proto/v1", name), options),
);
const browser = load("browser_session.proto").fb_agent.browser_session.v1;
const scanner = load("scanner.proto").fb_agent.scanner.v1;
const meta = load("meta_api.proto").fb_agent.meta_api.v1;

let profileId = "rehearsal-profile";
const sessionId = "rehearsal-session";
const started = (call, callback) => {
  profileId = String(call.request.vision_profile_id || profileId);
  callback(null, {
    session_id: sessionId,
    profile: { folder_id: "rehearsal", profile_id: profileId, cdp_port: 9222 },
    initial_page_url: "https://adsmanager.facebook.com/adsmanager/manage/campaigns",
  });
};

const server = new grpc.Server();
server.addService(browser.BrowserSessionService.service, {
  startBrowser: started,
  reconnectBrowser: started,
  recoverBrowserProfileUnderMaintenance: started,
  openCabinetTabs: (call, callback) => callback(null, {
    results: (call.request.ad_account_ids || []).map((account) => ({
      ad_account_id: String(account),
      opened: true,
      url: `https://adsmanager.facebook.com/adsmanager/manage/campaigns?act=${account}`,
      error: "",
    })),
  }),
});
server.addService(scanner.ScannerService.service, {
  runScanCycle: (call) => {
    call.write({
      session_id: sessionId,
      complete: {
        all_rows: [],
        total_passes: 1,
        duration_seconds: 0.01,
        dismissed_modals: [],
        unknown_modal_artifacts: [],
        partial_row_ids: [],
        warnings: [],
        empty_reason: "no_active_ads",
        rows_with_all_metrics_empty: 0,
        metrics_contract_revision: 1,
      },
    });
    call.end();
  },
  listCampaigns: (_call, callback) => callback(null, { campaigns: [] }),
});
server.addService(meta.MetaApiService.service, {
  checkMetaApiHealth: (_call, callback) => callback(null, {
    healthy: true,
    current_url: "https://adsmanager.facebook.com/adsmanager/manage/campaigns",
    token_present: true,
    token_length: 128,
    detail: "rehearsal_stub",
    probe_performed: true,
    probe_ok: true,
    probe_status_code: 200,
    probe_duration_ms: 1,
    probe_detail: "ok",
    browser_contract_version: 5,
    session_id: sessionId,
    vision_profile_id: profileId,
  }),
  executeGraphCallV5: (call, callback) => {
    const endpoint = String(call.request.endpoint || "");
    const accountMatch = endpoint.match(/^\/act_(\d+)$/);
    const response = accountMatch
      ? { id: `act_${accountMatch[1]}`, timezone_name: "Europe/Kaliningrad", currency: "USD" }
      : { data: [] };
    callback(null, {
      status_code: 200,
      response_json: JSON.stringify(response),
      duration_ms: 1,
    });
  },
  uploadImage: (_call, callback) => callback(null, { image_hash: "", ok: false }),
  uploadVideo: (call, callback) => {
    call.on("data", () => {});
    call.on("end", () => callback(null, { video_id: "", ok: false }));
  },
});

server.bindAsync(
  `0.0.0.0:${process.env.GRPC_PORT || "50051"}`,
  grpc.ServerCredentials.createInsecure(),
  (error) => {
    if (error) throw error;
  },
);
