"use strict";
(() => {
  // src/creator/registry.ts
  var _registry = /* @__PURE__ */ new Map();
  function registerStep(step) {
    if (_registry.has(step.name)) {
      throw new Error(`Step ${step.name} already registered`);
    }
    _registry.set(step.name, step);
  }
  function getStep(name) {
    return _registry.get(name);
  }
  function listSteps() {
    return Array.from(_registry.values());
  }

  // src/creator/steps/base.ts
  var BaseStep = class {
    async execute(state2, input, ctx) {
      if (this.isSatisfied(state2, input)) {
        ctx.emit("step_skipped", { step: this.name, reason: "already_satisfied" });
        return void 0;
      }
      return await this.run(state2, input, ctx);
    }
  };

  // src/creator/enums/conversion-location.ts
  var conversionLocationLabels = {
    WEBSITE: { ru: ["\u0421\u0430\u0439\u0442", "\u0412\u0435\u0431-\u0441\u0430\u0439\u0442"], en: ["Website", "Web site"] },
    WEBSITE_AND_CALLS: { ru: ["\u0421\u0430\u0439\u0442 \u0438 \u0437\u0432\u043E\u043D\u043A\u0438"], en: ["Website and calls"] },
    APP: { ru: ["\u041F\u0440\u0438\u043B\u043E\u0436\u0435\u043D\u0438\u0435"], en: ["App"] },
    MESSENGER: { ru: ["Messenger"], en: ["Messenger"] }
  };

  // src/creator/enums/pixel-event.ts
  var pixelEventLabels = {
    PURCHASE: { ru: ["\u041F\u043E\u043A\u0443\u043F\u043A\u0430"], en: ["Purchase"] },
    LEAD: { ru: ["\u041B\u0438\u0434"], en: ["Lead"] },
    COMPLETE_REGISTRATION: {
      ru: ["\u0417\u0430\u0432\u0435\u0440\u0448\u0451\u043D\u043D\u0430\u044F \u0440\u0435\u0433\u0438\u0441\u0442\u0440\u0430\u0446\u0438\u044F", "\u0420\u0435\u0433\u0438\u0441\u0442\u0440\u0430\u0446\u0438\u044F"],
      en: ["Complete registration", "Completed registration"]
    },
    ADD_TO_CART: { ru: ["\u0414\u043E\u0431\u0430\u0432\u043B\u0435\u043D\u0438\u0435 \u0432 \u043A\u043E\u0440\u0437\u0438\u043D\u0443"], en: ["Add to cart"] },
    INITIATE_CHECKOUT: { ru: ["\u041D\u0430\u0447\u0430\u043B\u043E \u043E\u0444\u043E\u0440\u043C\u043B\u0435\u043D\u0438\u044F"], en: ["Initiate checkout"] },
    SUBSCRIBE: { ru: ["\u041F\u043E\u0434\u043F\u0438\u0441\u043A\u0430"], en: ["Subscribe"] },
    ADD_PAYMENT_INFO: { ru: ["\u0414\u043E\u0431\u0430\u0432\u043B\u0435\u043D\u0438\u0435 \u043F\u043B\u0430\u0442\u0451\u0436\u043D\u043E\u0439 \u0438\u043D\u0444\u043E\u0440\u043C\u0430\u0446\u0438\u0438"], en: ["Add payment info"] },
    CONTACT: { ru: ["\u041A\u043E\u043D\u0442\u0430\u043A\u0442"], en: ["Contact"] },
    SEARCH: { ru: ["\u041F\u043E\u0438\u0441\u043A"], en: ["Search"] },
    VIEW_CONTENT: { ru: ["\u041F\u0440\u043E\u0441\u043C\u043E\u0442\u0440 \u043A\u043E\u043D\u0442\u0435\u043D\u0442\u0430"], en: ["View content"] }
  };

  // src/creator/enums/optimization-goal.ts
  var optimizationGoalLabels = {
    CONVERSIONS: { ru: ["\u041A\u043E\u043D\u0432\u0435\u0440\u0441\u0438\u0438"], en: ["Conversions"] },
    LANDING_PAGE_VIEWS: { ru: ["\u041F\u0440\u043E\u0441\u043C\u043E\u0442\u0440\u044B \u0446\u0435\u043B\u0435\u0432\u043E\u0439 \u0441\u0442\u0440\u0430\u043D\u0438\u0446\u044B"], en: ["Landing page views"] },
    LINK_CLICKS: { ru: ["\u041A\u043B\u0438\u043A\u0438 \u043F\u043E \u0441\u0441\u044B\u043B\u043A\u0435"], en: ["Link clicks"] },
    IMPRESSIONS: { ru: ["\u041F\u043E\u043A\u0430\u0437\u044B"], en: ["Impressions"] },
    REACH: { ru: ["\u041E\u0445\u0432\u0430\u0442"], en: ["Reach"] },
    VALUE: { ru: ["\u0426\u0435\u043D\u043D\u043E\u0441\u0442\u044C"], en: ["Value"] }
  };

  // src/creator/enums/attribution.ts
  var attributionLabels = {
    CLICK_1D: { ru: ["\u041A\u043B\u0438\u043A 1 \u0434\u0435\u043D\u044C", "1 \u0434\u0435\u043D\u044C \u043F\u043E\u0441\u043B\u0435 \u043A\u043B\u0438\u043A\u0430"], en: ["1-day click"] },
    CLICK_7D: { ru: ["\u041A\u043B\u0438\u043A 7 \u0434\u043D\u0435\u0439", "7 \u0434\u043D\u0435\u0439 \u043F\u043E\u0441\u043B\u0435 \u043A\u043B\u0438\u043A\u0430"], en: ["7-day click"] },
    CLICK_7D_VIEW_1D: {
      ru: ["\u041A\u043B\u0438\u043A 7 \u0434\u043D\u0435\u0439 \u0438\u043B\u0438 \u043F\u0440\u043E\u0441\u043C\u043E\u0442\u0440 1 \u0434\u0435\u043D\u044C"],
      en: ["7-day click or 1-day view"]
    },
    CLICK_1D_VIEW_1D: {
      ru: ["\u041A\u043B\u0438\u043A 1 \u0434\u0435\u043D\u044C \u0438\u043B\u0438 \u043F\u0440\u043E\u0441\u043C\u043E\u0442\u0440 1 \u0434\u0435\u043D\u044C"],
      en: ["1-day click or 1-day view"]
    }
  };

  // src/creator/enums/cta.ts
  var ctaLabels = {
    LEARN_MORE: { ru: ["\u041F\u043E\u0434\u0440\u043E\u0431\u043D\u0435\u0435"], en: ["Learn more"] },
    SIGN_UP: { ru: ["\u0417\u0430\u0440\u0435\u0433\u0438\u0441\u0442\u0440\u0438\u0440\u043E\u0432\u0430\u0442\u044C\u0441\u044F"], en: ["Sign up"] },
    SHOP_NOW: { ru: ["\u0412 \u043C\u0430\u0433\u0430\u0437\u0438\u043D"], en: ["Shop now"] },
    SUBSCRIBE: { ru: ["\u041F\u043E\u0434\u043F\u0438\u0441\u0430\u0442\u044C\u0441\u044F"], en: ["Subscribe"] },
    GET_OFFER: { ru: ["\u041F\u043E\u043B\u0443\u0447\u0438\u0442\u044C \u043F\u0440\u0435\u0434\u043B\u043E\u0436\u0435\u043D\u0438\u0435"], en: ["Get offer"] },
    BOOK_TRAVEL: { ru: ["\u0417\u0430\u0431\u0440\u043E\u043D\u0438\u0440\u043E\u0432\u0430\u0442\u044C"], en: ["Book travel", "Book now"] },
    DOWNLOAD: { ru: ["\u0421\u043A\u0430\u0447\u0430\u0442\u044C"], en: ["Download"] },
    CONTACT_US: { ru: ["\u0421\u0432\u044F\u0437\u0430\u0442\u044C\u0441\u044F \u0441 \u043D\u0430\u043C\u0438"], en: ["Contact us"] },
    APPLY_NOW: { ru: ["\u041F\u043E\u0434\u0430\u0442\u044C \u0437\u0430\u044F\u0432\u043A\u0443"], en: ["Apply now"] }
  };

  // src/creator/enums/objective.ts
  var objectiveLabels = {
    SALES: { ru: ["\u041F\u0440\u043E\u0434\u0430\u0436\u0438"], en: ["Sales"] },
    LEADS: { ru: ["\u041B\u0438\u0434\u044B"], en: ["Leads"] },
    ENGAGEMENT: { ru: ["\u0412\u043E\u0432\u043B\u0435\u0447\u0451\u043D\u043D\u043E\u0441\u0442\u044C"], en: ["Engagement"] },
    TRAFFIC: { ru: ["\u0422\u0440\u0430\u0444\u0438\u043A"], en: ["Traffic"] },
    AWARENESS: { ru: ["\u0423\u0437\u043D\u0430\u0432\u0430\u0435\u043C\u043E\u0441\u0442\u044C"], en: ["Awareness"] },
    APP_PROMOTION: { ru: ["\u041F\u0440\u043E\u0434\u0432\u0438\u0436\u0435\u043D\u0438\u0435 \u043F\u0440\u0438\u043B\u043E\u0436\u0435\u043D\u0438\u044F"], en: ["App promotion"] }
  };

  // src/creator/humanizer.ts
  var IdleRange = {
    SHORT: [80, 250],
    BETWEEN_STEPS: [600, 2500],
    BETWEEN_SCENES: [3e3, 8e3],
    TYPING: [40, 180],
    TYPING_BURST_PAUSE: [200, 800]
  };
  var TYPING_BURST_MIN = 3;
  var TYPING_BURST_JITTER = 6;
  function rand(min, max) {
    return min + Math.random() * (max - min);
  }
  function humanIdle(range) {
    return new Promise((resolve) => setTimeout(resolve, rand(range[0], range[1])));
  }
  function dispatchPointer(el, type, x, y) {
    const ev = new PointerEvent(type, {
      bubbles: true,
      cancelable: true,
      composed: true,
      clientX: x,
      clientY: y,
      pointerType: "mouse",
      isPrimary: true
    });
    el.dispatchEvent(ev);
  }
  async function jitterHover(el) {
    const rect = el.getBoundingClientRect();
    const tx = rect.left + rect.width / 2;
    const ty = rect.top + rect.height / 2;
    dispatchPointer(el, "pointerover", tx, ty);
    const steps = 6 + Math.floor(Math.random() * 6);
    for (let i = 1; i <= steps; i++) {
      dispatchPointer(el, "pointermove", tx + Math.random() * 2 - 1, ty + Math.random() * 2 - 1);
      await humanIdle([8, 24]);
    }
  }
  async function humanClick(el) {
    await jitterHover(el);
    await humanIdle(IdleRange.SHORT);
    const rect = el.getBoundingClientRect();
    const x = rect.left + rect.width / 2;
    const y = rect.top + rect.height / 2;
    dispatchPointer(el, "pointerdown", x, y);
    await humanIdle([20, 90]);
    dispatchPointer(el, "pointerup", x, y);
    el.dispatchEvent(
      new MouseEvent("click", {
        bubbles: true,
        cancelable: true,
        clientX: x,
        clientY: y,
        button: 0,
        detail: 1
      })
    );
  }
  async function humanDoubleClick(el) {
    const rect = el.getBoundingClientRect();
    const baseX = rect.left + rect.width / 2;
    const baseY = rect.top + rect.height / 2;
    const doPointerCycle = async (x, y, detail) => {
      dispatchPointer(el, "pointerdown", x, y);
      await humanIdle([20, 90]);
      dispatchPointer(el, "pointerup", x, y);
      el.dispatchEvent(
        new MouseEvent("click", {
          bubbles: true,
          cancelable: true,
          clientX: x,
          clientY: y,
          button: 0,
          detail
        })
      );
    };
    await jitterHover(el);
    await humanIdle(IdleRange.SHORT);
    await doPointerCycle(baseX, baseY, 1);
    await humanIdle([80, 130]);
    const x2 = baseX + (Math.random() * 2 - 1);
    const y2 = baseY + (Math.random() * 2 - 1);
    await doPointerCycle(x2, y2, 2);
    el.dispatchEvent(
      new MouseEvent("dblclick", {
        bubbles: true,
        cancelable: true,
        clientX: x2,
        clientY: y2,
        button: 0,
        detail: 2
      })
    );
  }
  function setNativeInputValue(el, value) {
    const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
    setter?.call(el, value);
  }
  async function humanType(el, text) {
    el.focus();
    await humanIdle(IdleRange.SHORT);
    let current = "";
    for (let i = 0; i < text.length; i++) {
      const ch = text[i];
      el.dispatchEvent(new KeyboardEvent("keydown", { key: ch, bubbles: true }));
      el.dispatchEvent(new KeyboardEvent("keypress", { key: ch, bubbles: true }));
      current += ch;
      setNativeInputValue(el, current);
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new KeyboardEvent("keyup", { key: ch, bubbles: true }));
      await humanIdle(IdleRange.TYPING);
      if (i > 0 && i % (TYPING_BURST_MIN + Math.floor(Math.random() * TYPING_BURST_JITTER)) === 0) {
        await humanIdle(IdleRange.TYPING_BURST_PAUSE);
      }
    }
    el.dispatchEvent(new Event("change", { bubbles: true }));
    el.blur();
  }

  // src/creator/text.ts
  var INVISIBLE_RE = /[​-‏‪-‮⁠-⁯﻿]/g;
  function normalizeText(input) {
    return input.replace(INVISIBLE_RE, "").toLowerCase().trim().replace(/\s+/g, " ");
  }

  // src/creator/fiber.ts
  function findKey(el, prefix) {
    for (const key of Object.keys(el)) {
      if (key.startsWith(prefix)) return key;
    }
    return null;
  }
  function getReactProps(el) {
    const key = findKey(el, "__reactProps$");
    return key ? el[key] : null;
  }

  // src/creator/locator.ts
  function findByTestId(testid, root = document) {
    return root.querySelector(`[data-testid="${CSS.escape(testid)}"]`);
  }
  function findByAriaLabel(labels, root = document) {
    const targets = new Set(labels.map(normalizeText));
    for (const el of Array.from(root.querySelectorAll("[aria-label]"))) {
      const aria = normalizeText(el.getAttribute("aria-label") || "");
      if (targets.has(aria)) return el;
    }
    return null;
  }
  function findByFiberRole(role, root = document) {
    for (const el of Array.from(root.querySelectorAll("*"))) {
      const props = getReactProps(el);
      if (props && props.role === role) return el;
    }
    return null;
  }
  function findByNormalizedText(texts, root = document) {
    const targets = new Set(texts.map(normalizeText));
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
    let cur = walker.currentNode;
    while (cur) {
      const direct = Array.from(cur.childNodes).filter((n) => n.nodeType === Node.TEXT_NODE).map((n) => normalizeText(n.textContent || "")).join(" ").trim();
      if (direct && targets.has(direct)) return cur;
      cur = walker.nextNode();
    }
    return null;
  }
  function findBlock(spec, root = document) {
    if (spec.testid) {
      const el = findByTestId(spec.testid, root);
      if (el) return el;
    }
    if (spec.fiberRole) {
      const el = findByFiberRole(spec.fiberRole, root);
      if (el) return el;
    }
    if (spec.aria?.length) {
      const el = findByAriaLabel(spec.aria, root);
      if (el) return el;
    }
    if (spec.text?.length) {
      const el = findByNormalizedText(spec.text, root);
      if (el) return el;
    }
    return null;
  }

  // src/creator/steps/_helpers/select-from-dropdown.ts
  function resolveLabelToEnum(label, labels) {
    const norm = normalizeText(label);
    for (const [enumKey, syns] of Object.entries(labels)) {
      const all = [...syns.ru, ...syns.en].map(normalizeText);
      if (all.includes(norm)) return enumKey;
    }
    return null;
  }
  function readSelectedValue(spec) {
    const block = findBlock(spec.block);
    if (!block) return null;
    const visible = block.querySelector(
      '[aria-selected="true"], [data-selected="true"], button[aria-haspopup="listbox"]'
    );
    const text = (visible?.textContent ?? "").trim();
    if (!text) return null;
    return resolveLabelToEnum(text, spec.labels);
  }
  async function selectValue(spec, target) {
    const block = findBlock(spec.block);
    if (!block) throw new Error(`\u0411\u043B\u043E\u043A \u043D\u0435 \u043D\u0430\u0439\u0434\u0435\u043D: ${JSON.stringify(spec.block)}`);
    const trigger = block.querySelector(
      'button[aria-haspopup="listbox"], [role="combobox"]'
    );
    if (!trigger) throw new Error("Trigger \u0434\u0440\u043E\u043F\u0434\u0430\u0443\u043D\u0430 \u043D\u0435 \u043D\u0430\u0439\u0434\u0435\u043D");
    await humanClick(trigger);
    await humanIdle(IdleRange.SHORT);
    const syns = spec.labels[target];
    if (!syns) throw new Error(`Unknown enum value: ${target}`);
    const option = findByNormalizedText([...syns.ru, ...syns.en]);
    if (!option) {
      throw new Error(
        `\u041E\u043F\u0446\u0438\u044F "${target}" \u043D\u0435 \u043D\u0430\u0439\u0434\u0435\u043D\u0430 \u0432 \u0434\u0440\u043E\u043F\u0434\u0430\u0443\u043D\u0435 (\u0441\u0438\u043D\u043E\u043D\u0438\u043C\u044B: ${[...syns.ru, ...syns.en].join(", ")})`
      );
    }
    await humanClick(option);
    await humanIdle(IdleRange.BETWEEN_STEPS);
  }

  // src/creator/steps/set_conversion_location.ts
  var SPEC = {
    block: {
      testid: "conversion-location",
      aria: ["\u041C\u0435\u0441\u0442\u043E \u043A\u043E\u043D\u0432\u0435\u0440\u0441\u0438\u0438", "Conversion location"],
      text: ["\u043C\u0435\u0441\u0442\u043E \u043A\u043E\u043D\u0432\u0435\u0440\u0441\u0438\u0438", "conversion location"]
    },
    labels: conversionLocationLabels
  };
  var SetConversionLocationStep = class extends BaseStep {
    name = "set_conversion_location";
    async detect(_ctx) {
      const current = readSelectedValue(SPEC);
      return current ? { kind: "matched", current } : { kind: "missing" };
    }
    isSatisfied(state2, input) {
      return state2.kind === "matched" && state2.current === input.value;
    }
    async run(_state, input) {
      await selectValue(SPEC, input.value);
    }
  };

  // src/creator/steps/set_pixel_event.ts
  var EVENT_SPEC = {
    block: {
      testid: "pixel-event",
      aria: ["\u0421\u043E\u0431\u044B\u0442\u0438\u0435 \u043A\u043E\u043D\u0432\u0435\u0440\u0441\u0438\u0438", "Conversion event"],
      text: ["\u0441\u043E\u0431\u044B\u0442\u0438\u0435 \u043A\u043E\u043D\u0432\u0435\u0440\u0441\u0438\u0438", "conversion event"]
    },
    labels: pixelEventLabels
  };
  var PIXEL_BLOCK = {
    testid: "pixel-selector",
    aria: ["\u0418\u0441\u0442\u043E\u0447\u043D\u0438\u043A \u0434\u0430\u043D\u043D\u044B\u0445", "Data source", "\u041F\u0438\u043A\u0441\u0435\u043B\u044C"],
    text: ["\u043F\u0438\u043A\u0441\u0435\u043B\u044C", "data source"]
  };
  function readCurrentPixelId() {
    const block = findBlock(PIXEL_BLOCK);
    if (!block) return null;
    const id = block.querySelector("[data-pixel-id]")?.getAttribute("data-pixel-id");
    return id ?? null;
  }
  var SetPixelEventStep = class extends BaseStep {
    name = "set_pixel_event";
    async detect(_ctx) {
      const event = readSelectedValue(EVENT_SPEC);
      const pixelId = readCurrentPixelId();
      if (event && pixelId) {
        return { kind: "matched", current: { event, pixelId } };
      }
      return { kind: "missing" };
    }
    isSatisfied(state2, input) {
      if (state2.kind !== "matched") return false;
      const cur = state2.current;
      return cur.event === input.event && cur.pixelId === input.pixelId;
    }
    async run(_state, input) {
      const pxBlock = findBlock(PIXEL_BLOCK);
      if (!pxBlock) throw new Error("\u041D\u0435 \u043D\u0430\u0439\u0434\u0435\u043D \u0431\u043B\u043E\u043A \u0432\u044B\u0431\u043E\u0440\u0430 Pixel \u0432 UI");
      const trigger = pxBlock.querySelector(
        'button[aria-haspopup="listbox"], [role="combobox"]'
      );
      if (!trigger) throw new Error("\u041D\u0435 \u043D\u0430\u0439\u0434\u0435\u043D \u0442\u0440\u0438\u0433\u0433\u0435\u0440 \u043E\u0442\u043A\u0440\u044B\u0442\u0438\u044F \u0441\u043F\u0438\u0441\u043A\u0430 Pixel");
      await humanClick(trigger);
      await humanIdle(IdleRange.SHORT);
      const search = document.querySelector(
        'input[role="combobox"], input[type="search"]'
      );
      if (search) {
        await humanType(search, input.pixelId);
        await humanIdle(IdleRange.BETWEEN_STEPS);
        const option = document.querySelector(
          `[role="option"][data-pixel-id="${input.pixelId}"]`
        ) ?? document.querySelector('[role="option"]');
        if (!option) throw new Error(`\u041F\u0438\u043A\u0441\u0435\u043B\u044C ${input.pixelId} \u043D\u0435 \u043D\u0430\u0439\u0434\u0435\u043D \u0432 \u0441\u043F\u0438\u0441\u043A\u0435`);
        await humanClick(option);
        await humanIdle(IdleRange.BETWEEN_STEPS);
      }
      await selectValue(EVENT_SPEC, input.event);
    }
  };

  // src/creator/steps/set_optimization_goal.ts
  var SPEC2 = {
    block: {
      testid: "optimization-goal",
      aria: ["\u0426\u0435\u043B\u044C \u043E\u043F\u0442\u0438\u043C\u0438\u0437\u0430\u0446\u0438\u0438", "Optimization goal", "Performance goal"],
      text: ["\u0446\u0435\u043B\u044C \u043E\u043F\u0442\u0438\u043C\u0438\u0437\u0430\u0446\u0438\u0438", "optimization goal"]
    },
    labels: optimizationGoalLabels
  };
  var SetOptimizationGoalStep = class extends BaseStep {
    name = "set_optimization_goal";
    async detect(_ctx) {
      const current = readSelectedValue(SPEC2);
      return current ? { kind: "matched", current } : { kind: "missing" };
    }
    isSatisfied(state2, input) {
      return state2.kind === "matched" && state2.current === input.value;
    }
    async run(_state, input) {
      await selectValue(SPEC2, input.value);
    }
  };

  // src/creator/steps/set_attribution.ts
  var SPEC3 = {
    block: {
      testid: "attribution-setting",
      aria: ["\u041E\u043A\u043D\u043E \u0430\u0442\u0440\u0438\u0431\u0443\u0446\u0438\u0438", "Attribution setting", "Attribution window"],
      text: ["\u043E\u043A\u043D\u043E \u0430\u0442\u0440\u0438\u0431\u0443\u0446\u0438\u0438", "attribution"]
    },
    labels: attributionLabels
  };
  var SetAttributionStep = class extends BaseStep {
    name = "set_attribution";
    async detect(_ctx) {
      const current = readSelectedValue(SPEC3);
      return current ? { kind: "matched", current } : { kind: "missing" };
    }
    isSatisfied(state2, input) {
      return state2.kind === "matched" && state2.current === input.value;
    }
    async run(_state, input) {
      await selectValue(SPEC3, input.value);
    }
  };

  // src/creator/steps/set_cta.ts
  var SPEC4 = {
    block: {
      testid: "call-to-action",
      aria: ["\u041F\u0440\u0438\u0437\u044B\u0432 \u043A \u0434\u0435\u0439\u0441\u0442\u0432\u0438\u044E", "Call to action"],
      text: ["\u043F\u0440\u0438\u0437\u044B\u0432 \u043A \u0434\u0435\u0439\u0441\u0442\u0432\u0438\u044E", "call to action"]
    },
    labels: ctaLabels
  };
  var SetCtaStep = class extends BaseStep {
    name = "set_cta";
    async detect(_ctx) {
      const current = readSelectedValue(SPEC4);
      return current ? { kind: "matched", current } : { kind: "missing" };
    }
    isSatisfied(state2, input) {
      return state2.kind === "matched" && state2.current === input.value;
    }
    async run(_state, input) {
      await selectValue(SPEC4, input.value);
    }
  };

  // src/creator/steps/set_geo.ts
  var BLOCK = {
    testid: "locations",
    aria: ["\u041C\u0435\u0441\u0442\u0430", "Locations"],
    text: ["\u043C\u0435\u0441\u0442\u0430", "locations"]
  };
  function readCurrentCountries() {
    const block = findBlock(BLOCK);
    if (!block) return [];
    return Array.from(
      block.querySelectorAll('[data-testid="selected-country"], [aria-label^="\u0423\u0434\u0430\u043B\u0438\u0442\u044C"]')
    ).map((el) => (el.getAttribute("data-country") || el.textContent || "").trim()).filter(Boolean);
  }
  var SetGeoStep = class extends BaseStep {
    name = "set_geo";
    detect() {
      return { kind: "matched", current: readCurrentCountries() };
    }
    isSatisfied(state2, input) {
      const cur = new Set(state2.current || []);
      return input.countries.every((c) => cur.has(c));
    }
    async run(_s, input) {
      const block = findBlock(BLOCK);
      if (!block) throw new Error("\u0411\u043B\u043E\u043A Locations \u043D\u0435 \u043D\u0430\u0439\u0434\u0435\u043D");
      const search = block.querySelector(
        'input[type="text"], input[type="search"]'
      );
      if (!search) throw new Error("\u041F\u043E\u043B\u0435 \u043F\u043E\u0438\u0441\u043A\u0430 \u0441\u0442\u0440\u0430\u043D \u043D\u0435 \u043D\u0430\u0439\u0434\u0435\u043D\u043E");
      const cur = new Set(readCurrentCountries());
      for (const code of input.countries) {
        if (cur.has(code)) continue;
        await humanType(search, code);
        await humanIdle(IdleRange.BETWEEN_STEPS);
        const option = document.querySelector(`[role="option"][data-country="${code}"]`) ?? document.querySelector('[role="option"]');
        if (!option) throw new Error(`\u0421\u0442\u0440\u0430\u043D\u0430 ${code} \u043D\u0435 \u043D\u0430\u0439\u0434\u0435\u043D\u0430 \u0432 \u043F\u043E\u0434\u0441\u043A\u0430\u0437\u043A\u0430\u0445`);
        await humanClick(option);
        await humanIdle(IdleRange.SHORT);
      }
    }
  };

  // src/creator/steps/set_age.ts
  var BLOCK2 = { aria: ["\u0412\u043E\u0437\u0440\u0430\u0441\u0442", "Age"], text: ["\u0432\u043E\u0437\u0440\u0430\u0441\u0442", "age"] };
  function parseAriaNum(el) {
    if (!el) return NaN;
    const raw = el.getAttribute("aria-label") ?? el.value ?? "";
    const m = raw.match(/\d+/);
    return m ? Number(m[0]) : NaN;
  }
  function readRange() {
    const block = findBlock(BLOCK2);
    if (!block) return null;
    const minSel = block.querySelector('[data-testid="age-min"] [aria-label]') ?? block.querySelector('select[name*="min"]');
    const maxSel = block.querySelector('[data-testid="age-max"] [aria-label]') ?? block.querySelector('select[name*="max"]');
    const min = parseAriaNum(minSel);
    const max = parseAriaNum(maxSel);
    return Number.isFinite(min) && Number.isFinite(max) ? { min, max } : null;
  }
  async function pickFromDropdown(trigger, value) {
    await humanClick(trigger);
    await humanIdle(IdleRange.SHORT);
    const option = Array.from(
      document.querySelectorAll('[role="option"]')
    ).find((el) => (el.textContent || "").trim() === String(value));
    if (!option) throw new Error(`\u041E\u043F\u0446\u0438\u044F ${value} \u043D\u0435 \u043D\u0430\u0439\u0434\u0435\u043D\u0430`);
    await humanClick(option);
  }
  var SetAgeStep = class extends BaseStep {
    name = "set_age";
    detect() {
      const cur = readRange();
      return cur ? { kind: "matched", current: cur } : { kind: "missing" };
    }
    isSatisfied(state2, input) {
      const c = state2.current;
      return !!c && c.min === input.min && c.max === input.max;
    }
    async run(_s, input) {
      const block = findBlock(BLOCK2);
      if (!block) throw new Error("\u0411\u043B\u043E\u043A Age \u043D\u0435 \u043D\u0430\u0439\u0434\u0435\u043D");
      const minTrigger = block.querySelector(
        '[data-testid="age-min"] button, button[aria-label*="\u043C\u0438\u043D"], button[aria-label*="min"]'
      );
      const maxTrigger = block.querySelector(
        '[data-testid="age-max"] button, button[aria-label*="\u043C\u0430\u043A\u0441"], button[aria-label*="max"]'
      );
      if (!minTrigger || !maxTrigger) throw new Error("\u0422\u0440\u0438\u0433\u0433\u0435\u0440\u044B \u0432\u043E\u0437\u0440\u0430\u0441\u0442\u0430 \u043D\u0435 \u043D\u0430\u0439\u0434\u0435\u043D\u044B");
      await pickFromDropdown(minTrigger, input.min);
      await humanIdle(IdleRange.BETWEEN_STEPS);
      await pickFromDropdown(maxTrigger, input.max);
    }
  };

  // src/creator/steps/set_budget.ts
  var BLOCK3 = { aria: ["\u0411\u044E\u0434\u0436\u0435\u0442", "Budget"], text: ["\u0431\u044E\u0434\u0436\u0435\u0442", "budget"] };
  function readAmount() {
    const block = findBlock(BLOCK3);
    if (!block) return null;
    const input = block.querySelector(
      'input[inputmode="decimal"], input[type="number"], input[name*="budget"]'
    );
    if (!input) return null;
    const num = Number(input.value.replace(/[^\d.,]/g, "").replace(",", "."));
    const cur = (block.querySelector('[aria-label*="\u0432\u0430\u043B\u044E\u0442"], [aria-label*="curren"]')?.textContent || "").trim();
    return Number.isFinite(num) ? { amount: num, currency: cur } : null;
  }
  var SetBudgetStep = class extends BaseStep {
    name = "set_budget";
    detect() {
      const cur = readAmount();
      return cur ? { kind: "matched", current: cur } : { kind: "missing" };
    }
    isSatisfied(state2, input) {
      const c = state2.current;
      if (!c || c.amount !== input.amount) return false;
      if (input.currency && c.currency !== input.currency) return false;
      return true;
    }
    async run(_s, input) {
      const block = findBlock(BLOCK3);
      if (!block) throw new Error("\u0411\u043B\u043E\u043A Budget \u043D\u0435 \u043D\u0430\u0439\u0434\u0435\u043D");
      const field = block.querySelector(
        'input[inputmode="decimal"], input[type="number"], input[name*="budget"]'
      );
      if (!field) throw new Error("\u041F\u043E\u043B\u0435 \u0431\u044E\u0434\u0436\u0435\u0442\u0430 \u043D\u0435 \u043D\u0430\u0439\u0434\u0435\u043D\u043E");
      await humanClick(field);
      field.select();
      await humanIdle(IdleRange.SHORT);
      await humanType(field, String(input.amount));
      await humanIdle(IdleRange.BETWEEN_STEPS);
    }
  };

  // src/creator/steps/set_schedule_start.ts
  var BLOCK4 = {
    testid: "schedule-start",
    aria: ["\u0414\u0430\u0442\u0430 \u043D\u0430\u0447\u0430\u043B\u0430", "Start date", "Schedule start"],
    text: ["\u0434\u0430\u0442\u0430 \u043D\u0430\u0447\u0430\u043B\u0430", "start date"]
  };
  function readStart() {
    const block = findBlock(BLOCK4);
    if (!block) return null;
    const field = block.querySelector(
      'input[type="datetime-local"], input[type="date"], input[name*="start"]'
    );
    return field?.value?.trim() || null;
  }
  var SetScheduleStartStep = class extends BaseStep {
    name = "set_schedule_start";
    detect() {
      const cur = readStart();
      return cur ? { kind: "matched", current: cur } : { kind: "missing" };
    }
    isSatisfied(state2, input) {
      return state2.kind === "matched" && state2.current === input.isoDate;
    }
    async run(_s, input) {
      const block = findBlock(BLOCK4);
      if (!block) throw new Error("\u0411\u043B\u043E\u043A Schedule start \u043D\u0435 \u043D\u0430\u0439\u0434\u0435\u043D");
      const field = block.querySelector(
        'input[type="datetime-local"], input[type="date"], input[name*="start"]'
      );
      if (!field) throw new Error("\u041F\u043E\u043B\u0435 \u0434\u0430\u0442\u044B \u043D\u0430\u0447\u0430\u043B\u0430 \u043D\u0435 \u043D\u0430\u0439\u0434\u0435\u043D\u043E");
      await humanClick(field);
      field.select();
      await humanIdle(IdleRange.SHORT);
      await humanType(field, input.isoDate);
      await humanIdle(IdleRange.BETWEEN_STEPS);
    }
  };

  // src/creator/steps/set_tracking_url.ts
  var BLOCK5 = {
    testid: "tracking-url",
    aria: ["URL \u0434\u043B\u044F \u043E\u0442\u0441\u043B\u0435\u0436\u0438\u0432\u0430\u043D\u0438\u044F", "Tracking URL", "URL parameters"],
    text: ["url \u0434\u043B\u044F \u043E\u0442\u0441\u043B\u0435\u0436\u0438\u0432\u0430\u043D\u0438\u044F", "tracking url"]
  };
  function readUrl() {
    const block = findBlock(BLOCK5);
    if (!block) return null;
    const input = block.querySelector(
      'input[type="text"], input[type="url"], textarea'
    );
    return input?.value?.trim() || null;
  }
  var SetTrackingUrlStep = class extends BaseStep {
    name = "set_tracking_url";
    detect() {
      const cur = readUrl();
      return cur ? { kind: "matched", current: cur } : { kind: "missing" };
    }
    isSatisfied(state2, input) {
      return state2.kind === "matched" && state2.current === input.url;
    }
    async run(_s, input) {
      const block = findBlock(BLOCK5);
      if (!block) throw new Error("\u0411\u043B\u043E\u043A Tracking URL \u043D\u0435 \u043D\u0430\u0439\u0434\u0435\u043D");
      const field = block.querySelector(
        'input[type="text"], input[type="url"], textarea'
      );
      if (!field) throw new Error("\u041F\u043E\u043B\u0435 URL \u043D\u0435 \u043D\u0430\u0439\u0434\u0435\u043D\u043E");
      await humanClick(field);
      field.select();
      await humanIdle(IdleRange.SHORT);
      await humanType(field, input.url);
      await humanIdle(IdleRange.BETWEEN_STEPS);
    }
  };

  // src/creator/steps/fill_texts.ts
  var PRIMARY = {
    testid: "primary-text",
    aria: ["\u041E\u0441\u043D\u043E\u0432\u043D\u043E\u0439 \u0442\u0435\u043A\u0441\u0442", "Primary text"]
  };
  var HEADLINE = {
    testid: "headline",
    aria: ["\u0417\u0430\u0433\u043E\u043B\u043E\u0432\u043E\u043A", "Headline"]
  };
  var DESCRIPTION = {
    testid: "description",
    aria: ["\u041E\u043F\u0438\u0441\u0430\u043D\u0438\u0435", "Description"]
  };
  function readField(block) {
    const el = findBlock(block);
    if (!el) return null;
    const input = el.querySelector(
      'textarea, input[type="text"], [contenteditable="true"]'
    );
    if (!input) return null;
    if ("value" in input && typeof input.value === "string") return input.value;
    return (input.textContent || "").trim();
  }
  async function fillBlock(block, value) {
    const el = findBlock(block);
    if (!el) throw new Error(`\u0411\u043B\u043E\u043A \u043D\u0435 \u043D\u0430\u0439\u0434\u0435\u043D: ${JSON.stringify(block)}`);
    const field = el.querySelector(
      'textarea, input[type="text"]'
    );
    if (!field) throw new Error("\u041F\u043E\u043B\u0435 \u0442\u0435\u043A\u0441\u0442\u0430 \u043D\u0435 \u043D\u0430\u0439\u0434\u0435\u043D\u043E");
    await humanClick(field);
    field.select?.();
    await humanIdle(IdleRange.SHORT);
    await humanType(field, value);
    await humanIdle(IdleRange.BETWEEN_STEPS);
  }
  var FillTextsStep = class extends BaseStep {
    name = "fill_texts";
    detect() {
      const current = {
        primary: readField(PRIMARY) ?? "",
        headline: readField(HEADLINE) ?? "",
        description: readField(DESCRIPTION) ?? ""
      };
      return { kind: "matched", current };
    }
    isSatisfied(state2, input) {
      const c = state2.current;
      if (!c) return false;
      if (c.primary !== input.primary) return false;
      if (c.headline !== input.headline) return false;
      if (input.description !== void 0 && c.description !== input.description)
        return false;
      return true;
    }
    async run(_s, input) {
      await fillBlock(PRIMARY, input.primary);
      await fillBlock(HEADLINE, input.headline);
      if (input.description) await fillBlock(DESCRIPTION, input.description);
    }
  };

  // src/creator/steps/upload_creatives.ts
  var BLOCK6 = {
    testid: "media-section",
    aria: ["\u041C\u0435\u0434\u0438\u0430", "Media"],
    text: ["\u043C\u0435\u0434\u0438\u0430", "media"]
  };
  var UploadCreativesStep = class extends BaseStep {
    name = "upload_creatives";
    detect() {
      const block = findBlock(BLOCK6);
      const thumbs = block?.querySelectorAll('[data-testid="creative-thumb"]') ?? [];
      return { kind: "matched", current: thumbs.length };
    }
    isSatisfied(state2, input) {
      return state2.current === input.paths.length;
    }
    async run(_s, input, ctx) {
      const block = findBlock(BLOCK6);
      if (!block) throw new Error("\u0411\u043B\u043E\u043A Media \u043D\u0435 \u043D\u0430\u0439\u0434\u0435\u043D");
      const fileInput = block.querySelector('input[type="file"]');
      if (!fileInput) throw new Error("input[type=file] \u043D\u0435 \u043D\u0430\u0439\u0434\u0435\u043D \u0432 \u0431\u043B\u043E\u043A\u0435 Media");
      const id = `upload-${Date.now()}`;
      fileInput.setAttribute("data-fb-upload-id", id);
      ctx.emit("request_upload", {
        id,
        paths: input.paths,
        selector: `input[data-fb-upload-id="${id}"]`
      });
    }
  };

  // src/creator/steps/create_campaign.ts
  var OBJECTIVE_SPEC = {
    block: {
      testid: "campaign-objective",
      aria: ["\u0426\u0435\u043B\u044C \u043A\u0430\u043C\u043F\u0430\u043D\u0438\u0438", "Campaign objective"],
      text: ["\u0446\u0435\u043B\u044C \u043A\u0430\u043C\u043F\u0430\u043D\u0438\u0438", "campaign objective"]
    },
    labels: objectiveLabels
  };
  var NAME_BLOCK = {
    testid: "campaign-name",
    aria: ["\u041D\u0430\u0437\u0432\u0430\u043D\u0438\u0435 \u043A\u0430\u043C\u043F\u0430\u043D\u0438\u0438", "Campaign name"]
  };
  function readName() {
    const block = findBlock(NAME_BLOCK);
    if (!block) return null;
    const input = block.querySelector('input[type="text"]');
    return input?.value || null;
  }
  var CreateCampaignStep = class extends BaseStep {
    name = "create_campaign";
    detect() {
      const name = readName();
      return name ? { kind: "matched", current: { name } } : { kind: "missing" };
    }
    isSatisfied(state2, input) {
      const c = state2.current;
      return !!c && c.name === input.name;
    }
    async run(_s, input) {
      const createBtn = findByAriaLabel(["\u0421\u043E\u0437\u0434\u0430\u0442\u044C", "Create"]) ?? findByNormalizedText(["\u0441\u043E\u0437\u0434\u0430\u0442\u044C", "create"]);
      if (createBtn) {
        await humanClick(createBtn);
        await humanIdle(IdleRange.BETWEEN_STEPS);
      }
      await selectValue(OBJECTIVE_SPEC, input.objective);
      const block = findBlock(NAME_BLOCK);
      if (block) {
        const field = block.querySelector('input[type="text"]');
        if (field) {
          await humanClick(field);
          field.select();
          await humanIdle(IdleRange.SHORT);
          await humanType(field, input.name);
          await humanIdle(IdleRange.BETWEEN_STEPS);
        }
      }
    }
  };

  // src/creator/steps/create_adset.ts
  var NAME_BLOCK2 = {
    testid: "adset-name",
    aria: ["\u041D\u0430\u0437\u0432\u0430\u043D\u0438\u0435 \u0433\u0440\u0443\u043F\u043F\u044B \u043E\u0431\u044A\u044F\u0432\u043B\u0435\u043D\u0438\u0439", "Ad set name"]
  };
  function readName2() {
    const block = findBlock(NAME_BLOCK2);
    if (!block) return null;
    const input = block.querySelector('input[type="text"]');
    return input?.value || null;
  }
  var CreateAdsetStep = class extends BaseStep {
    name = "create_adset";
    detect() {
      const name = readName2();
      return name ? { kind: "matched", current: { name } } : { kind: "missing" };
    }
    isSatisfied(state2, input) {
      const c = state2.current;
      return !!c && c.name === input.name;
    }
    async run(_s, input) {
      const block = findBlock(NAME_BLOCK2);
      if (!block) throw new Error("\u0411\u043B\u043E\u043A Ad set name \u043D\u0435 \u043D\u0430\u0439\u0434\u0435\u043D");
      const field = block.querySelector('input[type="text"]');
      if (!field) throw new Error("\u041F\u043E\u043B\u0435 \u0438\u043C\u0435\u043D\u0438 ad set \u043D\u0435 \u043D\u0430\u0439\u0434\u0435\u043D\u043E");
      await humanClick(field);
      field.select();
      await humanIdle(IdleRange.SHORT);
      await humanType(field, input.name);
      await humanIdle(IdleRange.BETWEEN_STEPS);
    }
  };

  // src/creator/steps/_helpers/tree-nav.ts
  function listTreeNodeNames(role) {
    const nodes = Array.from(
      document.querySelectorAll(
        `[data-tree-role="${role}"], [data-testid="${role}-node"]`
      )
    );
    return nodes.map((el) => (el.getAttribute("data-name") || el.textContent || "").trim()).filter(Boolean);
  }
  function findTreeNodeByName(role, name) {
    const target = normalizeText(name);
    const nodes = Array.from(
      document.querySelectorAll(
        `[data-tree-role="${role}"], [data-testid="${role}-node"]`
      )
    );
    for (const node of nodes) {
      const txt = normalizeText(node.getAttribute("data-name") || node.textContent || "");
      if (txt === target) return node;
    }
    return null;
  }

  // src/creator/steps/_helpers/tree-actions.ts
  function roleLabel(role) {
    return role === "ad" ? "\u041E\u0431\u044A\u044F\u0432\u043B\u0435\u043D\u0438\u0435" : "Ad set";
  }
  function createDuplicateStep(name, role) {
    return class extends BaseStep {
      name = name;
      detect() {
        return { kind: "matched", current: listTreeNodeNames(role) };
      }
      isSatisfied(state2, input) {
        const names = state2.current || [];
        return names.includes(input.newName);
      }
      async run(_s, input) {
        const node = findTreeNodeByName(role, input.sourceName);
        if (!node) {
          throw new Error(`${roleLabel(role)} "${input.sourceName}" \u043D\u0435 \u043D\u0430\u0439\u0434\u0435\u043D \u0432 \u0434\u0435\u0440\u0435\u0432\u0435`);
        }
        const menu = node.querySelector(
          'button[aria-haspopup="menu"], [data-testid="row-menu"]'
        ) ?? node;
        await humanClick(menu);
        await humanIdle(IdleRange.SHORT);
        const dup = findByAriaLabel(["\u0414\u0443\u0431\u043B\u0438\u0440\u043E\u0432\u0430\u0442\u044C", "Duplicate"]) ?? findByNormalizedText(["\u0434\u0443\u0431\u043B\u0438\u0440\u043E\u0432\u0430\u0442\u044C", "duplicate"]);
        if (!dup) throw new Error("\u041F\u0443\u043D\u043A\u0442 \u043C\u0435\u043D\u044E \xAB\u0414\u0443\u0431\u043B\u0438\u0440\u043E\u0432\u0430\u0442\u044C\xBB \u043D\u0435 \u043D\u0430\u0439\u0434\u0435\u043D");
        await humanClick(dup);
        await humanIdle(IdleRange.BETWEEN_STEPS);
        const nameInput = document.querySelector(
          'input[type="text"][name*="name"], [data-testid="duplicate-name"] input'
        );
        if (nameInput) {
          await humanClick(nameInput);
          nameInput.select();
          await humanIdle(IdleRange.SHORT);
          await humanType(nameInput, input.newName);
        }
        const confirm = findByAriaLabel(["\u0414\u0443\u0431\u043B\u0438\u0440\u043E\u0432\u0430\u0442\u044C", "Duplicate", "\u041F\u043E\u0434\u0442\u0432\u0435\u0440\u0434\u0438\u0442\u044C", "Confirm"]) ?? findByNormalizedText(["\u0434\u0443\u0431\u043B\u0438\u0440\u043E\u0432\u0430\u0442\u044C", "duplicate", "\u043F\u043E\u0434\u0442\u0432\u0435\u0440\u0434\u0438\u0442\u044C", "confirm"]);
        if (confirm) {
          await humanClick(confirm);
          await humanIdle(IdleRange.BETWEEN_STEPS);
        }
      }
    };
  }
  function createRenameStep(name, role) {
    return class extends BaseStep {
      name = name;
      detect() {
        return { kind: "matched", current: listTreeNodeNames(role) };
      }
      isSatisfied(state2, input) {
        const names = state2.current || [];
        return names.includes(input.to) && !names.includes(input.from);
      }
      async run(_s, input) {
        const node = findTreeNodeByName(role, input.from);
        if (!node) throw new Error(`${roleLabel(role)} "${input.from}" \u043D\u0435 \u043D\u0430\u0439\u0434\u0435\u043D`);
        await humanClick(node);
        await humanIdle(IdleRange.SHORT);
        await humanDoubleClick(node);
        await humanIdle(IdleRange.SHORT);
        const input2 = node.querySelector('input[type="text"]') ?? document.querySelector('[data-testid="rename-input"] input');
        if (!input2) throw new Error("\u041F\u043E\u043B\u0435 \u043F\u0435\u0440\u0435\u0438\u043C\u0435\u043D\u043E\u0432\u0430\u043D\u0438\u044F \u043D\u0435 \u043D\u0430\u0439\u0434\u0435\u043D\u043E");
        input2.select();
        await humanType(input2, input.to);
        await humanIdle(IdleRange.BETWEEN_STEPS);
      }
    };
  }

  // src/creator/steps/duplicate_adset.ts
  var DuplicateAdsetStep = createDuplicateStep("duplicate_adset", "adset");

  // src/creator/steps/duplicate_ad.ts
  var DuplicateAdStep = createDuplicateStep("duplicate_ad", "ad");

  // src/creator/steps/rename_adset.ts
  var RenameAdsetStep = createRenameStep("rename_adset", "adset");

  // src/creator/steps/rename_ad.ts
  var RenameAdStep = createRenameStep("rename_ad", "ad");

  // src/creator/steps/reattach_creative.ts
  var MEDIA_BLOCK = {
    testid: "media-section",
    aria: ["\u041C\u0435\u0434\u0438\u0430", "Media"],
    text: ["\u043C\u0435\u0434\u0438\u0430", "media"]
  };
  var ReattachCreativeStep = class extends BaseStep {
    name = "reattach_creative";
    detect() {
      const block = findBlock(MEDIA_BLOCK);
      const thumbs = block?.querySelectorAll('[data-testid="creative-thumb"]') ?? [];
      return { kind: "matched", current: thumbs.length };
    }
    isSatisfied(state2, input) {
      return state2.current === input.paths.length;
    }
    async run(_s, input, ctx) {
      const node = findTreeNodeByName("ad", input.adName);
      if (node) {
        await humanClick(node);
        await humanIdle(IdleRange.BETWEEN_STEPS);
      }
      const block = findBlock(MEDIA_BLOCK);
      if (!block) throw new Error("\u0411\u043B\u043E\u043A Media \u043D\u0435 \u043D\u0430\u0439\u0434\u0435\u043D");
      const fileInput = block.querySelector('input[type="file"]');
      if (!fileInput) throw new Error("input[type=file] \u043D\u0435 \u043D\u0430\u0439\u0434\u0435\u043D \u0432 \u0431\u043B\u043E\u043A\u0435 Media");
      const id = `reattach-${Date.now()}`;
      fileInput.setAttribute("data-fb-upload-id", id);
      ctx.emit("request_upload", {
        id,
        paths: input.paths,
        selector: `input[data-fb-upload-id="${id}"]`
      });
    }
  };

  // src/creator/steps/switch_to_adset.ts
  function currentAdsetName() {
    const sel = document.querySelector(
      '[data-tree-role="adset"][aria-selected="true"], [data-testid="adset-node"][aria-current="true"]'
    );
    if (!sel) return null;
    return (sel.getAttribute("data-name") || sel.textContent || "").trim();
  }
  var SwitchToAdsetStep = class extends BaseStep {
    name = "switch_to_adset";
    detect() {
      const cur = currentAdsetName();
      return cur ? { kind: "matched", current: cur } : { kind: "missing" };
    }
    isSatisfied(state2, input) {
      return state2.kind === "matched" && state2.current === input.name;
    }
    async run(_s, input) {
      const node = findTreeNodeByName("adset", input.name);
      if (!node) throw new Error(`Ad set "${input.name}" \u043D\u0435 \u043D\u0430\u0439\u0434\u0435\u043D \u0432 \u0434\u0435\u0440\u0435\u0432\u0435`);
      await humanClick(node);
      await humanIdle(IdleRange.BETWEEN_STEPS);
    }
  };

  // src/creator/steps/click_next.ts
  var ClickNextStep = class extends BaseStep {
    name = "click_next";
    detect() {
      return { kind: "matched" };
    }
    isSatisfied() {
      return false;
    }
    async run() {
      const btn = findByAriaLabel(["\u0414\u0430\u043B\u0435\u0435", "Next", "\u041F\u0440\u043E\u0434\u043E\u043B\u0436\u0438\u0442\u044C", "Continue"]) ?? findByNormalizedText(["\u0434\u0430\u043B\u0435\u0435", "next", "\u043F\u0440\u043E\u0434\u043E\u043B\u0436\u0438\u0442\u044C", "continue"]);
      if (!btn) throw new Error("\u041A\u043D\u043E\u043F\u043A\u0430 \xAB\u0414\u0430\u043B\u0435\u0435\xBB \u043D\u0435 \u043D\u0430\u0439\u0434\u0435\u043D\u0430");
      await humanClick(btn);
      await humanIdle(IdleRange.BETWEEN_SCENES);
    }
  };

  // src/creator/steps/save_draft.ts
  function hasSavedIndicator() {
    const aria = findByAriaLabel(["\u0421\u043E\u0445\u0440\u0430\u043D\u0435\u043D\u043E", "Saved", "\u0427\u0435\u0440\u043D\u043E\u0432\u0438\u043A \u0441\u043E\u0445\u0440\u0430\u043D\u0451\u043D"]);
    if (aria) return true;
    const text = findByNormalizedText(["\u0441\u043E\u0445\u0440\u0430\u043D\u0435\u043D\u043E", "saved", "\u0447\u0435\u0440\u043D\u043E\u0432\u0438\u043A \u0441\u043E\u0445\u0440\u0430\u043D\u0451\u043D"]);
    return !!text;
  }
  var SaveDraftStep = class extends BaseStep {
    name = "save_draft";
    detect() {
      return hasSavedIndicator() ? { kind: "matched", current: "saved" } : { kind: "missing" };
    }
    isSatisfied(state2) {
      return state2.kind === "matched" && state2.current === "saved";
    }
    async run() {
      const btn = findByAriaLabel(["\u0421\u043E\u0445\u0440\u0430\u043D\u0438\u0442\u044C \u0447\u0435\u0440\u043D\u043E\u0432\u0438\u043A", "Save draft", "\u0421\u043E\u0445\u0440\u0430\u043D\u0438\u0442\u044C"]) ?? findByNormalizedText(["\u0441\u043E\u0445\u0440\u0430\u043D\u0438\u0442\u044C \u0447\u0435\u0440\u043D\u043E\u0432\u0438\u043A", "save draft"]);
      if (!btn) throw new Error("\u041A\u043D\u043E\u043F\u043A\u0430 \xAB\u0421\u043E\u0445\u0440\u0430\u043D\u0438\u0442\u044C \u0447\u0435\u0440\u043D\u043E\u0432\u0438\u043A\xBB \u043D\u0435 \u043D\u0430\u0439\u0434\u0435\u043D\u0430");
      await humanClick(btn);
      await humanIdle(IdleRange.BETWEEN_SCENES);
    }
  };

  // src/creator/steps/unknown.ts
  var UnknownStep = class extends BaseStep {
    name = "unknown";
    detect() {
      return { kind: "unknown" };
    }
    isSatisfied() {
      return false;
    }
    async run(_s, input, _ctx) {
      throw new Error(
        `UnimplementedStepError: \u0437\u0430\u043F\u0438\u0448\u0438 \u043D\u043E\u0432\u044B\u0439 \u0448\u0430\u0433 \u0434\u043B\u044F raw=${JSON.stringify(input.raw)}`
      );
    }
  };

  // src/creator/steps/index.ts
  var STEPS = [
    new SetConversionLocationStep(),
    new SetPixelEventStep(),
    new SetOptimizationGoalStep(),
    new SetAttributionStep(),
    new SetCtaStep(),
    new SetGeoStep(),
    new SetAgeStep(),
    new SetBudgetStep(),
    new SetScheduleStartStep(),
    new SetTrackingUrlStep(),
    new FillTextsStep(),
    new UploadCreativesStep(),
    new CreateCampaignStep(),
    new CreateAdsetStep(),
    new DuplicateAdsetStep(),
    new DuplicateAdStep(),
    new RenameAdsetStep(),
    new RenameAdStep(),
    new ReattachCreativeStep(),
    new SwitchToAdsetStep(),
    new ClickNextStep(),
    new SaveDraftStep(),
    new UnknownStep()
  ];
  for (const s of STEPS) registerStep(s);

  // src/creator/executor.ts
  var TEMPLATE_RE = /\{\{\s*([\w.]+)\s*\}\}/g;
  function resolvePath(obj, path) {
    return path.split(".").reduce((acc, key) => {
      if (acc && typeof acc === "object" && key in acc) {
        return acc[key];
      }
      return void 0;
    }, obj);
  }
  function interpolate(input, vars) {
    if (typeof input === "string") {
      return input.replace(TEMPLATE_RE, (_, p) => {
        const v = resolvePath(vars, p);
        return v == null ? "" : String(v);
      });
    }
    if (Array.isArray(input)) {
      return input.map((x) => interpolate(x, vars));
    }
    if (input && typeof input === "object") {
      const out = {};
      for (const [k, v] of Object.entries(input)) {
        out[k] = interpolate(v, vars);
      }
      return out;
    }
    return input;
  }
  async function runPlan(plan, variables, emit) {
    const ctx = { variables, emit };
    for (const step of plan.steps) {
      const impl = getStep(step.step);
      if (!impl) {
        emit("step_failed", { step: step.step, error: "unknown step" });
        return { ok: false, error: `unknown step: ${step.step}` };
      }
      const input = interpolate(step.input, variables);
      emit("step_started", { step: step.step });
      try {
        const state2 = await impl.detect(ctx);
        await impl.execute(state2, input, ctx);
        emit("step_finished", { step: step.step });
      } catch (e) {
        const msg = String(e?.message ?? e);
        emit("step_failed", { step: step.step, error: msg });
        return { ok: false, error: msg };
      }
      await humanIdle(IdleRange.BETWEEN_STEPS);
    }
    return { ok: true };
  }

  // src/creator/recorder.ts
  var state = {
    active: false,
    planName: "",
    recordedSteps: [],
    inputDebounceMs: 800,
    inputTimer: null,
    pendingInput: null,
    handlers: null
  };
  function getSelector(el) {
    if (!el) return "";
    const testid = el.getAttribute?.("data-testid");
    if (testid) return `[data-testid="${testid}"]`;
    const surface = el.getAttribute?.("data-surface");
    if (surface) return `[data-surface="${surface}"]`;
    const role = el.getAttribute?.("role");
    const aria = el.getAttribute?.("aria-label");
    if (role && aria) return `[role="${role}"][aria-label="${CSS.escape(aria)}"]`;
    if (aria) return `[aria-label="${CSS.escape(aria)}"]`;
    const tag = el.tagName?.toLowerCase() ?? "";
    const id = el.id ? `#${el.id}` : "";
    return `${tag}${id}`;
  }
  function toRecordedEvent(type, ev) {
    const target = ev.target;
    const value = (() => {
      if (target instanceof HTMLInputElement) return target.value;
      if (target instanceof HTMLTextAreaElement) return target.value;
      if (target instanceof HTMLSelectElement) return target.value;
      return null;
    })();
    const text = (target?.textContent || "").trim().slice(0, 200);
    return {
      type,
      selector: target ? getSelector(target) : "",
      text,
      value
    };
  }
  function getDomState() {
    return {
      url: typeof location !== "undefined" ? location.href : "",
      title: typeof document !== "undefined" ? document.title : ""
    };
  }
  function dispatch(recordedEvent) {
    if (!state.active) return;
    const dom = getDomState();
    for (const step of listSteps()) {
      if (typeof step.match !== "function") continue;
      let matched = false;
      try {
        matched = step.match(recordedEvent, dom);
      } catch {
        continue;
      }
      if (matched) {
        const planStep = { step: step.name, input: {} };
        const last = state.recordedSteps[state.recordedSteps.length - 1];
        if (last && last.step === planStep.step) {
          return;
        }
        state.recordedSteps.push(planStep);
        return;
      }
    }
  }
  function flushPendingInput() {
    if (state.pendingInput) {
      dispatch(toRecordedEvent("input", state.pendingInput.ev));
      state.pendingInput = null;
    }
    if (state.inputTimer) {
      clearTimeout(state.inputTimer);
      state.inputTimer = null;
    }
  }
  function onClick(ev) {
    flushPendingInput();
    dispatch(toRecordedEvent("click", ev));
  }
  function onInput(ev) {
    state.pendingInput = { target: ev.target, ev };
    if (state.inputTimer) clearTimeout(state.inputTimer);
    state.inputTimer = setTimeout(() => {
      flushPendingInput();
    }, state.inputDebounceMs);
  }
  function onChange(ev) {
    flushPendingInput();
    dispatch(toRecordedEvent("change", ev));
  }
  function startRecording(planName) {
    if (state.active) {
      throw new Error("recorder \u0443\u0436\u0435 \u0437\u0430\u043F\u0443\u0449\u0435\u043D");
    }
    state.active = true;
    state.planName = planName;
    state.recordedSteps = [];
    state.handlers = {
      click: onClick,
      input: onInput,
      change: onChange
    };
    if (typeof document !== "undefined") {
      document.addEventListener("click", state.handlers.click, true);
      document.addEventListener("input", state.handlers.input, true);
      document.addEventListener("change", state.handlers.change, true);
    }
  }
  function stopRecording() {
    if (!state.active) {
      throw new Error("recorder \u043D\u0435 \u0437\u0430\u043F\u0443\u0449\u0435\u043D");
    }
    flushPendingInput();
    if (state.handlers && typeof document !== "undefined") {
      document.removeEventListener("click", state.handlers.click, true);
      document.removeEventListener("input", state.handlers.input, true);
      document.removeEventListener("change", state.handlers.change, true);
    }
    state.active = false;
    state.handlers = null;
    return { planName: state.planName, steps: [...state.recordedSteps] };
  }
  function getStatus() {
    return {
      active: state.active,
      planName: state.planName,
      recordedSteps: state.recordedSteps.length
    };
  }

  // src/creator/index.ts
  var VERSION = "2.0.0";
  var api = {
    version: VERSION,
    async run(plan, variables) {
      const emit = (event, payload) => {
        const fn = globalThis.fbAgentEmit;
        if (typeof fn === "function") fn(event, payload);
      };
      return runPlan(plan, variables, emit);
    },
    async startRecording(planName) {
      startRecording(planName);
    },
    async stopRecording() {
      return stopRecording();
    },
    getRecorderStatus() {
      return getStatus();
    }
  };
  globalThis.window = globalThis.window ?? {};
  globalThis.window.__fbAgent = api;
})();
