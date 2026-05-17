"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
const node_test_1 = require("node:test");
const node_assert_1 = __importDefault(require("node:assert"));
const create_campaign_js_1 = require("./create_campaign.js");
const index_js_1 = require("../enums/index.js");
// Идемпотентность создания кампании — по имени.
(0, node_test_1.describe)('CreateCampaignStep', () => {
    (0, node_test_1.it)('isSatisfied при совпадении имени', () => {
        const s = new create_campaign_js_1.CreateCampaignStep();
        node_assert_1.default.equal(s.isSatisfied({ kind: 'matched', current: { name: 'CR2 | DRC | MV' } }, { name: 'CR2 | DRC | MV', objective: index_js_1.Objective.SALES }), true);
        node_assert_1.default.equal(s.isSatisfied({ kind: 'matched', current: { name: 'other' } }, { name: 'CR2 | DRC | MV', objective: index_js_1.Objective.SALES }), false);
    });
});
//# sourceMappingURL=create_campaign.test.js.map