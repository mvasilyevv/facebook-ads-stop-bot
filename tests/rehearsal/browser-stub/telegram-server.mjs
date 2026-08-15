import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const createTelegramStubContract = () => {
  let webhookUrl = "";
  const evidence = [];

  const dispatch = (method, payload = {}) => {
    if (method === "getMe") {
      return {
        id: 900000001,
        is_bot: true,
        first_name: "FB Agent Rehearsal",
        username: "fb_agent_rehearsal_bot",
        can_join_groups: true,
        can_read_all_group_messages: false,
        supports_inline_queries: false,
      };
    }
    if (method === "setWebhook") {
      webhookUrl = String(payload.url || "");
      return true;
    }
    if (method === "getWebhookInfo") {
      return {
        url: webhookUrl,
        has_custom_certificate: false,
        pending_update_count: 0,
        max_connections: 40,
        allowed_updates: ["message", "edited_message", "callback_query"],
      };
    }
    if (method === "sendMessage") {
      evidence.push({
        method,
        chat_id: String(payload.chat_id || ""),
        message_id: 42,
        lifecycle: /RehearsalNotificationLifecycle|Disposable release rehearsal alert/.test(
          String(payload.text || ""),
        ),
      });
      return { message_id: 42 };
    }
    if (method === "editMessageText") {
      evidence.push({
        method,
        chat_id: String(payload.chat_id || ""),
        message_id: Number(payload.message_id || 0),
        lifecycle: /RehearsalNotificationLifecycle|Disposable release rehearsal alert/.test(
          String(payload.text || ""),
        ),
      });
      return true;
    }
    if (method === "editMessageReplyMarkup" || method === "answerCallbackQuery") {
      return true;
    }
    return undefined;
  };

  return {
    dispatch,
    evidence: () => evidence.map((entry) => ({ ...entry })),
    reset: () => {
      evidence.length = 0;
    },
  };
};

export const createTelegramStubServer = () => {
  const contract = createTelegramStubContract();

  return http.createServer((request, response) => {
  if (request.method === "GET" && request.url === "/evidence") {
    const body = JSON.stringify({ calls: contract.evidence() });
    response.writeHead(200, {
      "content-type": "application/json",
      "content-length": Buffer.byteLength(body),
    });
    response.end(body);
    return;
  }
  if (request.method === "POST" && request.url === "/evidence/reset") {
    contract.reset();
    response.writeHead(204);
    response.end();
    return;
  }
  const chunks = [];
  request.on("data", (chunk) => chunks.push(chunk));
  request.on("end", () => {
    let payload = {};
    try {
      payload = JSON.parse(Buffer.concat(chunks).toString("utf8") || "{}");
    } catch {
      response.writeHead(400, { "content-type": "application/json" });
      response.end(JSON.stringify({ ok: false, error_code: 400, description: "invalid JSON" }));
      return;
    }
    const method = String(request.url || "").split("/").pop();
    const result = contract.dispatch(method, payload);
    if (result === undefined) {
      response.writeHead(404, { "content-type": "application/json" });
      response.end(JSON.stringify({ ok: false, error_code: 404, description: "unsupported" }));
      return;
    }
    const body = JSON.stringify({ ok: true, result });
    response.writeHead(200, {
      "content-type": "application/json",
      "content-length": Buffer.byteLength(body),
    });
    response.end(body);
  });
  });
};

const isEntrypoint =
  process.argv[1] &&
  fileURLToPath(import.meta.url) === path.resolve(process.argv[1]);
if (isEntrypoint) {
  createTelegramStubServer().listen(
    Number(process.env.TELEGRAM_STUB_PORT || "18080"),
    "0.0.0.0",
  );
}
