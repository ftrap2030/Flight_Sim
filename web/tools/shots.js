/* Put the aircraft at fixed places in the world and photograph it. This is how
   the renderer is checked: a frame rate number from a software rasteriser is
   meaningless, but whether a lake is level and whether a ring boundary shows is
   not.

       node web/tools/shots.js "$PWD/web/anfell.html" /tmp/shots

   Set PW and CHROMIUM to point at Playwright and a Chromium if they are not on
   the default path. */

const PW = process.env.PW || 'playwright';
const { chromium } = require(PW);

/* Put the aircraft somewhere and photograph it. `paused` matters: without it
   the aeroplane flies out of the frame -- or into the ground -- between the
   setup and the shutter. */
const place = (p, o) => p.evaluate(o => {
  paused = true;
  S.x = o.x; S.y = o.y; S.alt = o.alt; S.hdg = o.hdg;
  S.gamma = o.gamma || 0; S.pitch = o.pitch || 2; S.bank = o.bank || 0;
  S.tas = o.tas || 260; S.onGround = false; S.gear = !!o.gear;
  S.flaps = o.flaps || 0; S.brakes = 0;
  if (o.timeH !== undefined) { timeOfDay = o.timeH; }
  if (o.view) { view = o.view; }
  refreshActiveFields(S.x, S.y);
  htOriginNm = [NaN, NaN]; htFineOriginNm = [NaN, NaN];   // force both bakes
}, o);

(async () => {
  const launch = { args: ['--use-gl=angle', '--use-angle=swiftshader',
                          '--enable-unsafe-swiftshader'] };
  if (process.env.CHROMIUM) launch.executablePath = process.env.CHROMIUM;
  const b = await chromium.launch(launch);
  const p = await b.newPage({ viewport: { width: 1280, height: 820 } });
  const errs = []; p.on('pageerror', e => errs.push('PAGEERROR: ' + e.message));
  await p.goto('file://' + process.argv[2]);
  await p.waitForTimeout(7000);
  console.log('ERRORS: ' + JSON.stringify(errs.slice(0, 3)));
  const out = process.argv[3];

  /* A valley floor at 1,221 ft with a 7,640 ft ridge three miles off, and no
     airfield within twenty-five miles -- so nothing here is graded flat. */
  const V = { x: 138, y: -144 };

  await place(p, { ...V, alt: 2000, hdg: 40, tas: 250 });
  await p.waitForTimeout(2500);
  await p.screenshot({ path: out + '/d-low.png' });

  /* Same spot, half a mile on: anything that shimmers or cracks at a ring
     boundary shows up as a difference between these two. */
  await place(p, { x: V.x + 0.35, y: V.y + 0.42, alt: 2000, hdg: 40, tas: 250 });
  await p.waitForTimeout(2000);
  await p.screenshot({ path: out + '/d-low2.png' });

  /* From height, where the outer rings do the work. */
  await place(p, { ...V, alt: 9000, hdg: 40, tas: 300, view: 'chase' });
  await p.waitForTimeout(2200);
  await p.screenshot({ path: out + '/d-high.png' });

  /* A lake, from low enough to see the glint on it. Height is taken from the
     terrain here rather than fixed, or the aeroplane ends up inside a hillside. */
  const lakeAlt = await p.evaluate(() => elevation(-9, 118) + 2600);
  await place(p, { x: -9, y: 118, alt: lakeAlt, hdg: 20, tas: 250, view: 'cockpit' });
  await p.waitForTimeout(2500);
  await p.screenshot({ path: out + '/d-water.png' });

  /* A settled valley, after dark. */
  await place(p, { x: -90, y: -47, alt: 3200, hdg: 355, tas: 250,
                   timeH: 22.5, view: 'cockpit' });
  await p.waitForTimeout(2500);
  await p.screenshot({ path: out + '/d-night.png' });

  console.log('LATE ERRORS: ' + JSON.stringify(errs.slice(0, 3)));
  await b.close();
})();
