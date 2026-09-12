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
    ap: {} },
  /* Transitions, not resting states. Every case above sets something and looks
     at it; none of them *un*-sets anything, which is exactly how a fire came to
     survive a restart with the ECAM silent and the E/WD still painting it. */
  { name: 'fire, then restart', alt: 20000, ias: 280, flaps: 0, gear: false,
    fail: [['fire', 1]], then: [['restore']], spool: 30, ap: {} },
  { name: 'broken, fixed, broken again', alt: 12000, ias: 250, flaps: 2,
    gear: true, fail: [['hydraulics', 0], ['flaps', 0]],
    then: [['clear', 'flaps'], ['fail', 'brakes']], spool: 5, ap: {} },
  { name: 'an engine fixed', alt: 12000, ias: 250, flaps: 0, gear: false,
    fail: [['engine', 1], ['hydraulics', 0]], then: [['clear', 'engine']],
    spool: 15, ap: {} },
  { name: 'the last engine of four', alt: 15000, ias: 260, flaps: 0,
    gear: false, fail: [['fire', 3]], spool: 20, ap: {} },
  /* An EGT in each band. The band is model data now, and comparing it means
     nothing while every state sits in the green. */
  /* One degree either side of each threshold, so a limit that drifts by any
     amount at all moves the band. Sampling comfortably inside a band would
     compare two builds that happen to agree rather than two that must. */
  { name: 'egt just under the caution', alt: 0, ias: 200, flaps: 0, gear: false,
    throttle: 100, egt: 875, ap: {} },
  { name: 'egt just over the caution', alt: 0, ias: 200, flaps: 0, gear: false,
    throttle: 100, egt: 876, ap: {} },
  { name: 'egt just under the limit', alt: 0, ias: 200, flaps: 0, gear: false,
    throttle: 100, egt: 950, ap: {} },
  { name: 'egt just over the limit', alt: 0, ias: 200, flaps: 0, gear: false,
    throttle: 100, egt: 951, ap: {} }
];

/* Four airliners and the freighter. The BelugaXL is here because its numbers
   are the least like anything else in the fleet -- Mmo 0.78 against 0.89, a
   ceiling below where the others cruise, an aspect ratio of 10 -- so it is the
   type most likely to walk into a ported constant that was quietly wrong. */
const TYPES = ['a320neo', 'a350', 'a380', 'a330neo', 'belugaxl'];

/* The weather is compared separately, because it is a property of the world and
   the clock rather than of an aeroplane. Nothing guarded it before, and it had
   drifted badly: the browser had one constant wind vector at every altitude,
   over every piece of ground, for the whole flight.

   `t` is elapsed seconds, which is what drives the evolution. */
const WEATHER_CASES = [
  { profile: 'clear',     t: 0 },
  { profile: 'clear',     t: 1800 },
  { profile: 'crosswind', t: 0 },
  { profile: 'crosswind', t: 450 },
  { profile: 'crosswind', t: 7200 },
  { profile: 'stormy',    t: 0 },
  { profile: 'stormy',    t: 3600 },
  { profile: 'foggy',     t: 900 }
];

/* Heights through the friction layer and out the other side of it. */
const WIND_HEIGHTS = [0, 150, 800, 2000, 9000];

/* Where the terrain coupling is sampled. A rotor of zero in both builds proves
   nothing, so the sweep runs a line of ground and the Python end asserts that
   what it found is genuinely non-zero. */
const ROTOR_SWEEP = { x0: 300, y: 200, step: 0.3, count: 120, aglFt: 1500 };

/* The flight plan, which is compared per *route* rather than per state. Both
   builds had grown an end-of-flight card by hand and a planner is the same
   shape of hazard, only worse: a block fuel figure that is 6% out looks
   entirely plausible and there is nothing on the screen to check it against.

   The masses are chosen to bracket the weight range, because cruise flow is
   strongly weight-dependent and a planner that forgot the mass falls would
   still agree at one weight. The short route is the one that exercises the
   level search, and the long one the one that reaches a cruise at all. */
/* The managed descent. The guidance is a pure function of the aeroplane, its
   mass, where it is and what route it is flying -- so unlike a flown profile
   it must agree *exactly*, and a top of descent that drifted between the two
   builds would put the mark on the browser's navigation display somewhere the
   text simulator never said to start down.

   The aircraft sits on the first field of its route, so the distance to go is
   the whole route and needs no trigonometry that could be got wrong twice.
   High levels over short routes are already past the top of descent; the long
   routes are still short of it, which is the case that exercises ALT CRZ. */
const DESCENT_CASES = [
  { key: 'a320neo',  route: ['ANFL', 'CROW'],                          alt: 37000, massT: 71.3 },
  { key: 'a320neo',  route: ['ANFL', 'KEBR', 'CROW', 'HRWD'],          alt: 37000, massT: 64.6 },
  { key: 'a320neo',  route: ['ANFL', 'KEBR'],                          alt: 8000,  massT: 71.3 },
  { key: 'a350',     route: ['ANFL', 'VSPR', 'CROW', 'HRWD'],          alt: 37000, massT: 252.4 },
  { key: 'a350',     route: ['ANFL', 'CROW'],                          alt: 41000, massT: 230.0 },
  { key: 'a380',     route: ['ANFL', 'KEBR', 'CROW'],                  alt: 37000, massT: 497.0 },
  { key: 'a330neo',  route: ['ANFL', 'HRWD'],                          alt: 31000, massT: 235.0 },
  { key: 'belugaxl', route: ['ANFL', 'CROW'],                          alt: 25000, massT: 211.0 },
  /* Below the destination's elevation: there is no descent left to fly, and a
     build that inverted the test would invent a path that climbs. */
  { key: 'a320neo',  route: ['ANFL', 'VSPR'],                          alt: 1000,  massT: 71.3 },
];

/* The sky. Traffic is evaluated rather than simulated, so a contact's position
   is a pure function of the seed and the clock and must agree *exactly* -- and
   there is nothing on the screen to check it against, so if the two builds put
   different aeroplanes in different places nothing else would ever notice.
   Sampled across the timetable's cycle, including a moment in each phase. */
/* Times chosen so that somebody is climbing, somebody is cruising and
   somebody is descending across the sample. Without that the comparison is
   vacuous in exactly the way the flight-plan section's was: a cruise altitude
   deliberately broken by two hundred feet went through an eight-moment sample
   unnoticed, because on these short sectors almost nothing is ever in cruise. */
const TRAFFIC_TIMES = [0, 200, 440, 600, 920, 1200, 1440, 2100, 2880, 3300];

/* The TCAS band is a pure function of range and height, so it is compared on a
   grid rather than on wherever the timetable happens to put two aeroplanes --
   real traffic on real routes almost never comes inside three and a half miles,
   so a threshold moved by half a mile sailed through the sampled skies. The
   pairs straddle every threshold by a whisker, which is the same reason four of
   the EGT states sit one degree either side of theirs. */
const BAND_CASES = [
  [1.4, 390], [1.6, 390], [1.4, 410], [1.5, 400],
  [3.4, 890], [3.6, 890], [3.4, 910], [3.5, 900],
  [5.9, 1190], [6.1, 1190], [5.9, 1210], [6.0, 1200],
  [0.2, 0], [0.2, -390], [0.2, -410], [2.0, -880], [2.0, -920],
  [12.0, 100], [40.0, 0], [0.5, 5000], [0.5, -5000]
];

/* The controller. What it says is text on a screen with nothing to check it
   against, so two builds could easily clear the same aeroplane to two
   different levels and neither would ever notice. The levels straddle the
   semicircular rule in both directions, and the distances straddle the
   descent clearance. */
const ATC_CASES = [
  { key: 'a320neo', route: ['ANFL', 'CROW'], alt: 23000, hdg: 215, distNm: 150 },
  { key: 'a320neo', route: ['ANFL', 'CROW'], alt: 23000, hdg: 35,  distNm: 150 },
  { key: 'a350',    route: ['ANFL', 'CROW'], alt: 31000, hdg: 215, distNm: 200 },
  { key: 'a350',    route: ['ANFL', 'CROW'], alt: 31000, hdg: 35,  distNm: 60  },
  { key: 'a380',    route: ['ANFL', 'KEBR'], alt: 37000, hdg: 90,  distNm: 40  },
  { key: 'a330neo', route: ['ANFL', 'CROW'], alt: 4000,  hdg: 270, distNm: 25  },
  { key: 'a320neo', route: ['ANFL', 'CROW'], alt: 12000, hdg: 180, distNm: 300 },
  /* The level tolerance, asked about from both sides and twice over. 290 ft
     off the legal level is inside 300 and 310 is outside, so a tolerance moved
     fifty feet either way changes what the controller says about one of these
     four. The first pair is an aeroplane being given its level; the second is
     already established on one and has drifted off it, which is the branch
     that decides `onClearance` and is otherwise never reached here. Every
     other case above is either exactly on its level or a thousand feet off,
     and neither of those can tell 300 ft from 350. */
  { key: 'a320neo', route: ['ANFL', 'CROW'], alt: 23290, hdg: 35, distNm: 150 },
  { key: 'a320neo', route: ['ANFL', 'CROW'], alt: 23310, hdg: 35, distNm: 150 },
  { key: 'a320neo', route: ['ANFL', 'CROW'], alt: 23290, hdg: 35, distNm: 150,
    clearedFt: 23000, levelReached: true, offLevelS: 20 },
  { key: 'a320neo', route: ['ANFL', 'CROW'], alt: 23310, hdg: 35, distNm: 150,
    clearedFt: 23000, levelReached: true, offLevelS: 20 },
  /* The descent clearance margin, straddled to half a mile: eighty miles out
     this aeroplane is 11.9 nm short of needing its descent and at eighty-one
     it is 12.4, so a margin moved in either direction flips exactly one of
     them. Before these two the nearest case was five miles inside the margin
     and the next a hundred outside, and moving the margin two miles changed
     nothing anywhere. */
  { key: 'a350', route: ['ANFL', 'CROW'], alt: 31000, hdg: 35, distNm: 80 },
  { key: 'a350', route: ['ANFL', 'CROW'], alt: 31000, hdg: 35, distNm: 81 },
];

/* The semicircular rule on a grid, straddling every boundary -- the same
   reason the TCAS bands are on a grid: a rule that only ever gets asked about
   cruise levels an aeroplane happens to be at is barely asked at all. */
const LEVEL_CASES = [
  [10, 23400], [90, 23400], [170, 23400], [179, 23400], [181, 23400],
  [190, 23400], [270, 23400], [350, 23400], [0, 23400], [359, 23400],
  [90, 22600], [270, 22600], [90, 5200], [270, 5200],
  [90, 3200], [270, 3200], [90, 4999], [90, 5001], [45, 40900], [225, 40900]
];

const PLAN_CASES = [
  { key: 'a320neo',  route: ['ANFL', 'KEBR'],                  alt: 4560, massT: 71.3, fuel: 12000 },
  { key: 'a320neo',  route: ['ANFL', 'KEBR', 'CROW', 'HRWD'],  alt: 4560, massT: 64.6, fuel: 9000 },
  { key: 'a350',     route: ['ANFL', 'CROW'],                  alt: 4560, massT: 252.4, fuel: 70000 },
  { key: 'a350',     route: ['ANFL', 'VSPR', 'CROW', 'HRWD'],  alt: 22000, massT: 230.0, fuel: 48000 },
  { key: 'a380',     route: ['ANFL', 'KEBR', 'CROW'],          alt: 4560, massT: 497.0, fuel: 160000 },
  { key: 'a330neo',  route: ['ANFL', 'HRWD'],                  alt: 15000, massT: 235.0, fuel: 58000 },
  { key: 'belugaxl', route: ['ANFL', 'CROW'],                  alt: 4560, massT: 211.0, fuel: 30000 },
  /* Deliberately not enough fuel: `enough` and `spareKg` are model data too,
     and a build that got the sign wrong would tell a pilot they could make it. */
  { key: 'a320neo',  route: ['ANFL', 'KEBR', 'CROW', 'HRWD'],  alt: 4560, massT: 64.6, fuel: 400 },
  /* Two cases that start at cruise level over a long route, so most of the
     distance is *cruise* rather than climb and descent. Without them a planner
     that forgot the mass falls went unnoticed: every other case here files a
     low level and cruises for two miles, and two miles of cruise cannot tell a
     mass model from a constant. This is the rotor sweep's lesson again -- the
     sample has to be somewhere the answer is not nearly zero. */
  { key: 'a350',     route: ['ANFL', 'KEBR', 'CROW', 'HRWD', 'VSPR', 'ANFL'],
    alt: 37000, massT: 252.4, fuel: 70000 },
  { key: 'a380',     route: ['ANFL', 'KEBR', 'CROW', 'HRWD', 'VSPR', 'ANFL'],
    alt: 37000, massT: 497.0, fuel: 160000 }
];

/* How much of a case's distance must actually be cruise for the cruise
   comparison to mean anything. Asserted on the Python end. */
const MINIMUM_CRUISE_NM = 60.0;

/* One resting end-of-flight state per outcome, for the debrief rows. Every row
   is compared on its key, its unit, its decimals, its kind and both numbers --
   and on the rendered string, because that is what a pilot actually reads and
   two builds can agree on 8339.7 and print 8,340 and 8,339. */
const DEBRIEF_CASES = [
  { name: 'a landing, against a plan', key: 'a330neo', kind: 'landed',
    t: 4320, distance: 69.2, initialFuel: 56000, fuel: 47660, planned: 7900,
    maxAlt: 25000, maxIas: 251, maxMach: 0.62, minAgl: 780, maxG: 1.31,
    warnings: [], route: ['ANFL', 'CROW'],
    touchdown: { grade: 'normal landing', sink: 180, ias: 140, ratio: 1.03,
                 across: 38, remaining: 5900, onRunway: true } },
  { name: 'a landing with no plan filed', key: 'a320neo', kind: 'landed',
    t: 700.4, distance: 15.44, initialFuel: 12000, fuel: 11889, planned: 0,
    maxAlt: 5009, maxIas: 250, maxMach: 0.4141, minAgl: 2473.6, maxG: 1.004,
    warnings: ['ALTERNATE LAW'], route: [],
    touchdown: { grade: 'greaser', sink: 44.4, ias: 140.5, ratio: 0.9812,
                 across: -8.2, remaining: 5100.5, onRunway: true } },
  /* Under thirty seconds, so the average-burn row must be absent in both. */
  { name: 'a crash before the average burn row exists', key: 'a350', kind: 'terrain',
    t: 24.0, distance: 1.9, initialFuel: 70000, fuel: 69940, planned: 0,
    maxAlt: 5100, maxIas: 310, maxMach: 0.51, minAgl: 0, maxG: 2.4,
    warnings: ['TERRAIN — PULL UP', 'STALL'], route: ['ANFL'], touchdown: null },
  /* 119.9999 s reads as "1 min 60 s" if the split happens before the round. */
  { name: 'the clock at a minute less an instant', key: 'a380', kind: 'structural',
    t: 119.9999, distance: 9.1, initialFuel: 160000, fuel: 159052, planned: 0,
    maxAlt: 12000, maxIas: 420, maxMach: 0.72, minAgl: 300, maxG: 3.85,
    warnings: ['OVERSPEED'], route: [], touchdown: null },
  { name: 'an overrun', key: 'belugaxl', kind: 'overrun',
    t: 300.0, distance: 2.4, initialFuel: 30000, fuel: 29500, planned: 2010,
    maxAlt: 4600, maxIas: 165, maxMach: 0.25, minAgl: 0, maxG: 1.1,
    warnings: [], route: ['ANFL', 'CROW'],
    touchdown: { grade: 'runway excursion', sink: 260, ias: 155, ratio: 1.12,
                 across: 210, remaining: 0, onRunway: false } }
];

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
        for (const [op, arg] of (c.then || [])) {
          if (op === 'restore') restoreEngines(S);
          else if (op === 'clear') clearFailure(S, arg);
          else if (op === 'fail') triggerFailure(a, S, arg, 0);
        }

        const eng = c.egt === undefined ? engineReadouts(a, S)
          /* Forced, because no flyable state reaches the caution band: EGT is a
             function of fan speed and the air going in, and the fan tops out at
             the takeoff rating. The band is what is being compared. */
          : engineReadouts(a, S).map(e =>
              Object.assign({}, e, { egt: c.egt, egtBand: egtBand(c.egt) }));

        const sp = characteristicSpeeds(a, S);
        const vs = vSpeeds(a, S);
        const f = fma(a, S);
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
            egtBand: e.egtBand, failed: !!e.failed, fire: !!e.fire
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

  const weather = await p.evaluate(([cases, heights, sweep]) => {
    paused = true;
    const round = v => Math.round(v * 1e6) / 1e6;
    return cases.map(c => {
      const w = new WeatherState(WEATHER_BY_KEY[c.profile], WORLD_SEED, c.t);
      const row = {
        profile: c.profile, t: c.t,
        windKt: round(w.windKt), windDir: round(w.windDir),
        visSm: round(w.visSm), turbulence: round(w.turbulence),
        at: heights.map(agl => {
          const [speed, dir] = w.windAt(agl, 0);
          return [agl, round(speed), round(dir)];
        }),
        /* The gust at the noise clamp and at rest: `gustKt` is the peak, so
           this is where the two builds could disagree about what a peak means. */
        gust: [-3, 0, 3].map(k => round(w.windAt(1500, k)[0])),
        rotor: [], wave: []
      };
      /* The terrain effects want a wind that is not drifting underneath the
         comparison, so they are sampled on a held state. */
      const held = new WeatherState(WEATHER_BY_KEY[c.profile], WORLD_SEED, c.t)
        .hold({ windKt: w.windKt, windDir: w.windDir });
      for (let i = 0; i < sweep.count; i++) {
        const x = sweep.x0 + i * sweep.step;
        row.rotor.push(round(held.mechanicalTurbulence(x, sweep.y, sweep.aglFt)));
        row.wave.push(round(held.orographicVerticalFpm(x, sweep.y, sweep.aglFt)));
      }
      return row;
    });
  }, [WEATHER_CASES, WIND_HEIGHTS, ROTOR_SWEEP]);

  /* And the gusts themselves. Both builds draw them from the same lattice hash,
     so these must agree to the last bit rather than to a tolerance. */
  const gusts = await p.evaluate(() => {
    const out = [];
    for (const tick of [0, 1, 7, 250, 6000]) {
      for (const index of [0, 1, 37, 99]) {
        for (let axis = 0; axis < 3; axis++) {
          out.push([tick, index, axis, gustDraw(tick, index, WORLD_SEED, axis)]);
        }
      }
    }
    return out;
  });

  const plans = await p.evaluate(cases => {
    paused = true;
    startMode = "airborne";
    const fields = buildAuthored();
    return cases.map(c => {
      craft = FLEET_BY_KEY[c.key];
      newFlight(true);
      Object.assign(S, { alt: c.alt, mass: c.massT * 1000, fuel: c.fuel,
                         gamma: 0, bank: 0, flaps: 0, gear: false,
                         spoilers: false, beta: 0, rudder: 0,
                         enginesFailed: [], enginesRunning: true,
                         onGround: false, status: "flying" });
      /* Planning from the *field* rather than from wherever the opening
         position happens to be, so the two builds start from the same point
         to the metre and the leg distances are comparable at all. */
      const home = fields.find(f => f.ident === c.route[0]);
      S.x = home.x; S.y = home.y;
      S.route = new Route(c.route.map(id =>
        Waypoint.fromAirfield(fields.find(f => f.ident === id))));
      const plan = planRoute(craft, S);
      return {
        cruiseFt: plan.cruiseFt, distanceNm: plan.distanceNm,
        timeS: plan.timeS,
        climbFuelKg: plan.climbFuelKg, climbTimeS: plan.climbTimeS,
        climbDistanceNm: plan.climbDistanceNm,
        cruiseFuelKg: plan.cruiseFuelKg, cruiseTimeS: plan.cruiseTimeS,
        descentFuelKg: plan.descentFuelKg, descentTimeS: plan.descentTimeS,
        descentDistanceNm: plan.descentDistanceNm,
        reserveKg: plan.reserveKg, blockFuelKg: plan.blockFuelKg,
        requiredKg: plan.requiredKg, spareKg: plan.spareKg,
        enough: plan.enough,
        legs: plan.legs.map(l => ({
          label: l.waypoint.label, distanceNm: l.distanceNm,
          trackDeg: l.trackDeg, fuelKg: l.fuelKg, timeS: l.timeS
        }))
      };
    });
  }, PLAN_CASES);

  const descents = await p.evaluate(cases => {
    paused = true;
    startMode = "airborne";
    const fields = buildAuthored();
    return cases.map(c => {
      craft = FLEET_BY_KEY[c.key];
      newFlight(true);
      Object.assign(S, { alt: c.alt, mass: c.massT * 1000,
                         gamma: 0, bank: 0, flaps: 0, gear: false,
                         spoilers: false, beta: 0, rudder: 0,
                         enginesFailed: [], enginesRunning: true,
                         onGround: false, status: "flying" });
      const home = fields.find(f => f.ident === c.route[0]);
      S.x = home.x; S.y = home.y;
      S.route = new Route(c.route.map(id =>
        Waypoint.fromAirfield(fields.find(f => f.ident === id))));
      S.ap.engaged = true; S.ap.altFt = c.alt; S.ap.des = true;
      const g = descentGuidance(craft, S, true);
      /* The annunciator words go with it: DES and ALT CRZ and THR IDLE are new
         vocabulary, and two front ends must not disagree about which of them
         the aeroplane is in. */
      const cols = fmaColumns(craft, S);
      const say = pair => (pair && pair[0]) ? pair[0][0] : null;
      const armed = pair => (pair && pair[1]) ? pair[1][0] : null;
      return {
        topOfDescentNm: g.topOfDescentNm,
        distanceToGoNm: g.distanceToGoNm,
        targetAltitudeFt: g.targetAltitudeFt,
        deviationFt: g.deviationFt,
        gradientFtPerNm: g.gradientFtPerNm,
        fieldElevationFt: g.fieldElevationFt,
        active: g.active, onPath: g.onPath,
        vertical: say(cols.vertical), verticalArmed: armed(cols.vertical),
        thrust: say(cols.thrust)
      };
    });
  }, DESCENT_CASES);

  const traffic = await p.evaluate(([times, BAND_CASES]) => {
    paused = true;
    const fields = buildAuthored();
    const profiles = buildTrafficProfiles(WORLD_SEED, fields);
    return {
      schedule: profiles.map(pr => ({
        callsign: pr.flight.callsign, key: pr.flight.key,
        origin: pr.flight.origin.ident, destination: pr.flight.destination.ident,
        departureS: pr.flight.departureS, cruiseFt: pr.cruiseFt,
        durationS: pr.durationS, totalNm: pr.totalNm,
        climbNm: pr.climbNm, descentNm: pr.descentNm
      })),
      bands: BAND_CASES.map(([r, ft]) => trafficBand(r, ft)),
      skies: times.map(t => {
        const home = fields[0];
        const out = [];
        for (const pr of profiles) {
          const age = ((t - pr.flight.departureS) % TRAFFIC_CYCLE_S
                       + TRAFFIC_CYCLE_S) % TRAFFIC_CYCLE_S;
          const c = trafficAt(pr, age);
          if (!c) continue;
          const rangeNm = Math.hypot(c.x - home.x, c.y - home.y);
          out.push({ callsign: c.callsign, x: c.x, y: c.y, alt: c.alt,
                     hdg: c.hdg, phase: c.phase, rangeNm: rangeNm,
                     band: trafficBand(rangeNm, c.alt - 20000) });
        }
        return out;
      })
    };
  }, [TRAFFIC_TIMES, BAND_CASES]);

  const atcRows = await p.evaluate(([cases, levelCases]) => {
    paused = true;
    startMode = "airborne";
    const fields = buildAuthored();
    return {
      levels: levelCases.map(([track, want]) => semicircularLevelFt(track, want)),
      cases: cases.map(c => {
        craft = FLEET_BY_KEY[c.key];
        newFlight(true);
        const field = fields.find(f => f.ident === c.route[c.route.length - 1]);
        Object.assign(S, {
          alt: c.alt, hdg: c.hdg, gamma: 0, bank: 0, flaps: 0, gear: false,
          spoilers: false, beta: 0, rudder: 0, onGround: false, status: "flying",
          x: field.x - Math.sin(rad(c.hdg)) * c.distNm,
          y: field.y - Math.cos(rad(c.hdg)) * c.distNm,
          atcClearedAltFt: c.clearedFt === undefined ? null : c.clearedFt,
          atcDescentCleared: false, atcSequence: 0,
          atcOffLevelS: c.offLevelS || 0, atcLevelReached: !!c.levelReached,
          atcChases: 0, atcDeviationS: 0, atcMessages: []
        });
        S.tas = profileTasMs(craft, c.alt);
        S.route = new Route(c.route.map(id =>
          Waypoint.fromAirfield(fields.find(f => f.ident === id))));
        /* Placed rather than flown into, and the clock has not moved
           between cases -- so the guidance is forced, exactly as the Python
           side passes force=True for a parity sample. */
        descentGuidance(craft, S, true);
        const said = atcUpdate(craft, S, 10).map(m => m.text);
        const cl = atcClearance(craft, S);
        return {
          said,
          callsign: cl.callsign, clearedAltitudeFt: cl.clearedAltitudeFt,
          levelText: cl.levelText, descentCleared: cl.descentCleared,
          sequence: cl.sequence, deviationFt: cl.deviationFt,
          onClearance: cl.onClearance
        };
      })
    };
  }, [ATC_CASES, LEVEL_CASES]);

  const debriefs = await p.evaluate(cases => {
    paused = true;
    const fields = buildAuthored();
    return cases.map(c => {
      craft = FLEET_BY_KEY[c.key];
      newFlight(true);
      S.route = new Route(c.route.map(id =>
        Waypoint.fromAirfield(fields.find(f => f.ident === id))));
      Object.assign(S, { t: c.t, distance: c.distance, initialFuel: c.initialFuel,
        fuel: c.fuel, plannedFuel: c.planned, maxAlt: c.maxAlt, maxIas: c.maxIas,
        maxMach: c.maxMach, minAgl: c.minAgl, maxG: c.maxG,
        warningsSeen: c.warnings.slice(), touchdown: c.touchdown });
      const d = debriefData(craft, S, c.kind);
      return {
        outcome: d.outcome, outcomeText: d.outcomeText, grade: d.grade,
        warningsSeen: d.warningsSeen, routeIdents: d.routeIdents,
        rows: d.rows.map(r => ({
          key: r.key, label: r.label, value: r.value, unit: r.unit,
          decimals: r.decimals, kind: r.kind, extra: r.extra,
          text: formatRow(r)
        }))
      };
    });
  }, DEBRIEF_CASES);

  await browser.close();
  if (errs.length) { console.error('page errors:', errs.slice(0, 3)); process.exit(1); }
  console.log(JSON.stringify({
    cases: CASES, rows,
    weatherCases: WEATHER_CASES, windHeights: WIND_HEIGHTS,
    rotorSweep: ROTOR_SWEEP, weather, gusts,
    planCases: PLAN_CASES, plans, debriefCases: DEBRIEF_CASES, debriefs,
    descentCases: DESCENT_CASES, descents,
    trafficTimes: TRAFFIC_TIMES, bandCases: BAND_CASES, traffic,
    atcCases: ATC_CASES, levelCases: LEVEL_CASES, atc: atcRows,
    minimumCruiseNm: MINIMUM_CRUISE_NM
  }, null, 1));
})();
