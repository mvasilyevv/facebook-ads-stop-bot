import assert from "node:assert/strict";
import test from "node:test";

import { createTelegramStubContract } from "./telegram-server.mjs";

test("Telegram rehearsal stub implements the deployed gateway contract", () => {
  const contract = createTelegramStubContract();
  const identity = contract.dispatch("getMe");
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
  assert.equal(contract.dispatch("setWebhook", { url: webhookUrl }), true);
  assert.deepEqual(contract.dispatch("getWebhookInfo"), {
    url: webhookUrl,
    has_custom_certificate: false,
    pending_update_count: 0,
    max_connections: 40,
    allowed_updates: ["message", "edited_message", "callback_query"],
  });

  assert.deepEqual(
    contract.dispatch("sendMessage", {
      chat_id: 1,
      text: "Disposable release rehearsal alert",
    }),
    { message_id: 42 },
  );
  assert.equal(
    contract.dispatch("editMessageText", {
      chat_id: 1,
      message_id: 42,
      text: "RehearsalNotificationLifecycle resolved",
    }),
    true,
  );

  assert.deepEqual(contract.evidence(), [
    { method: "sendMessage", chat_id: "1", message_id: 42, lifecycle: true },
    {
      method: "editMessageText",
      chat_id: "1",
      message_id: 42,
      lifecycle: true,
    },
  ]);
});
