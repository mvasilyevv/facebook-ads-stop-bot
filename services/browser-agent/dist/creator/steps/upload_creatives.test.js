"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
const node_test_1 = require("node:test");
const node_assert_1 = __importDefault(require("node:assert"));
const upload_creatives_js_1 = require("./upload_creatives.js");
// Идемпотентность: совпадает ли число загруженных превью с числом path-ов.
(0, node_test_1.describe)('UploadCreativesStep', () => {
    (0, node_test_1.it)('isSatisfied при равенстве кол-ва превью и paths', () => {
        const s = new upload_creatives_js_1.UploadCreativesStep();
        node_assert_1.default.equal(s.isSatisfied({ kind: 'matched', current: 2 }, { paths: ['a.jpg', 'b.jpg'] }), true);
        node_assert_1.default.equal(s.isSatisfied({ kind: 'matched', current: 0 }, { paths: ['a.jpg'] }), false);
    });
});
//# sourceMappingURL=upload_creatives.test.js.map