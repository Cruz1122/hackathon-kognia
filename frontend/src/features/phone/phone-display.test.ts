import { strict as assert } from 'node:assert';
import test from 'node:test';
import { parsePhoneDisplay, phoneDisplayMarkup } from './phone-display.ts';

test('resolves Colombian international numbers to a flag and national format', () => {
  const display = parsePhoneDisplay('+573001234567');
  assert.equal(display.country, 'co');
  assert.equal(display.national, '300 1234567');
  assert.match(phoneDisplayMarkup('+573001234567'), /fi fi-co/);
  assert.doesNotMatch(phoneDisplayMarkup('+573001234567'), /\+57/);
});

test('keeps unparseable values safe without inventing a flag', () => {
  const markup = phoneDisplayMarkup('ext <42>');
  assert.match(markup, /&lt;42&gt;/);
  assert.doesNotMatch(markup, /fi fi-/);
});
