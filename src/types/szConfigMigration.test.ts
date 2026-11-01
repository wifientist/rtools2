// Run with: npx tsx src/types/szConfigMigration.test.ts
import assert from 'node:assert/strict';
import { INITIAL_STATE, wizardReducer, type WizardAction, type WizardState } from './szConfigMigration';

const play = (actions: WizardAction[], from: WizardState = INITIAL_STATE) => actions.reduce(wizardReducer, from);

const midRun = play([
  { type: 'SET_SOURCE', szControllerId: 1, domainId: 'd1', domainName: 'Domain 1' },
  { type: 'SET_ZONE', zoneId: 'z1', zoneName: 'Zone 1' },
  { type: 'START_EXTRACTION', jobId: 'ex1' },
  { type: 'SET_SESSION_ID', sessionId: 7 },
  { type: 'SET_DESTINATION', r1ControllerId: 2, tenantId: 't1', venueId: 'v1', venueName: 'Venue 1' },
  { type: 'SET_PLAN', jobId: 'plan1', result: {} as any },
  { type: 'SET_STEP', step: 5 },
]);
assert.equal(midRun.destVenueId, 'v1');

// A different zone drops the run, keeping only the source just chosen.
const newZone = play([{ type: 'SET_ZONE', zoneId: 'z2', zoneName: 'Zone 2' }], midRun);
assert.equal(newZone.selectedZoneId, 'z2');
assert.equal(newZone.selectedDomainId, 'd1');
assert.equal(newZone.szControllerId, 1);
assert.equal(newZone.destVenueId, null);
assert.equal(newZone.destVenueName, null);
assert.equal(newZone.extractionJobId, null);
assert.equal(newZone.planJobId, null);
assert.equal(newZone.sessionId, null);
assert.equal(newZone.currentStep, 1);
assert.equal(newZone.highestStepReached, 1);
assert.equal(newZone.runId, midRun.runId + 1);

// The selectors report the same pick again on returning to Step 1: not a new run.
const sameDomainThenZone = play([
  { type: 'SET_SOURCE', szControllerId: 1, domainId: 'd1', domainName: 'Domain 1' },
  { type: 'SET_ZONE', zoneId: 'z1', zoneName: 'Zone 1' },
], midRun);
assert.equal(sameDomainThenZone.destVenueId, 'v1');
assert.equal(sameDomainThenZone.sessionId, 7);
assert.equal(sameDomainThenZone.runId, midRun.runId);

// Same domain, zone cleared by the selector, then a different zone: still caught.
const viaCleared = play([
  { type: 'SET_SOURCE', szControllerId: 1, domainId: 'd1', domainName: 'Domain 1' },
  { type: 'SET_ZONE', zoneId: 'z2', zoneName: 'Zone 2' },
], midRun);
assert.equal(viaCleared.destVenueId, null);

// A different domain drops the run at once.
const newDomain = play([{ type: 'SET_SOURCE', szControllerId: 1, domainId: 'd2', domainName: 'Domain 2' }], midRun);
assert.equal(newDomain.selectedDomainId, 'd2');
assert.equal(newDomain.selectedZoneId, null);
assert.equal(newDomain.destVenueId, null);
assert.equal(newDomain.currentStep, 1);

// Picking a zone before any extraction is not a new run.
const browsing = play([
  { type: 'SET_SOURCE', szControllerId: 1, domainId: 'd1', domainName: 'Domain 1' },
  { type: 'SET_ZONE', zoneId: 'z1', zoneName: 'Zone 1' },
  { type: 'SET_ZONE', zoneId: 'z2', zoneName: 'Zone 2' },
]);
assert.equal(browsing.runId, 0);
assert.equal(browsing.selectedDomainId, 'd1');

// Re-extracting the same zone keeps the destination.
const reExtract = play([{ type: 'START_EXTRACTION', jobId: 'ex2' }], midRun);
assert.equal(reExtract.destVenueId, 'v1');

// RESET clears everything and marks a new run.
const reset = play([{ type: 'RESET' }], midRun);
assert.deepEqual(reset, { ...INITIAL_STATE, runId: midRun.runId + 1 });

console.log('szConfigMigration reducer: ok');
