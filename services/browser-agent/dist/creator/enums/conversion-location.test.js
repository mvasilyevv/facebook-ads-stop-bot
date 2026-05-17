"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
const node_test_1 = require("node:test");
const node_assert_1 = __importDefault(require("node:assert"));
const conversion_location_js_1 = require("./conversion-location.js");
// Проверяем enum значений и наличие синонимов на двух языках.
(0, node_test_1.describe)('ConversionLocation', () => {
    (0, node_test_1.it)('перечисляет все ожидаемые значения', () => {
        node_assert_1.default.deepEqual(Object.values(conversion_location_js_1.ConversionLocation).sort(), ['APP', 'MESSENGER', 'WEBSITE', 'WEBSITE_AND_CALLS']);
    });
    (0, node_test_1.it)('у каждого enum есть ru и en синонимы', () => {
        for (const k of Object.values(conversion_location_js_1.ConversionLocation)) {
            const labels = conversion_location_js_1.conversionLocationLabels[k];
            node_assert_1.default.ok(labels.ru.length > 0 && labels.en.length > 0, `нет синонимов для ${k}`);
        }
    });
});
//# sourceMappingURL=conversion-location.test.js.map