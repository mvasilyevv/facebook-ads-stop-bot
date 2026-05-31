import assert from 'node:assert/strict';
import test from 'node:test';

import { parseOwnerTags, campaignMatchesOwner, resolveOwnerCampaignIds } from './am-owner.js';

// parseOwnerTags: CSV/точка-с-запятой → список, пусто → [].
test('parseOwnerTags: разбор тегов', () => {
  assert.deepEqual(parseOwnerTags('MV'), ['MV']);
  assert.deepEqual(parseOwnerTags('MV,ABC'), ['MV', 'ABC']);
  assert.deepEqual(parseOwnerTags('MV; ABC '), ['MV', 'ABC']);
  assert.deepEqual(parseOwnerTags(''), []);
  assert.deepEqual(parseOwnerTags(null), []);
});

// campaignMatchesOwner: те же боевые имена, что проверяли live (MV→in, чужие→out).
test('campaignMatchesOwner: боевые имена кабинета', () => {
  // многострочные имена Meta (как отдаёт campaigns edge)
  assert.equal(campaignMatchesOwner('MV\nKE\nCR2\nadset.pro\n31.05\n2', 'MV'), true);
  assert.equal(campaignMatchesOwner('MV\nGH\nCR2\nadset.pro\n22.05\n1', 'MV'), true);
  // чужие команды — НЕ матчатся
  assert.equal(campaignMatchesOwner('28.05 MZ Artemteam test MZF CBO 1-3-5', 'MV'), false);
  assert.equal(campaignMatchesOwner('ls_aviator_ivan_team_05_28', 'MV'), false);
  assert.equal(campaignMatchesOwner('28 05 GH chiken yakim', 'MV'), false);
});

// campaignMatchesOwner: word-boundary — тег внутри слова НЕ матчит (как _owner_tag_pattern).
test('campaignMatchesOwner: граница слова', () => {
  assert.equal(campaignMatchesOwner('AMVB campaign', 'MV'), false); // mv внутри слова
  assert.equal(campaignMatchesOwner('xMV', 'MV'), false); // префикс-буква
  assert.equal(campaignMatchesOwner('MV | KE', 'mv'), true); // case-insensitive
  assert.equal(campaignMatchesOwner('foo MV bar', 'MV'), true); // пробелы — граница
});

// campaignMatchesOwner: несколько тегов — совпадение с любым; пустой тег → все True.
test('campaignMatchesOwner: мульти-тег и выключенный фильтр', () => {
  assert.equal(campaignMatchesOwner('ABC | promo', 'MV,ABC'), true);
  assert.equal(campaignMatchesOwner('XYZ | promo', 'MV,ABC'), false);
  assert.equal(campaignMatchesOwner('что угодно', ''), true); // фильтр выключен
  assert.equal(campaignMatchesOwner('что угодно', null), true);
});

// resolveOwnerCampaignIds: имена кампаний → id только своих; пустой тег → [] (без резолва).
test('resolveOwnerCampaignIds: отбор id по owner_tag', () => {
  const camps = [
    { id: '1', name: 'MV | KE | CR2 | 31.05' },
    { id: '2', name: 'MV | GH | CR2 | 22.05' },
    { id: '3', name: '28.05 MZ Artemteam test' },
    { id: '4', name: 'ls_aviator_ivan_team' },
  ];
  assert.deepEqual(resolveOwnerCampaignIds(camps, 'MV'), ['1', '2']);
  assert.deepEqual(resolveOwnerCampaignIds(camps, ''), []); // фильтр выключен → без резолва
  assert.deepEqual(resolveOwnerCampaignIds([], 'MV'), []);
});
