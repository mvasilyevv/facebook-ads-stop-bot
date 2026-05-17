import { describe, it } from 'node:test';
import assert from 'node:assert';
import { CreateCampaignStep } from './create_campaign.js';
import { Objective } from '../enums/index.js';

// Идемпотентность создания кампании — по имени.
describe('CreateCampaignStep', () => {
  it('isSatisfied при совпадении имени', () => {
    const s = new CreateCampaignStep();
    assert.equal(
      s.isSatisfied(
        { kind: 'matched', current: { name: 'CR2 | DRC | MV' } },
        { name: 'CR2 | DRC | MV', objective: Objective.SALES },
      ),
      true,
    );
    assert.equal(
      s.isSatisfied(
        { kind: 'matched', current: { name: 'other' } },
        { name: 'CR2 | DRC | MV', objective: Objective.SALES },
      ),
      false,
    );
  });
});
