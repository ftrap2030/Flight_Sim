/* Hold the browser build to the same published figures the Python is held to.
   The browser's flight model is a *port* of flight_sim/, not a second
   implementation, and this is the thing that says so out loud: every type trims
   at its published cruise condition and burns its published block fuel flow.
   If one drifts, one of the two was edited without the other.

       node web/tools/cruise_check.js "$PWD/web/anfell.html"

   Paths are arguments because where Playwright and Chromium live is a property
   of the machine, not of the simulator. Set PW and CHROMIUM to override. */

const PW = process.env.PW || 'playwright';
const { chromium } = require(PW);

/* tests/test_physics.py::CRUISE_TARGETS -- altitude, published flow, and the
   mass each figure belongs to. A target quoted without a weight cannot be
   verified: the same A321neo burns 2,300 kg/h at 85 tonnes and under 2,000
   late in a flight. */
const CASES = [
  ['a319neo', 35000, 1850,  64600], ['a320',    35000,  2400,  69600],
  ['a320neo', 35000, 2000,  71300], ['a321',    35000,  2300,  85100],
  ['a321xlr', 35000, 2600,  94300], ['a330-800', 37000, 5750, 224000],
  ['a330neo', 37000,  6050, 235000],
  ['a350',    37000, 5800, 252400], ['a350k',   37000,  6700, 285000],
  ['a380',    37000, 11500, 497000]
];
const TOLERANCE = 0.05;

(async () => {
  const page = process.argv[2];
  if (!page) { console.error('usage: cruise_check.js <path to anfell.html>'); process.exit(2); }

  const launch = { args: ['--use-gl=angle', '--use-angle=swiftshader',
                          '--enable-unsafe-swiftshader'] };
  if (process.env.CHROMIUM) launch.executablePath = process.env.CHROMIUM;
  const browser = await chromium.launch(launch);
  const p = await browser.newPage({ viewport: { width: 800, height: 600 } });
  const errs = [];
  p.on('pageerror', e => errs.push(e.message));
  await p.goto('file://' + page);
  await p.waitForTimeout(6000);

  /* Every type in the fleet must have a case. Without this the tool happily
     reports "all inside 5%" while quietly not checking a type nobody added a
     target for -- which is what it did the moment the A330-800 went in. A guard
     that is silent about what it is not looking at is not a guard. */
  const uncovered = await p.evaluate(
    keys => FLEET.map(a => a.key).filter(k => !keys.includes(k)),
    CASES.map(c => c[0]));
  if (uncovered.length) {
    console.error('no cruise target for: ' + uncovered.join(', '));
    await browser.close();
    process.exit(1);
  }

  const rows = await p.evaluate(cases => {
    paused = true;
    return cases.map(([key, alt, target, mass]) => {
      const a = FLEET.find(f => f.key === key);
      const s = JSON.parse(JSON.stringify(S));
      Object.assign(s, { alt, mass, fuel: 40000, gamma: 0, bank: 0, flaps: 0,
                         gear: false, spoilers: false, beta: 0, rudder: 0,
                         enginesFailed: [], enginesRunning: true,
                         onGround: false, status: 'flying' });
      s.tas = a.cruiseMach * soundMs(alt);
      s.pitch = levelFlightPitch(a, s);       // trim
      s.throttle = trimThrottle(a, s);
      // Steady state means the fan has caught up with the levers; without this
      // the aircraft is trimmed against a thrust its engines are not making.
      settleEngines(a, s);
      const aero = aeroState(a, s);
      return { key, alt, mach: a.cruiseMach, mass, target,
               flow: Math.round(a.tsfc * aero.thrust * 3600),
               ld: +(aero.lift / aero.drag).toFixed(1) };
    });
  }, CASES);

  if (errs.length) console.error('page errors:', errs.slice(0, 3));
  let bad = 0;
  for (const x of rows) {
    const err = x.flow / x.target - 1;
    const ok = Math.abs(err) <= TOLERANCE;
    if (!ok) bad++;
    console.log(`${ok ? ' ' : '!'} ${x.key.padEnd(8)} FL${x.alt / 100} M${x.mach} `
      + `${(x.mass / 1000).toFixed(1)}t  ${String(x.flow).padStart(6)} kg/h `
      + `(published ${x.target}, ${(err * 100).toFixed(1)}%)  L/D ${x.ld}`);
  }
  await browser.close();
  console.log(bad ? `\n${bad} type(s) outside ${TOLERANCE * 100}%` : `\nall inside ${TOLERANCE * 100}%`);
  process.exit(bad || errs.length ? 1 : 0);
})();
