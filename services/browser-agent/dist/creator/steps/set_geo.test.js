"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
const node_test_1 = require("node:test");
const node_assert_1 = __importDefault(require("node:assert"));
const set_geo_js_1 = require("./set_geo.js");
// Идемпотентность: true когда все требуемые страны уже выбраны.
(0, node_test_1.describe)('SetGeoStep', () => {
    (0, node_test_1.it)('isSatisfied когда current содержит все требуемые страны', () => {
        const s = new set_geo_js_1.SetGeoStep();
        node_assert_1.default.equal(s.isSatisfied({ kind: 'matched', current: ['DE', 'AT'] }, { countries: ['DE'] }), true);
        node_assert_1.default.equal(s.isSatisfied({ kind: 'matched', current: ['DE'] }, { countries: ['DE', 'AT'] }), false);
    });
});
//# sourceMappingURL=set_geo.test.js.map