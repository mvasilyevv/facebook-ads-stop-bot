// adsManagerColumnsQs: дефолт содержит набор колонок/пресет; env переопределяет.
import { describe, it, afterEach } from "node:test";
import assert from "node:assert/strict";

import {
  adsManagerColumnsQs,
  adsManagerUrlUsesColumnsQs,
} from "./am-columns-preset.js";
import { AM_COLUMN_FIELDS } from "./am-config.js";

describe("adsManagerColumnsQs", () => {
  afterEach(() => {
    delete process.env.BROWSER_AGENT_AM_COLUMNS_QS;
  });

  // Дефолтный пресет — колонки пользователя + column_preset + attribution_windows.
  it("дефолт содержит columns, column_preset и attribution_windows", () => {
    delete process.env.BROWSER_AGENT_AM_COLUMNS_QS;
    const qs = adsManagerColumnsQs();
    assert.ok(qs.includes("columns="), "есть columns");
    assert.ok(
      qs.includes("column_preset=1030561339462971"),
      "есть column_preset",
    );
    assert.ok(
      qs.includes("attribution_windows=default"),
      "есть attribution_windows",
    );
  });

  // Env-переменная переопределяет дефолт (смена набора колонок без пересборки).
  it("env BROWSER_AGENT_AM_COLUMNS_QS переопределяет дефолт", () => {
    process.env.BROWSER_AGENT_AM_COLUMNS_QS = "columns=name&column_preset=999";
    assert.equal(adsManagerColumnsQs(), "columns=name&column_preset=999");
  });

  it("значение из БД имеет приоритет над env и проходит ту же санитизацию", () => {
    process.env.BROWSER_AGENT_AM_COLUMNS_QS =
      "columns=env_value&column_preset=777";
    assert.equal(
      adsManagerColumnsQs(
        "columns=name%2Cspend&column_preset=999&access_token=secret",
      ),
      "columns=name%2Cspend&column_preset=999",
    );
  });

  it("мусорное значение из БД не уезжает в URL", () => {
    const qs = adsManagerColumnsQs(
      "access_token=secret&business_id=42&columns=&column_preset=",
    );
    assert.ok(qs.includes("column_preset=1030561339462971"));
    assert.equal(qs.includes("secret"), false);
    assert.equal(qs.includes("business_id"), false);
  });

  it("env допускает только presentation-параметры и удаляет секреты", () => {
    process.env.BROWSER_AGENT_AM_COLUMNS_QS =
      "columns=name&column_preset=999&access_token=secret&business_id=42";
    assert.equal(adsManagerColumnsQs(), "columns=name&column_preset=999");
  });

  // Пустая env-переменная игнорируется (фолбэк на дефолт).
  it("пустая env игнорируется → дефолт", () => {
    process.env.BROWSER_AGENT_AM_COLUMNS_QS = "   ";
    assert.ok(adsManagerColumnsQs().includes("column_preset=1030561339462971"));
  });

  it("пустое значение из БД сохраняет fallback env → default", () => {
    process.env.BROWSER_AGENT_AM_COLUMNS_QS =
      "columns=name&column_preset=888";
    assert.equal(adsManagerColumnsQs("  "), "columns=name&column_preset=888");
    delete process.env.BROWSER_AGENT_AM_COLUMNS_QS;
    assert.ok(
      adsManagerColumnsQs("").includes("column_preset=1030561339462971"),
    );
  });

  it("сравнивает только безопасные presentation-параметры URL", () => {
    const url =
      "https://adsmanager.facebook.com/adsmanager/manage/campaigns" +
      "?act=123&columns=name%2Cspend&column_preset=999&access_token=ignored";
    assert.equal(
      adsManagerUrlUsesColumnsQs(
        url,
        "columns=name%2Cspend&column_preset=999",
      ),
      true,
    );
    assert.equal(adsManagerUrlUsesColumnsQs(url, "columns=name"), false);
  });

  it("presentation-настройка не меняет AM_COLUMN_FIELDS денежного скана", () => {
    const scanFields = [...AM_COLUMN_FIELDS];
    adsManagerColumnsQs("columns=name&column_preset=999");
    assert.deepEqual(AM_COLUMN_FIELDS, scanFields);
    assert.deepEqual(scanFields, [
      "results",
      "cost_per_result",
      "objective",
      "reach",
      "impressions",
      "spend",
      "clicks",
      "cpc",
      "actions",
      "cost_per_action_type",
      "ctr",
      "outbound_clicks",
      "outbound_clicks_ctr",
      "cpm",
      "frequency",
      "attribution_setting",
      "conversion_count_setting",
      "ad_id",
    ]);
  });
});
