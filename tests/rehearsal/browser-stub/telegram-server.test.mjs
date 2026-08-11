import assert from "node:assert/strict";
import { once } from "node:events";
import net from "node:net";
import path from "node:path";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import test from "node:test";

const script = path.join(
  path.dirname(fileURLToPath(import.meta.url)),
  "telegram-server.mjs",
);

const reservePort = async () => {
  const socket = net.createServer();
  socket.listen(0, "127.0.0.1");
  await once(socket, "listening");
  const address = socket.address();
  assert.equal(typeof address, "object");
  const port = address.port;
  await new Promise((resolve, reject) =>
    socket.close((error) => (error ? reject(error) : resolve())),
  );
  return port;
};

const post = async (origin, method, payload = {}) => {
  const response = await fetch(`${origin}/botrehearsal-token/${method}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
  const body = await response.json();
  assert.equal(response.status, 200);
  assert.equal(body.ok, true);
  return body.result;
};

test("Telegram rehearsal stub implements the deployed gateway contract", async (context) => {
  const port = await reservePort();
  const origin = `http://127.0.0.1:${port}`;
  const child = spawn(process.execPath, [script], {
    env: { ...process.env, TELEGRAM_STUB_PORT: String(port) },
    stdio: ["ignore", "pipe", "pipe"],
  });
  context.after(async () => {
    if (child.exitCode === null) {
      child.kill("SIGTERM");
      await once(child, "exit");
    }
  });

  for (let attempt = 0; attempt < 50; attempt += 1) {
    try {
      const response = await fetch(`${origin}/evidence`);
      if (response.ok) break;
    } catch {
      // The child has not bound its socket yet.
    }
    await new Promise((resolve) => setTimeout(resolve, 20));
  }

  const identity = await post(origin, "getMe");
  assert.deepEqual(identity, {
    id: 900000001,
    is_bot: true,
    first_name: "FB Agent Rehearsal",
    username: "fb_agent_rehearsal_bot",
    can_join_groups: true,
    can_read_all_group_messages: false,
    supports_inline_queries: false,
  });

  const webhookUrl =
    "https://app.example/api/v1/integrations/telegram/webhook?bot_generation=3";
  assert.equal(await post(origin, "setWebhook", { url: webhookUrl }), true);
  assert.deepEqual(await post(origin, "getWebhookInfo"), {
    url: webhookUrl,
    has_custom_certificate: false,
    pending_update_count: 0,
    max_connections: 40,
    allowed_updates: ["message", "edited_message", "callback_query"],
  });

  assert.deepEqual(
    await post(origin, "sendMessage", {
      chat_id: 1,
      text: "Disposable release rehearsal alert",
    }),
    { message_id: 42 },
  );
  assert.equal(
    await post(origin, "editMessageText", {
      chat_id: 1,
      message_id: 42,
      text: "RehearsalNotificationLifecycle resolved",
    }),
    true,
  );

  const evidence = await (await fetch(`${origin}/evidence`)).json();
  assert.deepEqual(evidence.calls, [
    { method: "sendMessage", chat_id: "1", message_id: 42, lifecycle: true },
    {
      method: "editMessageText",
      chat_id: "1",
      message_id: 42,
      lifecycle: true,
    },
  ]);
});
