import http from "node:http";

let webhookUrl = "";
const evidence = [];

const server = http.createServer((request, response) => {
  if (request.method === "GET" && request.url === "/evidence") {
    const body = JSON.stringify({ calls: evidence });
    response.writeHead(200, {
      "content-type": "application/json",
      "content-length": Buffer.byteLength(body),
    });
    response.end(body);
    return;
  }
  if (request.method === "POST" && request.url === "/evidence/reset") {
    evidence.length = 0;
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
    let result;
    if (method === "setWebhook") {
      webhookUrl = String(payload.url || "");
      result = true;
    } else if (method === "getWebhookInfo") {
      result = {
        url: webhookUrl,
        has_custom_certificate: false,
        pending_update_count: 0,
        max_connections: 40,
        allowed_updates: ["message", "edited_message", "callback_query"],
      };
    } else if (method === "sendMessage") {
      evidence.push({
        method,
        chat_id: String(payload.chat_id || ""),
        message_id: 42,
        lifecycle: /RehearsalNotificationLifecycle|Disposable release rehearsal alert/.test(
          String(payload.text || ""),
        ),
      });
      result = { message_id: 42 };
    } else if (method === "editMessageText") {
      evidence.push({
        method,
        chat_id: String(payload.chat_id || ""),
        message_id: Number(payload.message_id || 0),
        lifecycle: /RehearsalNotificationLifecycle|Disposable release rehearsal alert/.test(
          String(payload.text || ""),
        ),
      });
      result = true;
    } else if (method === "editMessageReplyMarkup" || method === "answerCallbackQuery") {
      result = true;
    } else {
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

server.listen(18080, "0.0.0.0");
