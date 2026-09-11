/* Hold the engine sound to a turbofan's spectrum, not a propeller's.

       node web/tools/sound_check.js "$PWD/web/anfell.html"

   Sound is the one part of this simulator with no numbers on screen to check
   it against, which is exactly how it came to be synthesising a propeller: a
   sawtooth at 46 + N1x128 Hz with harmonics at two and three times it, which
   is 174 Hz at full power. Ninety-nine percent of the energy sat below 300 Hz,
   there was nothing above 1.5 kHz, and every type made the same noise.

   So it is measured. The engine graph is rebuilt into an OfflineAudioContext,
   rendered, and the spectrum probed -- and the checks are the things that
   actually distinguish the two machines:

     * the fan tone lands on the blade-passing frequency the published fan
       diameter and blade count imply (aircraft.fan_tone_hz);
     * at takeoff power most of the energy is *above* 300 Hz, where a
       propeller's is nearly all below it;
     * types with different fans come out on measurably different tones, so
       the sound is a property of the aeroplane rather than of the synthesiser.

   Paths are arguments because where Playwright and Chromium live is a property
   of the machine, not of the simulator. Set PW and CHROMIUM to override. */

const PW = process.env.PW || 'playwright';
const { chromium } = require(PW);

/* Types chosen to span the fleet's fans: the CFM56's 36 narrow blades on a
   small fan, the LEAP's 18 wide ones, and the biggest Trent. */
const TYPES = ['a320', 'a320neo', 'a350', 'a380', 'belugaxl'];
const N1S = [0.22, 0.60, 1.00];

/* At full power a turbofan must put most of its energy above this. A propeller
   puts nearly all of its below. */
const HIGH_BAND_HZ = 300;
const MIN_HIGH_FRACTION = 0.45;
/* How far a band under 300 Hz may stand above its own two neighbours. Broadband
   noise and filter skirts are smooth and sit near 1; a propeller's harmonic
   series is not. Measured against the synthesis this replaced, which ran to
   two hundred times on the same statistic. */
const MAX_LOW_PEAKINESS = 6.0;
/* And the fan tone must stand this far above the median band, or there is a
   roar with no aeroplane in it. */
const MIN_TONE_PROMINENCE = 20.0;
/* The measured fan tone must land on the derived blade-passing frequency. The
   probe is sixth-octave, so a tolerance below that would be measuring the
   probe rather than the sound. */
const TONE_TOLERANCE = 0.13;

(async () => {
  const page = process.argv[2];
  if (!page) { console.error('usage: sound_check.js <path to anfell.html>'); process.exit(2); }
  const launch = { args: ['--use-gl=angle', '--use-angle=swiftshader',
                          '--enable-unsafe-swiftshader'] };
  if (process.env.CHROMIUM) launch.executablePath = process.env.CHROMIUM;
  const browser = await chromium.launch(launch);
  const p = await browser.newPage({ viewport: { width: 800, height: 600 } });
  const errs = [];
  p.on('pageerror', e => errs.push(e.message));
  await p.goto('file://' + page);
  await p.waitForTimeout(6000);

  const rows = await p.evaluate(async ([types, n1s]) => {
    const out = [];
    for (const key of types) {
      const a = FLEET_BY_KEY[key];
      for (const n1 of n1s) {
        const ctx = new OfflineAudioContext(1, 44100 * 2, 44100);
        const buf = ctx.createBuffer(1, ctx.sampleRate, ctx.sampleRate);
        const d = buf.getChannelData(0);
        for (let i = 0; i < d.length; i++) d[i] = Math.random() * 2 - 1;
        const noiseSource = () => { const s = ctx.createBufferSource();
          s.buffer = buf; s.loop = true; s.start(); return s; };

        /* The same graph and the same settings `updateAudio` applies, at sea
           level with the engines running. Rebuilt rather than tapped because
           an OfflineAudioContext renders faster than real time and a live
           context cannot be measured at all. */
        const shaftHz = FAN_TIP_SPEED_MS / (Math.PI * a.fanDia);
        const fanHz = Math.min(Math.max(n1 * shaftHz * a.fanBlades, 20), 16000);
        const tipMach = n1 * FAN_TIP_SPEED_MS / soundMs(0);
        const buzzing = Math.min(Math.max((tipMach - 0.95) / 0.35, 0), 1);

        const eng = ctx.createGain();
        eng.gain.value = 0.030 + n1 * 0.115;
        eng.connect(ctx.destination);

        const mk = (type, freq, gain, dest) => {
          const o = ctx.createOscillator(); o.type = type; o.frequency.value = freq;
          const g = ctx.createGain(); g.gain.value = gain;
          o.connect(g); g.connect(dest || eng); o.start(); return o;
        };
        mk("sine", fanHz, 0.26 + n1 * 0.40);
        mk("sine", Math.min(fanHz * 2, 18000), 0.09 + n1 * 0.14);

        const buzzGain = ctx.createGain(); buzzGain.gain.value = buzzing * 0.16;
        buzzGain.connect(eng);
        const buzzFilter = ctx.createBiquadFilter();
        buzzFilter.type = "bandpass"; buzzFilter.Q.value = 1.1;
        buzzFilter.frequency.value = Math.min(Math.max(fanHz * 0.8, 200), 6000);
        buzzFilter.connect(buzzGain);
        const buzzCut = ctx.createBiquadFilter();
        buzzCut.type = "highpass"; buzzCut.frequency.value = 260; buzzCut.Q.value = 0.7;
        buzzCut.connect(buzzFilter);
        mk("sawtooth", Math.max(20, n1 * shaftHz), 1.0, buzzCut);

        /* N2 approximated the way `engines.py` shapes it: a spool that idles
           near sixty and reaches a hundred with the fan. */
        const n2 = 0.6 + n1 * 0.38;
        mk("triangle", Math.min(Math.max(
          n2 * shaftHz * HP_SPOOL_RATIO * HP_STAGE_BLADES, 200), 11000), 0.05 * n2);

        const jetGain = ctx.createGain(); jetGain.gain.value = 0.20 + n1 * 0.50;
        jetGain.connect(eng);
        const jetFilter = ctx.createBiquadFilter();
        jetFilter.type = "lowpass"; jetFilter.Q.value = 0.5;
        jetFilter.frequency.value = 320 + n1 * 2600;
        const jetTilt = ctx.createBiquadFilter();
        jetTilt.type = "highpass"; jetTilt.frequency.value = 120; jetTilt.Q.value = 0.7;
        const jetTilt2 = ctx.createBiquadFilter();
        jetTilt2.type = "highpass"; jetTilt2.frequency.value = 120; jetTilt2.Q.value = 0.7;
        jetFilter.connect(jetTilt); jetTilt.connect(jetTilt2); jetTilt2.connect(jetGain);
        noiseSource().connect(jetFilter);

        const pcm = (await ctx.startRendering()).getChannelData(0).slice(44100);

        /* A real FFT, and band *energy* rather than point probes.

           The first version of this evaluated single frequencies at
           sixth-octave spacing. That is fine down at 40 Hz, where the probes
           are 5 Hz apart and the resolution is 5.4 Hz -- and useless at 3 kHz,
           where they are 320 Hz apart, so a fan tone falls between two probes
           and reads as silence while broadband noise reads at every one. It
           said the engine had no high-frequency content when what it had was
           no high-frequency *probe*. Bands, summed over every bin they cover,
           see a tone wherever it lands. */
        const N = 16384, sr = 44100;
        const re = new Float64Array(N), im = new Float64Array(N);
        for (let i = 0; i < N; i++) {
          re[i] = pcm[i] * (0.5 - 0.5 * Math.cos(2 * Math.PI * i / N));
        }
        /* Iterative radix-2 Cooley-Tukey. */
        for (let i = 1, j = 0; i < N; i++) {
          let bit = N >> 1;
          for (; j & bit; bit >>= 1) j ^= bit;
          j ^= bit;
          if (i < j) { let t = re[i]; re[i] = re[j]; re[j] = t;
                       t = im[i]; im[i] = im[j]; im[j] = t; }
        }
        for (let len = 2; len <= N; len <<= 1) {
          const ang = -2 * Math.PI / len;
          const wr = Math.cos(ang), wi = Math.sin(ang);
          for (let i = 0; i < N; i += len) {
            let cr = 1, ci = 0;
            for (let k = 0; k < len / 2; k++) {
              const ur = re[i + k], ui = im[i + k];
              const vr = re[i + k + len / 2] * cr - im[i + k + len / 2] * ci;
              const vi = re[i + k + len / 2] * ci + im[i + k + len / 2] * cr;
              re[i + k] = ur + vr; im[i + k] = ui + vi;
              re[i + k + len / 2] = ur - vr; im[i + k + len / 2] = ui - vi;
              const ncr = cr * wr - ci * wi;
              ci = cr * wi + ci * wr; cr = ncr;
            }
          }
        }
        const binHz = sr / N;
        const power = new Float64Array(N / 2);
        for (let k = 0; k < N / 2; k++) power[k] = re[k] * re[k] + im[k] * im[k];

        /* Sixth-octave bands, each the sum of the bins inside it. */
        const bands = [];
        for (let f = 40; f <= 8000; f *= Math.pow(2, 1 / 6)) {
          const lo = Math.max(1, Math.round(f / Math.pow(2, 1 / 12) / binHz));
          const hi = Math.min(N / 2 - 1, Math.round(f * Math.pow(2, 1 / 12) / binHz));
          let e = 0;
          for (let k = lo; k <= hi; k++) e += power[k];
          bands.push([f, e]);
        }
        let high = 0, all = 0;
        for (const [f, e] of bands) { all += e; if (f > 300) high += e; }
        const peak = bands.reduce((a, b) => (b[1] > a[1] ? b : a));

        /* The measurement that actually separates the two machines. A
           propeller's low end is a *harmonic series* -- 71, 143, 226 Hz, each
           standing far above its neighbours. A jet's low end is a shear layer
           tearing itself apart, which is broadband: no band sticks out. So
           compare the loudest band under 300 Hz against the median one. Smooth
           is a jet; peaky is a propeller, however much energy is up high. */
        /* Peakiness measured against each band's *neighbours*, not against
           the median of the whole low end.

           A tone stands above the bands either side of it. A filter's skirt
           does not -- it is a smooth slope, and the two highpasses at 120 Hz
           make the bands down at 40 Hz very quiet, which drags a median down
           and makes a perfectly smooth spectrum look peaked. Comparing each
           band with its own neighbours sees a harmonic and ignores a slope,
           which is the actual question.

           The fan's own band and the two either side of it are skipped: at
           idle a real turbofan's blade-passing tone genuinely falls below
           300 Hz -- an idling jet is a low hum, not a whine -- and a tone that
           strong leaks into its neighbours through the window. */
        let lowPeakiness = 0;
        for (let i = 1; i < bands.length - 1; i++) {
          const f = bands[i][0];
          if (f >= 300) break;
          if (Math.abs(bands[i][0] / fanHz - 1) < 0.30) continue;
          const neighbours = Math.sqrt(bands[i - 1][1] * bands[i + 1][1]) || 1e-30;
          lowPeakiness = Math.max(lowPeakiness, bands[i][1] / neighbours);
        }

        /* And the fan must stand proud of the noise around it, or there is a
           roar with no aeroplane in it. */
        const allE = bands.map(b => b[1]).sort((x, y) => x - y);
        const medianAll = allE[Math.floor(allE.length / 2)] || 1e-30;
        out.push({ key: key, n1: n1, fanHz: fanHz, peakHz: peak[0],
                   highFraction: high / all, lowPeakiness: lowPeakiness,
                   toneProminence: peak[1] / medianAll });
      }
    }
    return out;
  }, [TYPES, N1S]);

  await browser.close();
  if (errs.length) { console.error('page errors:', errs.slice(0, 3)); process.exit(1); }

  let bad = 0;
  console.log('  type       N1    fan tone   measured peak   >300 Hz   low-end   fan above');
  console.log('                                                      peakiness   the floor');
  for (const r of rows) {
    /* At idle the roar may legitimately be the loudest thing -- an aeroplane
       at flight idle is a hiss, not a whine. From mid-power up the fan is what
       you hear, and that is where a propeller and a turbofan part company. */
    const onTone = r.n1 < 0.5
      || Math.abs(r.peakHz / r.fanHz - 1) <= TONE_TOLERANCE;
    const loud = r.n1 < 0.9 || r.highFraction >= MIN_HIGH_FRACTION;
    const smooth = r.lowPeakiness <= MAX_LOW_PEAKINESS;
    const proud = r.n1 < 0.5 || r.toneProminence >= MIN_TONE_PROMINENCE;
    const ok = onTone && loud && smooth && proud;
    if (!ok) bad++;
    console.log(`  ${ok ? ' ' : '!'} ${r.key.padEnd(8)} ` +
      `${String(Math.round(r.n1 * 100)).padStart(3)}%  ${String(Math.round(r.fanHz)).padStart(6)} Hz` +
      `   ${String(Math.round(r.peakHz)).padStart(7)} Hz` +
      `   ${(r.highFraction * 100).toFixed(0).padStart(5)}%` +
      `   ${r.lowPeakiness.toFixed(1).padStart(7)}x` +
      `   ${r.toneProminence.toFixed(0).padStart(7)}x`);
  }

  /* And the point of doing it per type: two fans must not sound alike. The
     A320ceo's CFM56 and the A320neo's LEAP are the same airframe. */
  const tone = (k, n1) => rows.find(r => r.key === k && r.n1 === n1).fanHz;
  const ratio = tone('a320', 1) / tone('a320neo', 1);
  const spread = tone('a320', 1) / tone('a350', 1);
  console.log(`\n  A320ceo is ${ratio.toFixed(2)}x the A320neo's tone; ` +
              `${spread.toFixed(2)}x the A350's`);
  if (ratio < 1.8) { console.error('  ! the two A320s sound alike'); bad++; }
  if (spread < 2.0) { console.error('  ! the fleet has one voice'); bad++; }

  console.log(bad ? `\n${bad} problem(s)` : '\nthe engines sound like turbofans');
  process.exit(bad ? 1 : 0);
})();
