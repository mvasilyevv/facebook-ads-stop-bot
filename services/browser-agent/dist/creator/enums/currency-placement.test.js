"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
const node_test_1 = require("node:test");
const node_assert_1 = __importDefault(require("node:assert"));
const currency_js_1 = require("./currency.js");
const placement_js_1 = require("./placement.js");
(0, node_test_1.describe)('Currency', () => {
    (0, node_test_1.it)('содержит набор поддерживаемых валют', () => {
        node_assert_1.default.deepEqual(Object.values(currency_js_1.Currency).sort(), ['EUR', 'RUB', 'UAH', 'USD']);
    });
});
(0, node_test_1.describe)('Placement', () => {
    (0, node_test_1.it)('у каждого enum есть ru и en синонимы', () => {
        for (const k of Object.values(placement_js_1.Placement)) {
            const labels = placement_js_1.placementLabels[k];
            node_assert_1.default.ok(labels.ru.length > 0 && labels.en.length > 0, `нет синонимов для ${k}`);
        }
    });
});
//# sourceMappingURL=currency-placement.test.js.map