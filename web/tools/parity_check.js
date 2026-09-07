/* Dump the browser build's speeds and flight modes for a fixed set of states.
   `parity_check.py` computes the same from flight_sim/ and compares.

       node web/tools/parity_check.js "$PWD/web/anfell.html" > /tmp/web.json
       python web/tools/parity_check.py /tmp/web.json

   This is the guard on the claim that the glass cockpit shows *model* data.
   A PFD is a plausible-looking thing: a speed tape with the marks in the wrong
   place still looks like a speed tape, an FMA saying OP CLB when the aeroplane
   is levelling off still looks like an FMA, and an E/WD announcing the failure
   of the engine that is still running still looks like an E/WD. Only comparing
   them against the side with the tests catches any of it.

   Set PW and CHROMIUM to point at Playwright and a Chromium if they are not on
   the default path. */

const PW = process.env.PW || 'playwright';
const { chromium } = require(PW);

/* Each case is a state the two builds must describe identically. Chosen to walk
   the modes: nothing engaged, a climb, the capture, the hold, managed lateral,
   an approach armed, and the flaps out where the speed tape changes shape. */
const CASES = [
  { name: 'cold cruise',      alt: 20000, ias: 280, flaps: 0, gear: false, ap: {} },
  { name: 'climbing',         alt: 20000, ias: 280, flaps: 0, gear: false,
    ap: { engaged: true, altFt: 30000, spdKt: 280, hdgDeg: 90 } },
  { name: 'capturing',        alt: 20000, ias: 280, flaps: 0, gear: false,
    ap: { engaged: true, altFt: 20400, spdKt: 280, hdgDeg: 90 } },
  { name: 'holding a level',  alt: 20000, ias: 280, flaps: 0, gear: false,
    ap: { engaged: true, altFt: 20000, spdKt: 280, hdgDeg: 90 } },
  { name: 'vertical speed',   alt: 12000, ias: 250, flaps: 0, gear: false,
    ap: { engaged: true, vsFpm: -1800, spdKt: 250, hdgDeg: 200 } },
  { name: 'managed lateral',  alt: 20000, ias: 280, flaps: 0, gear: false,
    dest: true, ap: { engaged: true, altFt: 20000, spdKt: 280, nav: true } },
  { name: 'approach armed',   alt: 8000, ias: 210, flaps: 2, gear: false,
    ap: { engaged: true, altFt: 8000, spdKt: 210, hdgDeg: 70, appr: true } },
  { name: 'landing config',   alt: 3000, ias: 150, flaps: 4, gear: true,
    ap: { engaged: true, altFt: 3000, spdKt: 150, hdgDeg: 70 } },
  { name: 'heavy, flaps 3',   alt: 5000, ias: 180, flaps: 3, gear: true,
    mass: 'mtow', ap: {} },
  { name: 'high, mach limit', alt: 41000, ias: 250, flaps: 0, gear: false, ap: {} },
  /* On the runway. Newly comparable: until the Python grew a takeoff there was
     no way to reach MAN TOGA or to bug a V-speed on the side with the tests. */
  { name: 'lined up',         alt: 2367, ias: 0, flaps: 1, gear: true,
    ground: true, throttle: 0, ap: {} },
  { name: 'takeoff roll',     alt: 2367, ias: 120, flaps: 1, gear: true,
    ground: true, throttle: 100, ap: {} },
  { name: 'heavy departure',  alt: 2367, ias: 90, flaps: 2, gear: true,
    ground: true, throttle: 100, mass: 'mtow', ap: {} },
  /* Broken. The ECAM text has one owner too, and a failed engine's gauges are
     the case where the engine readouts are least like each other -- the fan is
     running down, so N1 is mid-decay rather than at either end of its range. */
  { name: 'the V1 cut',       alt: 2367, ias: 150, flaps: 1, gear: true,
    ground: true, throttle: 100, fail: [['engine', 0]], spool: 25, ap: {} },
  { name: 'fire and a leak',  alt: 20000, ias: 280, flaps: 0, gear: false,
    fail: [['fire', 1], ['fuel', 0]], spool: 40,
    ap: { engaged: true, altFt: 20000, spdKt: 280, hdgDeg: 90 } },
  { name: 'jammed and slow',  alt: 4000, ias: 170, flaps: 2, gear: true,
    fail: [['flaps', 0], ['hydraulics', 0], ['brakes', 0]], spool: 5, ap: {} },
  { name: 'everything wrong', alt: 9000, ias: 220, flaps: 3, gear: true,
    fail: [['engine', 0], ['fire', 1], ['fuel', 0], ['gear', 0]], spool: 60,
    ap: {} }
];

const TYPES = ['a320neo', 'a350', 'a380', 'a330neo'];

(async () => {
  const page = process.argv[2];
  if (!page) { console.error('usage: parity_check.js <path to anfell.html>'); process.exit(2); }

  const launch = { args: ['--use-gl=angle', '--use-angle=swiftshader',
                          '--enable-unsafe-swiftshader'] };
  if (process.env.CHROMIUM) launch.executablePath = process.env.CHROMIUM;
  const browser = await chromium.launch(launch);
  const p = await browser.newPage({ viewport: { width: 800, height: 600 } });
  const errs = [];
  p.on('pageerror', e => errs.push(e.message));
  await p.goto('file://' + page);
  await p.waitForTimeout(6000);

  const rows = await p.evaluate(([cases, types]) => {
    paused = true;
    /* The browser draws the Airbus grammar in hex; the Python names it. This is
       the only place the two vocabularies meet, so a colour drawn from outside
       the grammar shows up here as an unmapped hex rather than passing. */
    const ECAM_NAMES = { [ECAM_RED]: 'red', [ECAM_AMBER]: 'amber',
                         [ECAM_CYAN]: 'cyan', [ECAM_GREEN]: 'green' };
    const out = [];
    for (const key of types) {
      const a = FLEET.find(f => f.key === key);
      for (const c of cases) {
        /* A fresh state each time, so a mode left over from the previous case
           cannot make two builds agree for the wrong reason. */
        Object.assign(S, {
          alt: c.alt, flaps: c.flaps, gear: c.gear, spoilers: false,
          onGround: !!c.ground, status: c.ground ? 'rollout' : 'flying',
          pitch: c.ground ? 0 : 2, bank: 0, gamma: 0,
          beta: 0, rudder: 0, enginesRunning: true, enginesFailed: [],
          enginesOnFire: [], failures: [], jammedFlaps: null, jammedGear: null,
          armedFailure: null, alphaFloorLatched: false,
          throttle: c.throttle === undefined ? 60 : c.throttle, approach: null,
          mass: c.mass === 'mtow' ? a.mtow : a.oew + a.payload + a.startFuel,
          ap: freshAutopilot(), dest: null, fmaChanged: {}, t: 100
        });
        S.tas = iasToTas(c.ias * MS_PER_KT, c.alt);
        settleEngines(a, S);        // the levers were just assigned
        Object.assign(S.ap, c.ap);
        if (c.dest) S.dest = airfieldsNear(S.x, S.y, 90)[0] || null;

        /* Break it, then let the fans run down for as long as the case says.
           The spool is where a port is easiest to get subtly wrong -- two time
           constants and a direction test -- and a half-decayed N1 is the only
           value that catches an error in any of the three. */
        for (const [key, index] of (c.fail || [])) triggerFailure(a, S, key, index);
        for (let i = 0; i < (c.spool || 0); i++) spoolEngines(a, S, 0.1);

        const sp = characteristicSpeeds(a, S);
        const vs = vSpeeds(a, S);
        const f = fma(a, S);
        const eng = engineReadouts(a, S);
        out.push({
          key, case: c.name,
          speeds: Object.fromEntries(
            Object.entries(sp).map(([k, v]) => [k, Math.round(v * 1000) / 1000])),
          takeoff: Object.fromEntries(
            Object.entries(vs).map(([k, v]) => [k, Math.round(v * 1000) / 1000])),
          fma: Object.fromEntries(FMA_COLUMNS.map(col => [col, {
            engaged: f[col].engaged ? f[col].engaged[0] : null,
            armed: f[col].armed ? f[col].armed[0] : null
          }])),
          channels: apChannels(S),
          engines: eng.map(e => ({
            n1: Math.round(e.n1 * 1000) / 1000,
            n2: Math.round(e.n2 * 1000) / 1000,
            egt: Math.round(e.egt * 1000) / 1000,
            flow: Math.round(e.flow * 1000) / 1000,
            failed: !!e.failed, fire: !!e.fire
          })),
          ecam: ecamLines(S).map(l => ({
            text: l.text, colour: ECAM_NAMES[l.colour] || l.colour,
            indent: !!l.indent
          }))
        });
      }
    }
    return out;
  }, [CASES, TYPES]);

  await browser.close();
  if (errs.length) { console.error('page errors:', errs.slice(0, 3)); process.exit(1); }
  console.log(JSON.stringify({ cases: CASES, rows }, null, 1));
})();
