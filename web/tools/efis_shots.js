/* Photograph the PFD and ND in the states that exercise them: an approach with
   the low-speed marks in range, a managed climb at a wide ND range, and low
   over mountains where TERR ON ND has something to say.

       node web/tools/efis_shots.js "$PWD/web/anfell.html" /tmp/shots

   Set PW and CHROMIUM if Playwright and Chromium are not on the default path. */

const PW = process.env.PW || 'playwright';
const { chromium } = require(PW);
(async () => {
  const launch = { args: ['--use-gl=angle', '--use-angle=swiftshader',
                          '--enable-unsafe-swiftshader'] };
  if (process.env.CHROMIUM) launch.executablePath = process.env.CHROMIUM;
  const b = await chromium.launch(launch);
  const p = await b.newPage({ viewport:{width:1440,height:900} });
  const errs=[]; p.on('pageerror',e=>errs.push(e.message));
  await p.goto('file://'+process.argv[2]);
  await p.waitForTimeout(6500);
  const out = process.argv[3];

  const setup = (p, o) => p.evaluate(o => {
    paused = true;
    Object.assign(S, { onGround:false, status:"flying", brakes:0, spoilers:false,
      alt:o.alt, flaps:o.flaps, gear:o.gear, pitch:o.pitch||2, bank:o.bank||0,
      gamma:o.gamma||0, hdg:o.hdg===undefined?68:o.hdg });
    S.tas = iasToTas(o.ias * MS_PER_KT, o.alt);
    if (o.x !== undefined) { S.x = o.x; S.y = o.y; refreshActiveFields(S.x,S.y); }
    S.ap = freshAutopilot(); S.fmaChanged = {};
    Object.assign(S.ap, o.ap || {});
    S.dest = o.dest ? (airfieldsNear(S.x,S.y,120).find(f=>f.distanceNm(S.x,S.y)>o.destMin||0) || null) : null;
    ndRangeIdx = o.nd === undefined ? 2 : o.nd;
    ndTerrainOn = o.terr === undefined ? true : o.terr;
    S.approach = approachGuidance(craft, S);
    refreshDestinations(); updateFcu();
  }, o);

  // 1. On final: the tape's low-speed marks are all in range here.
  const f = await p.evaluate(() => {
    const f = buildAuthored()[0];
    const back = (f.heading + 180) * Math.PI/180, d = 6;
    return { x: f.x + Math.sin(back)*d, y: f.y + Math.cos(back)*d,
             alt: f.elev + 1900, hdg: f.heading };
  });
  await setup(p, { ...f, ias: 155, flaps: 4, gear: true, gamma: -3, pitch: 1.5,
                   nd: 0, ap: { engaged:true, spdKt:150, hdgDeg:f.hdg, appr:true } });
  await p.waitForTimeout(2200);
  await p.screenshot({ path: out + '/efis-final.png' });

  // 2. Cruise, climbing, managed lateral, wide ND range.
  await setup(p, { alt: 26000, ias: 290, flaps: 0, gear: false, nd: 3, dest: true,
                   destMin: 30, ap: { engaged:true, altFt:34000, spdKt:290, nav:true } });
  await p.waitForTimeout(2200);
  await p.screenshot({ path: out + '/efis-cruise.png' });

  // 3. Low over terrain, where TERR ON ND has something to say.
  await setup(p, { x: 138, y: -144, alt: 4200, ias: 240, flaps: 0, gear: false,
                   nd: 1, bank: 18, pitch: 4,
                   ap: { engaged:true, altFt:4200, spdKt:240, hdgDeg:40 } });
  await p.waitForTimeout(2600);
  await p.screenshot({ path: out + '/efis-terrain.png' });

  console.log('errors', errs.slice(0,3));
  await b.close();
})();
