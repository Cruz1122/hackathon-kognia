import type { AgentSignalsEnvelope } from './types';

export const DEMO_FRAMES: readonly AgentSignalsEnvelope[] = [
  { signals: {
    satisfaction: { value: 'very_high', probabilities: { neutral: .02, high: .13, very_high: .85 } },
    frustration: { value: 'very_low', probabilities: { very_low: .86, low: .11, neutral: .03 } },
    confirmation: { value: 'explicit', probabilities: { rejected: .02, uncertain: .08, explicit: .90 } },
    integrity: { value: 'supported', probabilities: { unsupported: .01, uncertain: .02, supported: .97 } },
    intent: { value: 'continue', probabilities: { continue: .88, correct: .03, callback: .04, human: .02, unknown: .03 } },
    human: { value: 'not_requested', probabilities: { requested: .01, not_requested: .97, unknown: .02 } },
    schedule_flexibility: { value: 'flexible', probabilities: { flexible: .82, fixed: .13, unknown: .05 } },
  } },
  { signals: {
    satisfaction: { value: 'neutral', probabilities: { very_low: .03, low: .16, neutral: .60, high: .17, very_high: .04 } },
    frustration: { value: 'neutral', probabilities: { very_low: .09, low: .24, neutral: .48, high: .15, very_high: .04 } },
    integrity: { value: 'uncertain', probabilities: { unsupported: .11, uncertain: .68, supported: .21 } },
    intent: { value: 'correct', probabilities: { continue: .28, correct: .52, cancel: .01, callback: .05, human: .04, unknown: .10 } },
    human: { value: 'not_requested', probabilities: { requested: .08, not_requested: .84, unknown: .08 } },
    schedule_flexibility: { value: 'unknown', probabilities: { flexible: .34, fixed: .31, unknown: .35 } },
  } },
  { signals: {
    satisfaction: { value: 'very_low', probabilities: { very_low: .91, low: .07, neutral: .02 } },
    frustration: { value: 'very_high', probabilities: { low: .01, neutral: .03, high: .08, very_high: .88 } },
    confirmation: { value: 'rejected', probabilities: { rejected: .86, uncertain: .12, explicit: .02 } },
    integrity: { value: 'unsupported', probabilities: { unsupported: .78, uncertain: .18, supported: .04 } },
    intent: { value: 'human', probabilities: { continue: .13, correct: .14, cancel: .06, callback: .08, human: .55, unknown: .04 } },
    human: { value: 'requested', probabilities: { requested: .79, not_requested: .16, unknown: .05 } },
    schedule_flexibility: { value: 'fixed', probabilities: { flexible: .18, fixed: .74, unknown: .08 } },
  } },
  { signals: {
    satisfaction: { value: 'high', probabilities: { very_low: .01, low: .05, neutral: .13, high: .64, very_high: .17 } },
    frustration: { value: 'low', probabilities: { very_low: .36, low: .49, neutral: .10, high: .02, very_high: .03 } },
    confirmation: { value: 'explicit', probabilities: { rejected: .03, uncertain: .13, explicit: .84 } },
    intent: { value: 'continue', probabilities: { continue: .79, correct: .04, callback: .08, human: .03, unknown: .06 } },
    human: { value: 'not_requested', probabilities: { requested: .04, not_requested: .92, unknown: .04 } },
    schedule_flexibility: { value: 'fixed', probabilities: { flexible: .27, fixed: .67, unknown: .06 } },
  } },
  { signals: {
    satisfaction: { value: 'very_high', probabilities: { low: .01, neutral: .05, high: .22, very_high: .72 } },
    frustration: { value: 'very_low', probabilities: { very_low: .77, low: .18, neutral: .03, very_high: .02 } },
    confirmation: { value: 'explicit', probabilities: { rejected: .01, uncertain: .07, explicit: .92 } },
    integrity: { value: 'supported', probabilities: { unsupported: .01, uncertain: .04, supported: .95 } },
    intent: { value: 'callback', probabilities: { continue: .16, correct: .02, callback: .75, human: .02, unknown: .05 } },
    human: { value: 'not_requested', probabilities: { requested: .02, not_requested: .95, unknown: .03 } },
    schedule_flexibility: { value: 'fixed', probabilities: { flexible: .16, fixed: .79, unknown: .05 } },
  } },
];
