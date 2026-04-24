"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
const strict_1 = __importDefault(require("node:assert/strict"));
const node_test_1 = __importDefault(require("node:test"));
const parser_js_1 = require("./parser.js");
// Сценарий: денежные значения с русскими и английскими разделителями тысяч не занижаются.
(0, node_test_1.default)('parseMoney корректно нормализует разделители тысяч и дробной части', () => {
    const cases = [
        ['1 234,56', '1234.56'],
        ['1,234.56', '1234.56'],
        ['$1,234.56', '1234.56'],
        ['€1.234,56', '1234.56'],
        ['1,234', '1234'],
        ['1 234', '1234'],
        ['1.234', '1234'],
        ['1234,56', '1234.56'],
        ['1 234 567,89', '1234567.89'],
    ];
    for (const [input, expected] of cases) {
        strict_1.default.equal((0, parser_js_1.parseMoney)(input), expected);
    }
});
// Сценарий: nullable money parser возвращает null для пустых значений и число для валютных строк.
(0, node_test_1.default)('parseMoneyOrNull различает пустые и валидные денежные значения', () => {
    strict_1.default.equal((0, parser_js_1.parseMoneyOrNull)('—'), null);
    strict_1.default.equal((0, parser_js_1.parseMoneyOrNull)('--'), null);
    strict_1.default.equal((0, parser_js_1.parseMoneyOrNull)('$1,234.56'), '1234.56');
});
// Сценарий: целочисленные метрики с разделителями тысяч читаются как тысячи, а не как единицы.
(0, node_test_1.default)('parseIntValue корректно читает целые метрики с разделителями тысяч', () => {
    strict_1.default.equal((0, parser_js_1.parseIntValue)('1,234'), 1234);
    strict_1.default.equal((0, parser_js_1.parseIntValue)('1 234'), 1234);
    strict_1.default.equal((0, parser_js_1.parseIntValue)('1.234'), 1234);
    strict_1.default.equal((0, parser_js_1.parseIntValue)('12,345'), 12345);
    strict_1.default.equal((0, parser_js_1.parseIntValue)('1 234 567'), 1234567);
});
// Сценарий: десятичные метрики сохраняют дробную часть для русской и английской локали.
(0, node_test_1.default)('parseDecimalOrNull сохраняет дробную часть десятичных метрик', () => {
    strict_1.default.equal((0, parser_js_1.parseDecimalOrNull)('12,34%'), '12.34');
    strict_1.default.equal((0, parser_js_1.parseDecimalOrNull)('12.34%'), '12.34');
    strict_1.default.equal((0, parser_js_1.parseDecimalOrNull)('1,234'), '1234');
});
//# sourceMappingURL=parser-number.test.js.map