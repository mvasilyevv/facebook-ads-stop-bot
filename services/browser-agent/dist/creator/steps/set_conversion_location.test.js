"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
const node_test_1 = require("node:test");
const node_assert_1 = __importDefault(require("node:assert"));
const set_conversion_location_js_1 = require("./set_conversion_location.js");
const index_js_1 = require("../enums/index.js");
// Идемпотентность: true при совпадении, false при отличии.
(0, node_test_1.describe)('SetConversionLocationStep', () => {
    (0, node_test_1.it)('isSatisfied true когда current === input.value', () => {
        const s = new set_conversion_location_js_1.SetConversionLocationStep();
        node_assert_1.default.equal(s.isSatisfied({ kind: 'matched', current: index_js_1.ConversionLocation.WEBSITE }, { value: index_js_1.ConversionLocation.WEBSITE }), true);
    });
    (0, node_test_1.it)('isSatisfied false при отличии', () => {
        const s = new set_conversion_location_js_1.SetConversionLocationStep();
        node_assert_1.default.equal(s.isSatisfied({ kind: 'matched', current: index_js_1.ConversionLocation.APP }, { value: index_js_1.ConversionLocation.WEBSITE }), false);
    });
});
//# sourceMappingURL=set_conversion_location.test.js.map