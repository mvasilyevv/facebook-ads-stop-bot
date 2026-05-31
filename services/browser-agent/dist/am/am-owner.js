"use strict";
// Owner-scoping матчер — ТОЧНОЕ зеркало Python core/observer/queries.py
// (parse_owner_tags / _owner_tag_pattern / campaign_matches_owner). Money-критично:
// резолвит owner_tag → campaign.id, чтобы am_tabular тянул сразу только свой скоуп
// (а не весь общий кабинет). Расхождение с Python = неверный скоуп фетча → покрыто
// тестом на тех же боевых именах. Пайплайн дополнительно фильтрует — defense in depth.
Object.defineProperty(exports, "__esModule", { value: true });
exports.parseOwnerTags = parseOwnerTags;
exports.campaignMatchesOwner = campaignMatchesOwner;
exports.resolveOwnerCampaignIds = resolveOwnerCampaignIds;
// CSV owner-тегов → список непустых тегов. Разделители — запятая/точка-с-запятой.
function parseOwnerTags(raw) {
    if (!raw)
        return [];
    return raw
        .replace(/;/g, ',')
        .split(',')
        .map((t) => t.trim())
        .filter((t) => t.length > 0);
}
function escapeRegExp(s) {
    return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}
// Word-boundary regex как Python _owner_tag_pattern: (?<![a-z0-9])tag(?![a-z0-9]).
function ownerTagPattern(tagLower) {
    return new RegExp(`(?<![a-z0-9])${escapeRegExp(tagLower)}(?![a-z0-9])`);
}
// True если имя кампании содержит любой owner-тег (word-boundary, case-insensitive).
// Пусто/null owner_tag → True (фильтр выключен). ВНИМАНИЕ: матчит ТОЛЬКО campaign_name
// (резолвер работает на уровне кампаний); ad_name остаётся за Python-пайплайном.
function campaignMatchesOwner(campaignName, ownerTag) {
    const tags = parseOwnerTags(ownerTag);
    if (!tags.length)
        return true;
    const hay = (campaignName || '').toLowerCase();
    return tags.some((tag) => ownerTagPattern(tag.toLowerCase()).test(hay));
}
// Резолв campaign.id по owner_tag: имена кампаний → id тех, что принадлежат владельцу.
function resolveOwnerCampaignIds(campaigns, ownerTag) {
    if (!parseOwnerTags(ownerTag).length)
        return [];
    return campaigns.filter((c) => campaignMatchesOwner(c.name ?? '', ownerTag)).map((c) => c.id);
}
//# sourceMappingURL=am-owner.js.map