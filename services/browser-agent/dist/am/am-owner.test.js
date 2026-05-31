"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
const strict_1 = __importDefault(require("node:assert/strict"));
const node_test_1 = __importDefault(require("node:test"));
const am_owner_js_1 = require("./am-owner.js");
// parseOwnerTags: CSV/точка-с-запятой → список, пусто → [].
(0, node_test_1.default)('parseOwnerTags: разбор тегов', () => {
    strict_1.default.deepEqual((0, am_owner_js_1.parseOwnerTags)('MV'), ['MV']);
    strict_1.default.deepEqual((0, am_owner_js_1.parseOwnerTags)('MV,ABC'), ['MV', 'ABC']);
    strict_1.default.deepEqual((0, am_owner_js_1.parseOwnerTags)('MV; ABC '), ['MV', 'ABC']);
    strict_1.default.deepEqual((0, am_owner_js_1.parseOwnerTags)(''), []);
    strict_1.default.deepEqual((0, am_owner_js_1.parseOwnerTags)(null), []);
});
// campaignMatchesOwner: те же боевые имена, что проверяли live (MV→in, чужие→out).
(0, node_test_1.default)('campaignMatchesOwner: боевые имена кабинета', () => {
    // многострочные имена Meta (как отдаёт campaigns edge)
    strict_1.default.equal((0, am_owner_js_1.campaignMatchesOwner)('MV\nKE\nCR2\nadset.pro\n31.05\n2', 'MV'), true);
    strict_1.default.equal((0, am_owner_js_1.campaignMatchesOwner)('MV\nGH\nCR2\nadset.pro\n22.05\n1', 'MV'), true);
    // чужие команды — НЕ матчатся
    strict_1.default.equal((0, am_owner_js_1.campaignMatchesOwner)('28.05 MZ Artemteam test MZF CBO 1-3-5', 'MV'), false);
    strict_1.default.equal((0, am_owner_js_1.campaignMatchesOwner)('ls_aviator_ivan_team_05_28', 'MV'), false);
    strict_1.default.equal((0, am_owner_js_1.campaignMatchesOwner)('28 05 GH chiken yakim', 'MV'), false);
});
// campaignMatchesOwner: word-boundary — тег внутри слова НЕ матчит (как _owner_tag_pattern).
(0, node_test_1.default)('campaignMatchesOwner: граница слова', () => {
    strict_1.default.equal((0, am_owner_js_1.campaignMatchesOwner)('AMVB campaign', 'MV'), false); // mv внутри слова
    strict_1.default.equal((0, am_owner_js_1.campaignMatchesOwner)('xMV', 'MV'), false); // префикс-буква
    strict_1.default.equal((0, am_owner_js_1.campaignMatchesOwner)('MV | KE', 'mv'), true); // case-insensitive
    strict_1.default.equal((0, am_owner_js_1.campaignMatchesOwner)('foo MV bar', 'MV'), true); // пробелы — граница
});
// campaignMatchesOwner: несколько тегов — совпадение с любым; пустой тег → все True.
(0, node_test_1.default)('campaignMatchesOwner: мульти-тег и выключенный фильтр', () => {
    strict_1.default.equal((0, am_owner_js_1.campaignMatchesOwner)('ABC | promo', 'MV,ABC'), true);
    strict_1.default.equal((0, am_owner_js_1.campaignMatchesOwner)('XYZ | promo', 'MV,ABC'), false);
    strict_1.default.equal((0, am_owner_js_1.campaignMatchesOwner)('что угодно', ''), true); // фильтр выключен
    strict_1.default.equal((0, am_owner_js_1.campaignMatchesOwner)('что угодно', null), true);
});
// resolveOwnerCampaignIds: имена кампаний → id только своих; пустой тег → [] (без резолва).
(0, node_test_1.default)('resolveOwnerCampaignIds: отбор id по owner_tag', () => {
    const camps = [
        { id: '1', name: 'MV | KE | CR2 | 31.05' },
        { id: '2', name: 'MV | GH | CR2 | 22.05' },
        { id: '3', name: '28.05 MZ Artemteam test' },
        { id: '4', name: 'ls_aviator_ivan_team' },
    ];
    strict_1.default.deepEqual((0, am_owner_js_1.resolveOwnerCampaignIds)(camps, 'MV'), ['1', '2']);
    strict_1.default.deepEqual((0, am_owner_js_1.resolveOwnerCampaignIds)(camps, ''), []); // фильтр выключен → без резолва
    strict_1.default.deepEqual((0, am_owner_js_1.resolveOwnerCampaignIds)([], 'MV'), []);
});
//# sourceMappingURL=am-owner.test.js.map