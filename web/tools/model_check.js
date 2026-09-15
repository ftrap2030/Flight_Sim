/* The 3D model, held to the published figures it is supposed to be built from.

       node web/tools/model_check.js "$PWD/web/anfell.html"

   This exists for the reason `sound_check.js` exists. Rendering was the last
   output in this repository with no guard on it: `shots.js` makes pictures and
   nobody asserts on them, and unlike a speed mark or an ECAM line there is no
   number on the screen to check an aeroplane's shape against. A wing half a
   span too short still looks exactly like a wing.

   What it asserts is what `tests/test_artwork.py` asserts about the ASCII side
   profile, in three dimensions: that the drawing is *derived* -- span, length
   and height are the published ones, there is one pod per engine at the
   published arm, the bogie count follows `mtow` by the same rule -- and that
   **if two types look identical, they are identical.** A drawing may not
   invent a difference it has no way of seeing, and it may not hide one it can.

   Set PW and CHROMIUM to point at Playwright and a Chromium if they are not on
   the default path.  */

const PW = process.env.PW || 'playwright';
const { chromium } = require(PW);

/* Tight, because none of this is approximate: the model is generated from
   these numbers, so it either equals them or it has a bug. */
const DIM_TOL_M = 0.02;

/* `artwork.py`: pavement loading decides the bogies -- one a side on a
   narrowbody, two on a widebody, three on the A380. The point of repeating the
   thresholds here rather than reading them off the model is that a change to
   one build has to be made to the other deliberately. */
const gearGroupsFor = mtow => (mtow < 150000 ? 1 : (mtow < 400000 ? 2 : 3));

/* `SURF` lives in the page; these are the same numbers on this side of the
   bridge, because a constant declared out here is not visible inside
   `p.evaluate` and one declared in there does not come back. */
const SURF_ID = { AIL_L: 1, AIL_R: 2, ELEV: 3, RUDDER: 4, FLAP_L: 5, FLAP_R: 6,
                  SPOIL_L: 7, SPOIL_R: 8, GEAR: 9 };

/* Where a model is measured from, and what its lamps are. Everything below is
   computed in the page, because that is where the geometry is. */
const PROBE = () => {
  const box = tris => {
    const lo = [1e9, 1e9, 1e9], hi = [-1e9, -1e9, -1e9];
    for (const t of tris) for (const v of t.p) for (let i = 0; i < 3; i++) {
      lo[i] = Math.min(lo[i], v[i]); hi[i] = Math.max(hi[i], v[i]);
    }
    return { lo, hi, size: [hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]] };
  };
  return FLEET.map(a => {
    const m = modelFor(a);
    const all = box(m.tris);
    const gear = m.tris.filter(t => t.sid === SURF.GEAR);
    const nac = m.tris.filter(t => t.col === "#dde3e7" || t.col === "#9aa5ac"
                                || t.col === "#39424a" || t.col === "#14181b");
    /* Pods: cluster the nacelle skin by the arm it is nearest to, and check
       each published arm actually has metal on it. */
    const podsFound = a.arms.map(arm => nac.filter(t =>
      t.p.every(v => Math.abs(v[0] - arm) < a.fanDia * 1.2)).length).filter(n => n > 40).length;
    /* The fuselage section at mid-cabin: how far the roof stands above the
       widest point. On a circular section that is the half-width; a cargo lobe
       is whatever the published height adds on top of it. */
    /* The constant section, amidships. By centroid: a fuselage quad spans two
       stations, so demanding all three vertices sit inside a thin band selects
       nothing at all -- which is how this first reported every type as having
       a lobe a billion metres deep. */
    const skin = m.tris.filter(t => (t.col === "#e7ebee" || t.col === "#aab4bc")
      && Math.abs((t.p[0][1] + t.p[1][1] + t.p[2][1]) / 3) < a.length * 0.12);
    let halfW = 0, roof = -1e9, floor = 1e9;
    for (const t of skin) for (const v of t.p) {
      halfW = Math.max(halfW, Math.abs(v[0]));
      roof = Math.max(roof, v[2]); floor = Math.min(floor, v[2]);
    }
    /* Does anything actually join each engine to the wing? Measured, not
       assumed: the topmost nacelle-coloured metal near an arm has to reach up
       to the wing's underside at that station. Without a pylon it stops at the
       cowl crown, a nacelle radius short -- and the first version of this file
       did not notice when the pylons were taken off, because counting
       triangles near an arm counts the pylon's too. */
    const pylonGaps = a.arms.map(arm => {
      const near = t => Math.abs((t.p[0][0] + t.p[1][0] + t.p[2][0]) / 3 - arm) < a.fanDia * 0.75;
      let lo = 1e9, hi = -1e9;
      for (const t of m.tris) {
        if (t.col !== "#c6ced4" || !near(t)) continue;
        for (const v of t.p) { lo = Math.min(lo, v[2]); hi = Math.max(hi, v[2]); }
      }
      /* How deep the pylon actually is, as a fraction of the cowl. A vertical
         *gap* between engine and wing cannot be measured -- on a narrowbody the
         cowl crown is above the wing's underside, because the engine is slung
         ahead of the leading edge rather than below it. So the question is
         whether there is a pylon at all, and whether it is a real one. */
      return hi < lo ? 0 : (hi - lo) / (a.fanDia * 0.58);
    });

    /* The legs, clustered out of the geometry: one at the nose and
       `gearGroups` a side behind the wing. Only counting triangles missed a
       missing nose leg entirely. */
    const cen = t => [0, 1, 2].map(i => (t.p[0][i] + t.p[1][i] + t.p[2][i]) / 3);
    const gbox = box(gear);
    /* The struts only -- the upper half of the leg. Counting whole legs off
       every gear triangle counts each axle of a bogie as its own leg. */
    const struts = gear.filter(t => t.col === "#79828a"
      && cen(t)[2] > gbox.lo[2] + (gbox.hi[2] - gbox.lo[2]) * 0.55).map(cen);
    const noseLegTris = struts.filter(c => c[1] > a.length * 0.10).length;
    const mains = struts.filter(c => c[1] < 0);
    const mainSides = new Set(mains.map(c => Math.sign(c[0]))).size;
    /* Legs a side, by the gaps between them: a strut is about half a metre
       across and the legs are metres apart, so a metre separates the two. */
    const ys = mains.filter(c => c[0] > 0).map(c => c[1]).sort((u, v) => u - v);
    let mainRows = ys.length ? 1 : 0;
    for (let i = 1; i < ys.length; i++) if (ys[i] - ys[i - 1] > 1.0) mainRows++;

    const surfaces = {};
    for (const t of m.tris) surfaces[t.sid || 0] = (surfaces[t.sid || 0] || 0) + 1;
    const lampsBySide = { port: [], stbd: [] };
    for (const l of m.lamps) {
      if (l.kind !== 1) continue;
      if (l.p[0] < -a.span * 0.2) lampsBySide.port.push(l.col);
      if (l.p[0] >  a.span * 0.2) lampsBySide.stbd.push(l.col);
    }
    /* Silhouette: the model's own outline, quantised, so two types that draw
       the same picture hash the same. */
    let sig = 0;
    for (const t of m.tris) for (const v of t.p) {
      const q = Math.round(v[0] * 4) * 73856093 ^ Math.round(v[1] * 4) * 19349663
              ^ Math.round(v[2] * 4) * 83492791;
      sig = (sig ^ q) >>> 0; sig = (sig * 16777619) >>> 0;
    }
    return {
      key: a.key, name: a.name, span: a.span, length: a.length, height: a.height,
      mtow: a.mtow, arms: a.arms.length, pax: a.pax, decks: a.decks,
      fusW: a.fusW, fusH: a.fusH, fanDia: a.fanDia,
      size: all.size, groundZ: m.groundZ, gearGroups: m.gearGroups,
      gearTris: gear.length, gearLowest: box(gear).lo[2],
      pylonGaps, noseLegTris, mainSides, mainRows,
      nacLowest: box(nac).lo[2], podsFound,
      halfW, roof, floor, surfaces, lampsBySide, sig,
      tris: m.tris.length
    };
  });
};

/* The hangar, which is the one place the whole fleet is on show at once.

   The failure this exists for is the quiet one: a type is added to `FLEET` and
   the menu does not list it, so it can be flown by nobody and nothing else in
   the build would ever notice. That is `cruise_check`'s coverage guard in a new
   place. The thumbnails are checked for being *pictures of something* as well
   as present, because a card that rendered an empty frame looks exactly like a
   card until you go looking. */
const HANGAR = () => {
  showHangar();
  const cards = Array.from(document.querySelectorAll(".fleet-card"));
  return {
    screen: screen,
    keys: cards.map(c => c.dataset.key),
    named: cards.map(c => (c.querySelector("b") || {}).textContent || ""),
    /* How much variation is in each thumbnail. A blank card is one colour. */
    spread: cards.map(c => {
      const cv = c.querySelector("canvas");
      if (!cv) return -1;
      const d = cv.getContext("2d").getImageData(0, 0, cv.width, cv.height).data;
      let lo = 255, hi = 0, n = 0;
      for (let i = 0; i < d.length; i += 4) {
        const v = (d[i] + d[i + 1] + d[i + 2]) / 3;
        lo = Math.min(lo, v); hi = Math.max(hi, v); n++;
      }
      return n ? hi - lo : -1;
    })
  };
};

/* Does a surface actually move, and which way?

   Asserted on **where the metal ends up**, not on the sign of an angle. A
   hinge axis points somewhere, and whether +20 degrees is up or down depends
   on which way -- so a test written in signs passes a model whose port flap
   goes up while its starboard flap goes down. That is not a hypothetical: it
   is what this file found the first time it ran. Rotating the surface and
   asking which way its trailing edge went cannot be fooled that way. */
const DEFLECT = () => {
  const a = FLEET_BY_KEY['a320neo'], m = modelFor(a);
  const base = { bank: 0, lawBank: 0, cmdBank: 0, pitch: 2, lawPitch: 2, cmdPitch: 2,
                 rudder: 0, flaps: 0, spoilers: false, gear: false, onGround: false };
  /* The aftmost vertex of each surface: its trailing edge, which is the part
     whose movement is the deflection. */
  const tip = {};
  for (const t of m.tris) {
    const sid = t.sid || 0;
    if (!sid || sid === SURF.GEAR) continue;
    for (const v of t.p) {
      if (!tip[sid] || v[1] < tip[sid][1]) tip[sid] = v;
    }
  }
  const rodrigues = (p, P, A, ang) => {
    const c = Math.cos(ang), s = Math.sin(ang);
    const d = [p[0] - P[0], p[1] - P[1], p[2] - P[2]];
    const cr = [A[1]*d[2] - A[2]*d[1], A[2]*d[0] - A[0]*d[2], A[0]*d[1] - A[1]*d[0]];
    const dot = A[0]*d[0] + A[1]*d[1] + A[2]*d[2];
    return [0, 1, 2].map(i => P[i] + d[i]*c + cr[i]*s + A[i]*dot*(1 - c));
  };
  const at = over => {
    const out = new Float32Array(SURF_COUNT);
    surfaceAngles(a, Object.assign({}, base, over), out);
    const moved = {};
    for (const sid of Object.keys(tip)) {
      const h = m.hinges[sid], p = tip[sid];
      const q = rodrigues(p, h.p, h.axis, out[sid]);
      moved[sid] = { deg: out[sid] * 180 / Math.PI,
                     dx: q[0] - p[0], dy: q[1] - p[1], dz: q[2] - p[2] };
    }
    return moved;
  };
  return {
    neutral: at({}),
    rudderRight: at({ rudder: a.maxRudder }),
    rollLeft: at({ lawBank: -25, cmdBank: -25 }),
    pullUp: at({ lawPitch: 12, cmdPitch: 12 }),
    flapsFull: at({ flaps: 4 }),
    spoilersGround: at({ spoilers: true, onGround: true })
  };
};

(async () => {
  const launch = { args: ['--use-gl=angle', '--use-angle=swiftshader',
                          '--enable-unsafe-swiftshader'] };
  if (process.env.CHROMIUM) launch.executablePath = process.env.CHROMIUM;
  const b = await chromium.launch(launch);
  const p = await b.newPage({ viewport: { width: 900, height: 600 } });
  const pageErrs = [];
  p.on('pageerror', e => pageErrs.push(e.message));
  /* `?fly=1` skips the hangar and boots straight onto the runway. Every
     tool here reaches for `S`, `craft` or `render()` as soon as the page
     settles, and the menu would otherwise leave all of them waiting on a
     flight that has not started. */
  await p.goto('file://' + process.argv[2] + '?fly=1');
  await p.waitForTimeout(6000);
  if (pageErrs.length) {
    console.error('page errors:', pageErrs.slice(0, 3));
    process.exit(1);
  }
  const rows = await p.evaluate(PROBE);
  const defl = await p.evaluate(DEFLECT);
  /* The page was booted with `?fly=1`, so it is on the runway: reaching the
     hangar from here also proves the two screens can be moved between. */
  const flyingFirst = await p.evaluate(() => screen);
  const hangar = await p.evaluate(HANGAR);
  await b.close();

  const bad = [];
  const say = (key, msg) => bad.push(key.padEnd(9) + ' ' + msg);

  for (const r of rows) {
    /* 1. The three dimensions the type publishes. */
    const dims = [['span', r.size[0], r.span], ['length', r.size[1], r.length],
                  ['height', r.size[2], r.height]];
    for (const [name, got, want] of dims) {
      if (Math.abs(got - want) > DIM_TOL_M) {
        say(r.key, `${name} measures ${got.toFixed(2)} m, published ${want} m`);
      }
    }

    /* 2. One pod per engine, on the published arm. */
    if (r.podsFound !== r.arms) {
      say(r.key, `has ${r.arms} engines and ${r.podsFound} pods on their arms`);
    }
    /* And the pods have to clear the ground the wheels stand on, or the
       aeroplane cannot be on a runway at all. */
    if (r.nacLowest - r.groundZ < 0.25) {
      say(r.key, `engine clears the ground by ${(r.nacLowest - r.groundZ).toFixed(2)} m`);
    }
    /* Every engine is joined to the wing. An engine that is merely *near* one
       hangs in space, which is what the old model did and what it looked like. */
    r.pylonGaps.forEach((depth, i) => {
      if (!(depth > 0.20)) {
        say(r.key, `engine ${i + 1} has no pylon worth the name `
                   + `(${(depth * 100).toFixed(0)}% of a cowl deep)`);
      }
    });

    /* 3. The gear, by `artwork.py`'s rule, standing on the ground. */
    const want = gearGroupsFor(r.mtow);
    if (r.gearGroups !== want) {
      say(r.key, `has ${r.gearGroups} main bogies a side, artwork.py says ${want}`);
    }
    if (!r.gearTris) say(r.key, 'has no landing gear at all');
    if (!r.noseLegTris) say(r.key, 'has no nose leg');
    if (r.mainSides !== 2) say(r.key, 'has main gear on ' + r.mainSides + ' side(s)');
    if (r.mainRows !== want) {
      say(r.key, `has ${r.mainRows} rows of main wheels a side, artwork.py says ${want}`);
    }
    if (Math.abs(r.gearLowest - r.groundZ) > DIM_TOL_M) {
      say(r.key, `wheels sit ${(r.gearLowest - r.groundZ).toFixed(2)} m off the ground`);
    }

    /* 4. Every control surface exists. A model with no aileron cannot show
          you a roll, and nothing else in the build would ever notice. */
    for (const [name, sid] of [['aileron', 1], ['aileron', 2], ['elevator', 3],
                               ['rudder', 4], ['flap', 5], ['flap', 6],
                               ['spoiler', 7], ['spoiler', 8]]) {
      if (!r.surfaces[sid]) say(r.key, `has no ${name} (surface ${sid})`);
    }

    /* 5. Red to port, green to starboard. Getting this backwards is the one
          mistake about an aeroplane's lights that actually matters. */
    const red = c => c.toLowerCase().startsWith('#ff');
    const green = c => /^#[0-9a-f]{2}[c-f]/i.test(c) && !red(c);
    if (!r.lampsBySide.port.some(red)) say(r.key, 'has no red light to port');
    if (!r.lampsBySide.stbd.some(green)) say(r.key, 'has no green light to starboard');
    if (r.lampsBySide.port.some(green) || r.lampsBySide.stbd.some(red)) {
      say(r.key, 'has its navigation lights the wrong way round');
    }

    /* 6. The cabin branches on carrying passengers, not on a seat count of
          zero -- `_draw_cabin`'s distinction, and the reason the freighter has
          a flight deck and no window line. */
    const glass = r.surfaces[0];  // structure; the stripes live in it
    if (r.pax && r.decks < 1) say(r.key, 'is an airliner with no deck');
  }

  /* 7. The cargo lobe, from the two published cross-sections differenced --
        and no other type may have grown one. `test_artwork.py` asserts exactly
        this about the ASCII drawing; it is the same claim in 3D. */
  for (const r of rows) {
    const lobe = r.roof - r.halfW;               // how far the roof stands proud
    const expect = r.fusH - r.fusW / 2 - r.fusW / 2;
    if (Math.abs(lobe - expect) > 0.25) {
      say(r.key, `cargo lobe stands ${lobe.toFixed(2)} m above a circular section, `
                 + `published cross-sections differ by ${expect.toFixed(2)} m`);
    }
  }
  const beluga = rows.find(r => r.key === 'belugaxl');
  const a330 = rows.find(r => r.key === 'a330-800');
  if (beluga && a330) {
    if (!(beluga.roof - beluga.floor > a330.roof - a330.floor + 2.5)) {
      say('belugaxl', 'section is no deeper than the A330-800 it is built on');
    }
    if (Math.abs(beluga.halfW - a330.halfW) > 0.25) {
      say('belugaxl', 'lower lobe is not the A330-800 fuselage it is made of');
    }
  }
  /* "No other type grew a lobe" cannot be "no other type is taller than it is
     wide" -- the A380 is, by 1.27 m, because a double-decker's section is an
     oval and says so in its published figures. What is singular about the
     freighter is the *scale*: its lower lobe is an A330's fuselage and the
     cargo section above it is more than half as deep again. So the claim is
     that exactly one type is shaped like that, and it is the Beluga. */
  const lobed = rows.filter(r => r.roof - r.halfW > r.fusW * 0.35).map(r => r.key);
  if (lobed.join() !== 'belugaxl') {
    say('fleet', 'the types with a cargo lobe are [' + lobed.join(', ')
                 + '], and they should be [belugaxl]');
  }

  /* 8. If two types look identical, they are identical -- `artwork.py`'s rule,
        and the same care it takes. A model may not invent a difference it
        cannot see, and may not hide one it can. What a *shape* can carry is
        listed here; the A321neo and the A321XLR differ only in mass and fuel,
        so they draw the same aeroplane and that is correct. Mass appears in
        the list because it decides the bogies, which a shape does show. */
  const shapeOf = r => [r.span, r.length, r.height, r.fusW, r.fusH, r.fanDia,
                        r.arms, r.pax, r.decks, gearGroupsFor(r.mtow)].join('|');
  const bySig = {}, byShape = {};
  for (const r of rows) {
    (bySig[r.sig] = bySig[r.sig] || []).push(r);
    (byShape[shapeOf(r)] = byShape[shapeOf(r)] || []).push(r);
  }
  for (const group of Object.values(bySig)) {
    if (group.length < 2) continue;
    const shapes = new Set(group.map(shapeOf));
    if (shapes.size > 1) {
      say(group[0].key, 'draws the same model as ' + group.slice(1).map(r => r.key).join(', ')
                        + ', and they are not the same shape');
    }
  }
  for (const group of Object.values(byShape)) {
    if (group.length < 2) continue;
    if (new Set(group.map(r => r.sig)).size > 1) {
      say(group[0].key, 'draws a different model from ' + group.slice(1).map(r => r.key).join(', ')
                        + ', which it has no dimension to justify');
    }
  }

  /* 9. The surfaces move, and the metal goes where it should. Every claim
        below is about a trailing edge's position in space, so none of it can
        be satisfied by a sign convention that happens to line up. */
  const D = defl, MOVE = 0.08;                 // metres; anything less is noise
  const surf = msg => bad.push('surfaces  ' + msg);
  for (const sid of Object.keys(D.neutral)) {
    const n = D.neutral[sid];
    if (Math.hypot(n.dx, n.dy, n.dz) > 1e-6) {
      surf('a trimmed aeroplane has surface ' + sid + ' deflected');
    }
  }
  /* Right rudder puts the trailing edge to starboard: that is what yaws the
     nose right, and getting it backwards is the one rudder mistake that
     matters. */
  if (!(D.rudderRight[SURF_ID.RUDDER].dx > MOVE)) {
    surf('right rudder does not swing the trailing edge to starboard'
         + ' (dx ' + D.rudderRight[SURF_ID.RUDDER].dx.toFixed(2) + ' m)');
  }
  /* A roll to port: starboard aileron down, port aileron up. */
  const rl = D.rollLeft;
  if (!(rl[SURF_ID.AIL_R].dz < -MOVE)) surf('rolling left does not lower the starboard aileron');
  if (!(rl[SURF_ID.AIL_L].dz >  MOVE)) surf('rolling left does not raise the port aileron');
  /* Pull up: elevator trailing edge up. */
  if (!(D.pullUp[SURF_ID.ELEV].dz > MOVE)) surf('pulling up does not raise the elevator');
  /* Flaps go down, and on both wings -- the failure this guard was written
     after was one flap down and the other up, which looks fine in a sign. */
  for (const [n, sid] of [['port', SURF_ID.FLAP_L], ['starboard', SURF_ID.FLAP_R]]) {
    if (!(D.flapsFull[sid].dz < -MOVE)) surf('flaps full does not lower the ' + n + ' flap');
  }
  /* Spoilers go up, on both wings. */
  for (const [n, sid] of [['port', SURF_ID.SPOIL_L], ['starboard', SURF_ID.SPOIL_R]]) {
    if (!(D.spoilersGround[sid].dz > MOVE)) surf('the ' + n + ' spoiler does not rise');
  }

  /* 10. Every type is in the hangar, named, with a picture of itself. */
  if (flyingFirst !== "flying") {
    bad.push('hangar    `?fly=1` did not reach the runway (screen was "'
             + flyingFirst + '") -- every tool here depends on it');
  }
  if (hangar.screen !== "hangar") {
    bad.push('hangar    the hangar could not be opened from a flight');
  }
  for (const r of rows) {
    const i = hangar.keys.indexOf(r.key);
    if (i < 0) { say(r.key, 'is in the fleet and not in the hangar'); continue; }
    /* The card's name is the fleet table's own, so a card cannot end up
       labelled as an aeroplane it is not a picture of. */
    if (!hangar.named[i].includes(r.name)) {
      say(r.key, `has a hangar card labelled ${JSON.stringify(hangar.named[i])}, `
                 + `which does not name the ${r.name}`);
    }
    if (!(hangar.spread[i] > 40)) {
      say(r.key, `has a blank hangar card (only ${hangar.spread[i]} levels of `
                 + `light in it -- nothing was drawn)`);
    }
  }
  for (const key of hangar.keys) {
    if (!rows.some(r => r.key === key)) {
      say(key, 'is in the hangar and not in the fleet');
    }
  }

  /* The vacuity guard, the fifth of its kind here: a run that checked nothing
     passes just as quietly as one that checked everything. */
  if (rows.length < 11) bad.push('coverage      the fleet did not all get measured');
  if (!rows.some(r => r.arms === 4)) bad.push('coverage      no four-engined type was measured');
  if (!rows.some(r => !r.pax)) bad.push('coverage      no freighter was measured');
  if (!rows.some(r => r.decks > 1)) bad.push('coverage      no double-decker was measured');
  if (new Set(rows.map(r => r.gearGroups)).size < 3) {
    bad.push('coverage      the fleet does not reach all three bogie counts');
  }
  /* The leg length is *solved* for the clearance the engine needs, so on at
     least one type that solve has to be what decides it -- otherwise the
     aeroplane is standing on a floor figure and the solve is decorative. The
     A321 is the type it binds on, being an A320 stretched and made heavier.
     Without this, standing the whole fleet 25% higher goes through unseen:
     every published figure still holds, because they all measure from the
     ground the wheels are on. */
  const clearances = rows.map(r => r.nacLowest - r.groundZ);
  if (hangar.keys.length !== rows.length) {
    bad.push('coverage      the hangar shows ' + hangar.keys.length
             + ' types and the fleet has ' + rows.length);
  }
  if (!clearances.some(c => Math.abs(c - 0.50) < 0.08)) {
    bad.push('coverage      no type stands on the engine-clearance solve -- '
      + 'the closest is ' + Math.min(...clearances).toFixed(2) + ' m');
  }

  const w = rows.map(r => '  ' + r.key.padEnd(9)
    + String(r.tris).padStart(5) + ' tris'
    + '  ' + r.size.map(v => v.toFixed(1)).join(' x ') + ' m'
    + '  gear ' + r.gearGroups + 'x'
    + '  pods ' + r.podsFound
    + '  under engine ' + (r.nacLowest - r.groundZ).toFixed(2) + ' m'
    + '  lobe ' + (r.roof - r.halfW).toFixed(2) + ' m'
    + (r.pax ? '  cabin ' + r.decks : '  freighter'));
  console.log('  type        model            span x length x height');
  console.log(w.join('\n'));
  console.log();

  if (bad.length) {
    console.log('DISAGREEMENTS (' + bad.length + '):');
    console.log(bad.map(s => '  ' + s).join('\n'));
    process.exit(1);
  }
  console.log('every type is the shape its published figures say it is');
})();
