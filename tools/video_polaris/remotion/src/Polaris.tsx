// Vídeo «Polaris» con historia (EN y ES: textos en textos.ts, idioma y anclas en timeline.json), en Remotion. 28-sep-2026, con el OK de {{TITULAR}} para Remotion.
// Todo se sincroniza con timeline.json (montaje.py): cada frase y CADA PALABRA de su voz tienen su tiempo real
// (Whisper en local), y los cortes caen en la rejilla del tema. Los materiales son los verificados:
// capturas reales de helptitular.com (sin cifras clínicas salvo el esquema del tumor, captura exacta, decisión suya),
// el visor del hígado sin rótulos, el mapa público de la Anatomía y las cifras del día (cifras.json).
import React from 'react';
import {AbsoluteFill, Audio, Img, Sequence, Solid, interpolate, spring, staticFile, useCurrentFrame, useVideoConfig, Easing} from 'remotion';
import {lightLeak} from '@remotion/effects/light-leak';
import {loadFont as fFraunces} from '@remotion/google-fonts/Fraunces';
import {loadFont as fHanken} from '@remotion/google-fonts/HankenGrotesk';
import {loadFont as fMono} from '@remotion/google-fonts/JetBrainsMono';
import T from '../public/timeline.json';
import CIFRAS from '../public/cifras.json';
import {TEXTOS} from './textos';

const {fontFamily: FRAUNCES} = fFraunces('normal', {weights: ['600'], subsets: ['latin']});
const {fontFamily: HANKEN} = fHanken('normal', {weights: ['500', '700'], subsets: ['latin']});
const {fontFamily: MONO} = fMono('normal', {weights: ['400', '600'], subsets: ['latin']});

const C = {berenjena: '#2d1b3d', crema: '#faf6f0', violeta: '#a44db2', violetaOsc: '#c77dd2', coral: '#ff6b47', lienzo: '#1d1127'};
const STAR_D = 'M10 1.6 C10.8 5,11.4 6.2,12.6 7.4 C14 8.8,16.4 9.4,18.4 10 C16.2 10.8,14.2 11.4,12.8 12.7 C11.5 13.9,10.9 15.7,10 18.4 C9.3 15.9,8.5 14.2,7.2 12.9 C5.8 11.6,3.4 10.7,1.6 10 C3.8 9.1,5.8 8.4,7.2 7.1 C8.4 6,9.2 4.2,10 1.6 Z';

// ---------- tiempo ----------
type Palabra = {w: string; a: number; b: number};
type Tramo = {id: string; escena: string; t0: number; t1: number; texto: string; palabras: Palabra[]};
const TR = T.tramos as Tramo[];
const X = TEXTOS[(T as any).idioma || 'en'];
const ANCLAS: Record<string, string> = (T as any).anclas || {};  // clave EN → prefijo en su idioma
const norm = (s: string) => s.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().replace(/[^a-z0-9-]/g, '');
const tramo = (id: string) => TR.find((x) => x.id === id)!;
const CORTE = 0.2;
const ventana = (id: string): [number, number] => {
  const i = TR.findIndex((x) => x.id === id);
  const a = i === 0 ? 0 : TR[i].t0 - CORTE;
  const b = i + 1 < TR.length ? TR[i + 1].t0 - CORTE : T.total + 1;
  return [a, b];
};
// instante en que dice una palabra (por prefijo), dentro de una frase
const dice = (id: string, pref: string, n = 0) => {
  const p = ANCLAS[pref] ?? norm(pref);
  const ws = tramo(id).palabras.filter((w) => norm(w.w).startsWith(p));
  return (ws[n] || ws[0] || {a: tramo(id).t0}).a;
};
const cl = (x: number, a = 0, b = 1) => Math.min(b, Math.max(a, x));
const lin = (t: number, a: number, b: number) => cl((t - a) / (b - a));
const suave = (x: number) => 1 - Math.pow(1 - x, 3);
const rebote = (x: number) => {const c1 = 1.70158, c3 = c1 + 1; return x <= 0 ? 0 : x >= 1 ? 1 : 1 + c3 * Math.pow(x - 1, 3) + c1 * Math.pow(x - 1, 2);};

const useT = () => {const f = useCurrentFrame(); const {fps} = useVideoConfig(); return f / fps;};
// muelle anclado a un instante en segundos (física de Remotion: damping alto = sin rebote, bajo = con rebote)
const useMuelle = () => {
  const f = useCurrentFrame(); const {fps} = useVideoConfig();
  return (tSeg: number, damping = 14, mass = 0.7) => spring({frame: f - Math.round(tSeg * fps), fps, config: {damping, mass, stiffness: 140}});
};

// opacidad + barrido de entrada/salida de una escena (entra desde la derecha, sale hacia la izquierda)
// entra/sale = false: sin barrido, porque esa costura es una transformación (algo sobrevive y se convierte en lo siguiente)
const useEscena = (id: string, {entra = true, sale = true} = {}) => {
  const t = useT(); const [a, b] = ventana(id);
  const e = entra ? 1 - suave(lin(t, a, a + 0.45)) : 0, s = sale ? lin(t, b - 0.25, b) : 0;
  const op = t < a || t >= b ? 0 : Math.min(entra ? suave(lin(t, a, a + 0.35)) : 1, 1 - s);
  return {op, style: {opacity: op, transform: `translateX(${140 * e - 160 * s * s}px) scale(${1.035 - 0.035 * suave(lin(t, a, b)) + 0.02 * e})`,
    filter: 14 * e + 16 * s > 0.3 ? `blur(${14 * e + 16 * s}px)` : undefined} as React.CSSProperties, t, a, b};
};

// ---------- piezas ----------
const Estrella: React.FC<{size: number; color: string; style?: React.CSSProperties; mitad?: 'izq' | 'der'}> = ({size, color, style, mitad}) => (
  <svg viewBox="0 0 20 20" width={size} height={size} style={{position: 'absolute', overflow: 'visible', ...style}}>
    <defs>
      <clipPath id="izq"><rect x="0" y="0" width="10" height="20" /></clipPath>
      <clipPath id="der"><rect x="10" y="0" width="10" height="20" /></clipPath>
    </defs>
    <path d={STAR_D} fill={color} clipPath={mitad ? `url(#${mitad})` : undefined} />
  </svg>
);

const Secuencia: React.FC<{dir: string; n: number; t: number; style: React.CSSProperties}> = ({dir, n, t, style}) => (
  <Img src={staticFile(`${dir}/${String(1 + (Math.floor(t * 30) % n)).padStart(4, '0')}.jpg`)} style={style} />
);

const Cielo: React.FC = () => {
  const t = useT();
  const est = React.useMemo(() => {let s = 7; const r = () => (s = (s * 16807) % 2147483647) / 2147483647;
    return Array.from({length: 80}, () => ({x: r() * 1920, y: r() * 1080, z: r(), f: r() * 6.28}));}, []);
  return (
    <svg width={1920} height={1080} style={{position: 'absolute', inset: 0}}>
      {est.map((e, k) => {const boost = 1 + 7 * Math.max(0, 1 - Math.abs(t - 4.5) / 4.5) * (t < 9 ? 1 : 0);   // salto en la apertura
        const v = (6 + 30 * e.z), desp = v * t + (t < 9 ? 7 * v * (t - Math.sin(t * Math.PI / 9) * 9 / Math.PI) / 2 : 7 * v * 4.5);
        const x = ((e.x - desp) % 1940 + 1940) % 1940 - 10, sz = 3 + 9 * e.z;
        return <g key={k}>{boost > 1.5 && <line x1={x} y1={e.y + sz / 2} x2={x + v * (boost - 1) * 0.6} y2={e.y + sz / 2} stroke={C.violetaOsc} strokeWidth={1.2}
          opacity={0.25 * e.z * (boost - 1) / 7} />}<path d={STAR_D} fill={C.violetaOsc} opacity={(0.15 + 0.4 * e.z) * (0.6 + 0.4 * Math.sin(t * 1.3 + e.f))}
          transform={`translate(${x},${e.y}) scale(${sz / 20})`} /></g>;})}
    </svg>
  );
};

// palabra clave con golpe arriba y subrayado violeta que se dibuja
const KW: [string, string, string][] = [['problema', 'two', X.dosCaras], ['olvido', 'lot', X.muchoMas]];  // en el resto manda la casilla OUTPUT de la barra
const PalabraClave: React.FC = () => {
  const t = useT();
  const vivas = KW.map(([id, p, txt]) => ({t0: dice(id, p) - 0.05, fin: ventana(id)[1] - 0.15, txt})).filter((k) => t >= k.t0 && t < k.fin);
  const k = vivas[vivas.length - 1]; if (!k) return null;
  const g = rebote(lin(t, k.t0, k.t0 + 0.4)), raya = suave(lin(t, k.t0 + 0.1, k.t0 + 0.45));
  return (
    <div style={{position: 'absolute', left: 0, right: 0, top: 36, textAlign: 'center'}}>
      <div style={{fontFamily: FRAUNCES, fontWeight: 600, fontSize: 86, color: C.violetaOsc, letterSpacing: '-0.02em', opacity: cl(g * 1.5), transform: `scale(${1.35 - 0.35 * g})`}}>{k.txt}</div>
      <div style={{width: 240, height: 5, margin: '8px auto 0', borderRadius: 3, background: C.violetaOsc, transform: `scaleX(${raya})`}} />
    </div>
  );
};

// subtítulos: trozos de 2-4 palabras con los tiempos REALES de su voz; la palabra que dice, en pastilla violeta
// Lo que ya está escrito entero en un rótulo grande no se subtitula: sería el mismo texto dos veces a la vez
// (launch-video-kit, 28-sep). Tramo → [primera, última] palabra rotulada (null = desde el principio, '*' = hasta el final).
// Las palabras clave sueltas (TWO SIDES., Go further., AND A LOT MORE.) sí llevan subtítulo: la frase dice más que el rótulo.
// {{TITULAR}}, 28-sep: los golpes del principio también salían dos veces.
const ROTULADO: Record<string, [string | null, string]> = X.rotulado;  // por idioma: las palabras van en textos.ts
// se quitan las PALABRAS rotuladas antes de trocear (si no, «disease.» se colaba pegada a «And nothing»)
const visibles = (x: Tramo) => {
  const r = ROTULADO[x.id]; if (!r) return x.palabras;
  const ini = r[0] ? x.palabras.find((p) => p.w === r[0]) : x.palabras[0];
  const fin = r[1] === '*' ? x.palabras[x.palabras.length - 1] : x.palabras.find((p) => p.w === r[1]);
  return ini && fin ? x.palabras.filter((p) => p.a < ini.a || p.a > fin.a) : x.palabras;
};
const TROZOS = TR.flatMap((x) => {
  const out: Palabra[][] = []; let cur: Palabra[] = [];
  for (const w of visibles(x)) {
    const largo = [...cur, w].map((p) => p.w).join(' ').length;
    if (cur.length && (cur.length >= 4 || largo > 24)) {out.push(cur); cur = [];}
    cur.push(w); if (/[,.!?:]$/.test(w.w) && cur.length >= 2) {out.push(cur); cur = [];}
  }
  if (cur.length) out.push(cur);
  return out;
});
const Subtitulos: React.FC = () => {
  const t = useT();
  const i = TROZOS.findIndex((c, k) => t >= c[0].a - 0.05 && t < (TROZOS[k + 1] ? Math.min(TROZOS[k + 1][0].a - 0.05, c[c.length - 1].b + 0.6) : c[c.length - 1].b + 0.6));
  if (i < 0) return null;
  const c = TROZOS[i], g = rebote(lin(t, c[0].a - 0.05, c[0].a + 0.12));
  const [ca, cb] = ventana('mas-alla'), claro = t >= ca - 0.1 && t < cb - 0.2;  // fondo crema
  return (
    <div style={{position: 'absolute', left: 0, right: 0, bottom: 70, textAlign: 'center', fontFamily: HANKEN, fontWeight: 700, fontSize: 64, lineHeight: 1.1,
      color: claro ? C.berenjena : C.crema, letterSpacing: '-0.01em',
      textShadow: claro ? 'none' : '0 2px 0 rgba(29,17,39,.9), 0 0 18px rgba(29,17,39,.85), 0 0 4px rgba(29,17,39,1)',
      transform: `translateY(${10 * (1 - g)}px) scale(${0.96 + 0.04 * g})`}}>
      {c.map((w, k) => {const activa = t >= w.a && t < (c[k + 1] ? c[k + 1].a : w.b + 0.3);
        return <span key={k} style={{display: 'inline-block', padding: '2px 12px', borderRadius: 12, background: activa ? C.violeta : 'transparent',
          textShadow: activa ? 'none' : undefined, opacity: t >= w.a - 0.03 ? 1 : 0.55, transform: `scale(${activa ? 1.06 : 1})`}}>{w.w}</span>;})}
    </div>
  );
};

const Marco: React.FC<{x: number; y: number; w: number; h: number; bg?: string; children: React.ReactNode; op?: number}> = ({x, y, w, h, bg = C.lienzo, children, op = 1}) => (
  <div style={{position: 'absolute', left: x, top: y, width: w, height: h, borderRadius: 26, overflow: 'hidden', background: bg,
    boxShadow: '0 30px 80px rgba(0,0,0,.35)', opacity: op}}>{children}</div>
);

const Luz: React.FC<{seed: number; hue?: number}> = ({seed, hue = 290}) => {
  const f = useCurrentFrame(); const {durationInFrames, width, height} = useVideoConfig();
  // destello, no telón: al 50 % y en «screen» (a tope tapaba la pantalla entera); hue 110-120 = violeta de marca
  return <AbsoluteFill style={{opacity: 0.5, mixBlendMode: 'screen'}}><Solid width={width} height={height} effects={[lightLeak({seed, hueShift: hue, progress: interpolate(f, [0, durationInFrames - 1], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'})})]} /></AbsoluteFill>;
};

// ---------- escenas ----------
const Gancho: React.FC = () => {
  const t = useT(); const t0 = tramo('problema').t0; const p = suave(lin(t, t0 - 0.35, t0 + 0.1));
  if (p >= 1) return null;
  return (
    <AbsoluteFill style={{background: `rgba(45,27,61,${1 - p})`, alignItems: 'center', justifyContent: 'center', opacity: 1 - lin(t, t0 - 0.1, t0 + 0.1)}}>
      <div style={{fontFamily: FRAUNCES, fontWeight: 600, fontSize: 150, color: C.crema, letterSpacing: '-0.03em', lineHeight: 0.95, transform: `translateY(${-260 * p}px)`}}>{X.gancho[0]}</div>
      <div style={{fontFamily: FRAUNCES, fontWeight: 600, fontSize: 150, color: C.violetaOsc, letterSpacing: '-0.03em', lineHeight: 0.95, transform: `translateY(${260 * p}px)`}}>{X.gancho[1]}</div>
      {/* tres destellos de producto: el mapa 3D, el radar y la puerta con su firma (visibles ya en el fotograma 0: miniatura) */}
      <div style={{display: 'flex', gap: 28, marginTop: 50, opacity: 1 - suave(lin(t, t0 - 0.4, t0 - 0.1)), transform: `translateY(${120 * p}px)`}}>
        {[['esqueleto.png', X.ganchoChips[0]], ['', X.ganchoChips[1]], ['', X.ganchoChips[2]]].map(([img, lbl], i) => (
          <div key={lbl} style={{width: 220, height: 130, borderRadius: 18, overflow: 'hidden', position: 'relative', background: C.lienzo,
            border: `2px solid ${i === 2 ? C.coral : 'rgba(199,125,210,.5)'}`, boxShadow: `0 0 ${16 + 10 * Math.sin(t * 8 + i)}px rgba(199,125,210,.35)`}}>
            {img ? <Img src={staticFile(img)} style={{position: 'absolute', left: 50, top: -40, width: 120}} /> : i === 1 ?
              <svg viewBox="-60 -60 120 120" style={{position: 'absolute', left: 60, top: 10, width: 100, height: 100}}>
                <circle r={50} fill="none" stroke={C.violetaOsc} strokeWidth={1.5} />
                {Array.from({length: 24}, (_, k) => <circle key={k} cx={40 * Math.cos(k * 0.9 + t)} cy={40 * Math.sin(k * 1.7)} r={2} fill={C.violetaOsc} />)}
              </svg> :
              <div style={{position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: MONO, fontSize: 20,
                letterSpacing: '0.14em', color: C.coral}}>{X.puertaHumana}</div>}
            <div style={{position: 'absolute', left: 0, right: 0, bottom: 0, padding: '18px 0 6px', textAlign: 'center', fontFamily: MONO, fontSize: 15, letterSpacing: '0.12em',
              textTransform: 'uppercase', color: C.crema, background: 'linear-gradient(transparent, rgba(29,17,39,.95) 55%)'}}>{lbl}</div>
          </div>))}
      </div>
    </AbsoluteFill>
  );
};

const GOLPES: [string, string, string][] = X.golpes.map(([p, txt, violeta]) => [p, txt, violeta ? C.violetaOsc : C.crema]);
const Apertura: React.FC = () => {
  const t = useT(); const m = useMuelle();
  const tSale = dice('problema', 'tumor') - 0.1;
  if (t < tramo('problema').t0 - 0.1 || t > tSale + 0.5) return null;
  const idx = GOLPES.map(([p]) => dice('problema', p) - 0.04);
  const sale = suave(lin(t, tSale, tSale + 0.45));
  return (
    <AbsoluteFill style={{alignItems: 'center', justifyContent: 'center', opacity: 1 - sale, transform: `scale(${1 + 0.4 * sale})`, filter: sale > 0.05 ? `blur(${12 * sale}px)` : undefined}}>
      {GOLPES.map(([p, txt, col], i) => {
        if (t < idx[i]) return null;
        const g = m(idx[i], 11, 0.6);                                  // golpe: entra grande y se asienta
        const n = idx.filter((x) => t >= x).length - 1 - i;              // cuántas palabras han llegado después
        const hundida = n > 0 ? suave(lin(t, idx[i + 1], idx[i + 1] + 0.35)) + (n - 1) : 0;
        const shake = i === idx.filter((x) => t >= x).length - 1 ? Math.sin(t * 90) * 6 * Math.max(0, 1 - (t - idx[i]) * 6) : 0;
        return <div key={p} style={{position: 'absolute', fontFamily: FRAUNCES, fontWeight: 600, fontSize: 185, letterSpacing: '-0.035em', color: col, whiteSpace: 'nowrap',
          opacity: cl(g * 1.5) * Math.max(0, 1 - 0.55 * hundida), transform: `translate(${shake}px, ${-150 * hundida}px) scale(${(1.6 - 0.6 * g) * (1 - 0.3 * Math.min(1.5, hundida))})`,
          filter: g < 0.95 ? `blur(${14 * (1 - g)}px)` : hundida > 0 ? `blur(${3 * hundida}px)` : undefined}}>{txt}</div>;
      })}
      {idx.map((x, i) => {const p = lin(t, x, x + 0.6); if (p <= 0 || p >= 1) return null;   // destello en cada golpe
        return <div key={'f' + i} style={{position: 'absolute', width: 300 + 1500 * suave(p), height: 300 + 1500 * suave(p), borderRadius: '50%',
          background: 'radial-gradient(circle, rgba(199,125,210,.35), transparent 60%)', opacity: 1 - p}} />;})}
    </AbsoluteFill>
  );
};

const Problema: React.FC = () => {
  const t = useT(); const m = useMuelle(); const [, fin] = ventana('problema');
  const tDos = dice('problema', 'two'), tLados = dice('problema', 'sides');
  const encoge = suave(lin(t, tDos - 0.7, tDos - 0.1));
  const higOp = 1 - lin(t, tDos - 0.35, tDos - 0.1);
  const nace = 0.15 + 0.85 * m(tDos - 0.45, 10);  // nace en «two sides» (antes estaba sola en el centro: {{TITULAR}}, «raro»)
  const sep = 70 * suave(lin(t, tDos, tDos + 0.5)) + 570 * suave(lin(t, tLados + 0.1, tLados + 0.7));
  const vuela = 1500 * Math.pow(lin(t, fin - 0.45, fin), 2);
  const estOp = cl(lin(t, tDos - 0.45, tDos - 0.2));
  const llega = m(tLados + 0.1, 16), sale = 0;  // costura 1: la tarjeta se queda y crece hasta ser la página de «Go further»
  const anillo = lin(t, tDos, tDos + 1.1);
  if (t >= fin) return null;
  return (
    <AbsoluteFill>
      {anillo > 0 && anillo < 1 && <div style={{position: 'absolute', left: 960 - (40 + 450 * suave(anillo)), top: 520 - (40 + 450 * suave(anillo)), width: 2 * (40 + 450 * suave(anillo)),
        height: 2 * (40 + 450 * suave(anillo)), borderRadius: '50%', border: `3px solid ${C.violetaOsc}`, opacity: (1 - anillo) * 0.9}} />}
      {[['izq', -1, C.violetaOsc], ['der', 1, C.coral]].map(([lado, s, col]) => (
        <div key={lado as string} style={{position: 'absolute', left: 960 - 180, top: 520 - 180, width: 360, height: 360, opacity: estOp,
          transform: `translateX(${(s as number) * sep + (s === 1 ? vuela : 0)}px) scale(${nace})`, filter: t < tDos ? 'drop-shadow(0 0 30px rgba(199,125,210,.6))' : undefined}}>
          <Estrella size={360} color={col as string} mitad={lado as 'izq' | 'der'} />
        </div>))}
      {[['izq', -1], ['der', 1]].map(([lado, s]) => (
        <Img key={lado as string} src={staticFile('dos-caras.png')} style={{position: 'absolute', left: 460, top: 256, width: 1000, borderRadius: 22,
          boxShadow: '0 30px 80px rgba(0,0,0,.35)', clipPath: lado === 'izq' ? 'inset(0 50% 0 0)' : 'inset(0 0 0 50%)',
          opacity: cl(lin(t, tLados + 0.05, tLados + 0.3)) * (1 - sale),
          transform: `translateX(${(s as number) * 520 * (1 - llega) + (s === 1 ? 1500 * sale : 0)}px) scale(${0.9 + 0.1 * llega})`}} />))}
    </AbsoluteFill>
  );
};

// Costura 1: la tarjeta de las dos caras (460,256 · 1000×529) crece hasta ser esta página.
// Costura 2: la página se pliega en su subrayado y esa línea se abre en horizonte, de donde sube su foto.
const TARJETA = {x: 460, y: 256, w: 1000, h: 529}, LINEA_Y = 651;
const MasAlla: React.FC = () => {
  const t = useT(); const m = useMuelle(); const [a, b] = ventana('mas-alla');
  if (t < a - 0.35 || t >= b + 0.45) return null;
  const g = suave(lin(t, a - 0.3, a + 0.25)), pliega = suave(lin(t, b - 0.4, b));
  const ins = (d: number) => d * (1 - g);
  const top = ins(TARJETA.y) + pliega * (LINEA_Y - 3), bot = ins(1080 - TARJETA.y - TARJETA.h) + pliega * (1080 - LINEA_Y - 3);
  const tGo = dice('mas-alla', 'go'), tFur = dice('mas-alla', 'further');
  if (t >= b) {  // solo queda la línea: se abre a lo ancho y se apaga mientras sube la foto
    const h = lin(t, b, b + 0.45);
    return <div style={{position: 'absolute', left: 960 - 410 - 550 * suave(h), width: 820 + 1100 * suave(h), top: LINEA_Y - 3, height: 6, borderRadius: 3,
      background: C.crema, opacity: 1 - h}} />;
  }
  return (
    <AbsoluteFill style={{background: C.crema, opacity: cl(lin(t, a - 0.35, a - 0.22)), alignItems: 'center', justifyContent: 'center',
      clipPath: `inset(${top}px ${ins(TARJETA.x)}px ${bot}px ${ins(1920 - TARJETA.x - TARJETA.w)}px round ${22 * (1 - g)}px)`}}>
      <div style={{fontFamily: FRAUNCES, fontWeight: 600, fontSize: 170, color: C.berenjena, letterSpacing: '-0.03em'}}>
        {[[X.masAlla[0], tGo], [X.masAlla[1], tFur]].map(([w, tw]) => {const k = m(tw as number - 0.05, 10);
          return <span key={w as string} style={{display: 'inline-block', margin: '0 18px', opacity: cl(k * 1.5), transform: `translateY(${60 * (1 - k)}px) scale(${0.8 + 0.2 * k})`}}>{w}</span>;})}
      </div>
      <div style={{width: 820, height: 6, marginTop: 24, background: C.berenjena, borderRadius: 3, transformOrigin: 'left center',
        transform: `scaleX(${suave(lin(t, tFur + 0.2, tFur + 0.9))})`}} />
    </AbsoluteFill>
  );
};

const Mapa: React.FC<{size: number; t: number; style?: React.CSSProperties}> = ({size, t, style}) => (
  <div style={{position: 'absolute', width: size, height: size, borderRadius: '50%', overflow: 'hidden', background: C.berenjena, ...style}}>
    <Secuencia dir="mapa" n={120} t={t} style={{width: '100%', height: '100%'}} />
    <div style={{position: 'absolute', left: '50%', top: '50%', width: '19%', height: '19%', transform: 'translate(-50%,-50%)', borderRadius: '50%',
      background: C.violeta, display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: FRAUNCES, fontWeight: 600,
      fontSize: size * 0.045, color: C.crema, boxShadow: '0 0 60px 20px rgba(164,77,178,.45)'}}>POLARIS</div>
  </div>
);

const PolarisEsc: React.FC = () => {
  const e = useEscena('polaris', {entra: false}); const m = useMuelle(); const t = e.t;  // costura 2: su foto sube desde la línea, sin barrido
  if (e.op <= 0) return null;
  const drop = T.drop, tTeam = dice('polaris', 'team'), tNed = dice('polaris', 'ned'), tPuerta = dice('polaris', 'nothing');
  const zin = m(drop - 0.08, 11), zNed = Math.pow(lin(t, tNed - 0.45, tNed + 0.05), 2);
  const mapaOp = cl(zin * 1.3) * (1 - lin(t, tNed - 0.3, tNed - 0.05));
  const ned = m(tNed + 0.02, 10) * (1 - lin(t, tPuerta - 0.3, tPuerta));
  const vuelta = 2.2, fase = t - tPuerta - 1.6, u = fase > 0 ? (fase % vuelta) / vuelta : -1, pos = u * 4, ip = Math.min(3, Math.floor(Math.max(0, pos)));
  return (
    <AbsoluteFill style={e.style}>
      {(() => {const t0 = tramo('polaris').t0, ent = m(t0 - 0.15, 12), sale = suave(lin(t, drop - 0.12, drop + 0.35));
        if (t > drop + 0.4) return null;
        return <>
          <Img src={staticFile('titular-ingeniera.png')} style={{position: 'absolute', left: 960 - 330, top: 1080 - 1000 * ent + 260 * sale, height: 1300,
            opacity: cl(ent * 1.4) * (1 - sale), transform: `scale(${1 - 0.15 * sale})`, filter: sale > 0.05 ? `blur(${18 * sale}px)` : undefined,
            WebkitMaskImage: 'linear-gradient(#000 70%, transparent 92%)'}} />
          <div style={{position: 'absolute', left: 1330, top: 430, fontFamily: MONO, fontSize: 30, letterSpacing: '0.14em', textTransform: 'uppercase', color: C.violetaOsc,
            opacity: suave(lin(t, t0 + 0.2, t0 + 0.5)) * (1 - sale), transform: `translateX(${40 * (1 - suave(lin(t, t0 + 0.2, t0 + 0.6)))}px)`}}>
            <div style={{fontFamily: FRAUNCES, fontWeight: 600, fontSize: 56, letterSpacing: '-0.02em', textTransform: 'none', color: C.crema}}>{X.nombre}</div>{X.rol}</div>
        </>;})()}
      <Mapa size={620} t={t} style={{left: 960 - 310, top: 100, opacity: mapaOp, transform: `scale(${(0.6 + 0.4 * zin) * (1 + 5 * zNed)})`}} />
      <div style={{position: 'absolute', left: 0, right: 0, top: 800, textAlign: 'center', fontFamily: MONO, fontSize: 32, letterSpacing: '0.14em', textTransform: 'uppercase',
        color: C.violetaOsc, opacity: suave(lin(t, tTeam, tTeam + 0.3)) * (1 - lin(t, tNed - 0.4, tNed - 0.2))}}>{X.equipo}</div>
      {!X.nedDespliega ? <>
      <div style={{position: 'absolute', left: 0, right: 0, top: 200, textAlign: 'center', opacity: cl(ned * 1.4), transform: `scale(${0.7 + 0.3 * ned})`}}>
        <div style={{fontFamily: FRAUNCES, fontWeight: 600, fontSize: 300, color: C.crema, letterSpacing: '-0.04em', lineHeight: 1}}>NED</div>
        <div style={{fontFamily: HANKEN, fontWeight: 500, fontSize: 44, color: C.crema, marginTop: 10}}>{X.nedSub}</div>
      </div>
      </> : null}
      {/* {{TITULAR}}, 28-sep: las palabras de NED salen de cada inicial. N·E·D se abren y cada una se despliega en su palabra
          justo cuando ella la dice; «of» aparece entre medias. En ES las iniciales no casan (sin evidencia de enfermedad):
          ahí va NED con su subtítulo, como en la v16. */}
      {X.nedDespliega ? (() => {const tNo = dice('polaris', 'no'), tEv = dice('polaris', 'evidence'), tOf = dice('polaris', 'of', 1), tDis = dice('polaris', 'disease');
        const abre = suave(lin(t, tNo - 0.35, tNo + 0.1));
        const trozo = (txt: string, t0: number, dur: number) => txt.slice(0, Math.round(txt.length * suave(lin(t, t0, t0 + dur))));
        const ini = {color: C.crema}, resto = {color: C.violetaOsc};
        return <div style={{position: 'absolute', left: 0, right: 0, top: 330 - 130 * (1 - abre), textAlign: 'center', whiteSpace: 'nowrap', opacity: cl(ned * 1.4),
          transform: `scale(${0.7 + 0.3 * ned})`, fontFamily: FRAUNCES, fontWeight: 600, fontSize: 300 - 180 * abre, letterSpacing: `${-0.04 + 0.03 * abre}em`, lineHeight: 1}}>
          <span style={ini}>N</span><span style={resto}>{trozo('o', tNo - 0.05, 0.15)}</span>
          <span style={{display: 'inline-block', width: `${0.3 * abre}em`}} />
          <span style={ini}>E</span><span style={resto}>{trozo('vidence', tEv - 0.05, 0.3)}</span>
          <span style={{display: 'inline-block', width: `${0.3 * abre}em`}} />
          <span style={{...resto, opacity: suave(lin(t, tOf - 0.05, tOf + 0.15))}}>{abre > 0.5 ? 'of' : ''}</span>
          <span style={{display: 'inline-block', width: `${0.3 * abre}em`}} />
          <span style={ini}>D</span><span style={resto}>{trozo('isease', tDis - 0.05, 0.3)}</span>
        </div>;})() : null}
    </AbsoluteFill>
  );
};


// La columna vertebral (director creativo, 28-sep): el mismo sistema produce cada cosa que se ve después.
const NODOS = X.nodos;
const XS = [260, 610, 960, 1310, 1660];
const XB = XS.map((x) => 960 + (x - 960) * 0.92);
const SALIDA: [string, string][] = X.salida;
const Cadena: React.FC = () => {
  const t = useT(); const m = useMuelle();
  const tPuerta = dice('polaris', 'nothing'), tFirma = dice('polaris', 'sign-off');
  const [, finPol] = ventana('polaris'), [, finWeb] = ventana('web');
  if (t < tPuerta - 0.1 || t >= finWeb) return null;
  const k = suave(lin(t, finPol - 0.5, finPol + 0.1));          // 0 = grande, 1 = barra
  const op = 1 - lin(t, finWeb - 0.3, finWeb);
  const lerp = (a: number, b: number) => a + (b - a) * k;
  const cy = lerp(405, 66), w = lerp(300, 200), wOut = lerp(300, 300), h = lerp(150, 70);
  const activa = SALIDA.map(([id, txt]) => ({a: ventana(id)[0], b: ventana(id)[1], txt})).find((x) => t >= x.a && t < x.b);
  const firma = m(tFirma - 0.05, 8), brillo = t >= tFirma - 0.05 ? 1 : 0;
  // la puerta late cada vez que le llega un borrador (escena carga)
  const llegadas = [dice('carga', 'takes') + 0.7];
  const golpe = Math.max(0, ...llegadas.map((x) => 1 - Math.abs(t - x) * 4));
  const vuelta = 2.2, fase = t - tPuerta - 1.6, u = fase > 0 ? (fase % vuelta) / vuelta : -1, pos = u * 4, ip = Math.min(3, Math.floor(Math.max(0, pos)));
  const xs = XS.map((x, i) => lerp(x, XB[i]));
  return (
    <AbsoluteFill style={{opacity: op}}>
      {NODOS.map(([lbl, val], i) => {const g = m(tPuerta + i * 0.25, 13); const ww = i === 4 ? wOut : w;
        const cerca = u >= 0 ? Math.max(0, 1 - Math.abs(pos - i) * 1.6) : 0;
        const esPuerta = i === 3, glow = esPuerta ? Math.max(brillo * (0.6 + 0.4 * Math.sin((t - tFirma) * 5)) * (1 - k * 0.6), golpe) : 0;
        const valor = i === 4 && activa ? activa.txt : val;
        return <div key={lbl} style={{position: 'absolute', left: xs[i] - ww / 2, top: cy - h / 2, width: ww, height: h, borderRadius: lerp(22, 14),
          textAlign: 'center', paddingTop: lerp(28, 9), border: `2px solid ${esPuerta ? C.coral : 'rgba(199,125,210,.55)'}`,
          background: esPuerta ? `rgba(255,107,71,${0.12 + 0.2 * glow})` : 'rgba(45,27,61,.85)', opacity: cl(g * 1.3),
          transform: `translateY(${16 * (1 - g)}px) scale(${(esPuerta && t < finPol ? 1 + 0.12 * firma * (1 - lin(t, tFirma + 0.6, tFirma + 1.2)) : 1) + 0.12 * golpe})`,
          boxShadow: glow > 0 ? `0 0 ${50 * glow}px ${12 * glow}px rgba(255,107,71,.45)` : cerca > 0 ? `0 0 ${40 * cerca}px ${8 * cerca}px rgba(199,125,210,.45)` : undefined}}>
          <div style={{fontFamily: MONO, fontSize: lerp(26, 14), letterSpacing: '0.14em', textTransform: 'uppercase', color: esPuerta ? C.coral : C.violetaOsc}}>{lbl}</div>
          <div style={{fontFamily: FRAUNCES, fontWeight: 600, fontSize: lerp(36, 21), color: C.crema, marginTop: lerp(8, 2), whiteSpace: 'nowrap'}}>{valor}</div>
        </div>;})}
      {[0, 1, 2, 3].map((i) => {const a = xs[i] + w / 2 + 2, b = xs[i + 1] - (i === 3 ? wOut : w) / 2 - 2;
        return <div key={i} style={{position: 'absolute', left: a, top: cy - 1.5, width: Math.max(0, b - a), height: 3, background: C.violetaOsc, transformOrigin: 'left center',
          transform: `scaleX(${suave(lin(t, tPuerta + 0.2 + i * 0.25, tPuerta + 0.5 + i * 0.25))})`}} />;})}
      {u >= 0 && <div style={{position: 'absolute', top: cy + h / 2 + lerp(20, 10) - 9, left: xs[ip] + (xs[ip + 1] - xs[ip]) * suave(pos - ip) - 9, width: 18, height: 18,
        borderRadius: '50%', background: C.crema, boxShadow: '0 0 24px 8px rgba(199,125,210,.6)', transform: `scale(${lerp(1, 0.6)})`}} />}
    </AbsoluteFill>
  );
};

const Aprender: React.FC = () => {
  const e = useEscena('aprender'); if (e.op <= 0) return null;
  const tVer = dice('aprender', 'seeing'), va = 1 - suave(lin(e.t, tVer - 0.2, tVer + 0.1));
  if (va <= 0) return null;
  return (
    <AbsoluteFill style={{...e.style, opacity: (e.style.opacity as number) * va, transform: `${e.style.transform} scale(${1 + 0.08 * (1 - va)})`}}>
      <Marco x={150} y={250} w={900} h={300} bg="#f5efe6"><Img src={staticFile('science.png')} style={{position: 'absolute', left: 20, top: 75, width: 860}} /></Marco>
      <Marco x={1080} y={200} w={720} h={520}><Img src={staticFile('biopsia.png')} style={{position: 'absolute', left: '50%', top: '50%', height: 520,
        transform: `translate(-50%,-50%) scale(${1.05 + 0.08 * lin(e.t, e.a, e.b)})`}} /></Marco>
    </AbsoluteFill>
  );
};

// Galería de visores ({{TITULAR}}, 28-sep: «la mama, el hígado, el esqueleto y algún hueso… es lo que más impresiona»).
// Sincronizada con su voz: «seeing» → mama · «turned my scans» → hígado · «3D maps» → los cuatro a la vez ·
// «bone map… which one to biopsy» → zoom lento al esqueleto con sus focos. En «biopsy» NO se enseña una vértebra concreta:
// la biopsia fue de otro hueso y la imagen lo daría a entender. Todo sin rótulos de medida (grabados con ellos ocultos).
const Galeria: React.FC = () => {
  const t = useT(); const m = useMuelle();
  const t1 = dice('aprender', 'seeing') - 0.1, t2 = dice('3d', 'turned') - 0.1, t3 = dice('3d', '3d') - 0.1, t4 = dice('3d', 'bone') - 0.1, [, fin] = ventana('3d');
  if (t < t1 - 0.05 || t >= fin) return null;
  const op = Math.min(suave(lin(t, t1 - 0.05, t1 + 0.3)), 1 - lin(t, fin - 0.25, fin));
  const rot = {fontFamily: MONO, fontSize: 24, letterSpacing: '0.14em', textTransform: 'uppercase' as const, color: C.violetaOsc};
  const Pieza: React.FC<{i: number}> = ({i}) => i === 0 ? <Secuencia dir="mama" n={240} t={t} style={{width: '100%', height: '100%'}} />
    : i === 1 ? <Secuencia dir="higado" n={240} t={t} style={{width: '100%', height: '100%'}} />
    : i === 2 ? <Img src={staticFile('esqueleto.png')} style={{height: '96%', margin: '2% auto', display: 'block'}} />
    : <Img src={staticFile('vertebra.png')} style={{height: '92%', margin: '4% auto', display: 'block'}} />;
  const nombres = X.galeria;
  // fase grande (mama / hígado): un visor a pantalla, con empuje lento de cámara
  const grande = t < t3 ? (t < t2 ? 0 : 1) : -1;
  const kIn = m(grande === 0 ? t1 : t2, 13);
  // fase galería: los cuatro en fila; en «bone» el esqueleto crece hasta llenar y la cámara empuja hacia sus focos
  const xs = [300, 740, 1180, 1620], lado = 400, yG = 290;
  const zoomEsq = suave(lin(t, t4, t4 + 0.7)), empuje = lin(t, t4 + 0.7, fin);
  return (
    <AbsoluteFill style={{opacity: op}}>
      {grande >= 0 && <div style={{position: 'absolute', left: 960 - 350, top: 150, width: 700, height: 700, borderRadius: 30, overflow: 'hidden', background: C.lienzo,
        boxShadow: '0 30px 80px rgba(0,0,0,.45)', opacity: cl(kIn * 1.3), transform: `scale(${(0.85 + 0.15 * kIn) * (1 + 0.1 * lin(t, grande === 0 ? t1 : t2, grande === 0 ? t2 : t3))})`}}>
        <Pieza i={grande} />
        <div style={{...rot, position: 'absolute', left: 24, bottom: 18}}>{nombres[grande]} {X.sufijo3d}</div>
      </div>}
      {t >= t3 && [0, 1, 2, 3].map((i) => {
        const g = m(t3 + i * 0.12, 12), esq = false;
        const tam = lado, x = xs[i], y = yG;
        const otros = 1 - zoomEsq;
        return <div key={i} style={{position: 'absolute', left: x - tam / 2, top: y, width: tam, height: esq ? tam : lado, borderRadius: 24, overflow: 'hidden', background: C.lienzo,
          boxShadow: '0 24px 60px rgba(0,0,0,.4)', opacity: cl(g * 1.3) * otros, transform: `translateY(${50 * (1 - g)}px) scale(${0.85 + 0.15 * g})`, zIndex: esq ? 2 : 1}}>
          <div style={{position: 'absolute', inset: 0, transform: esq ? `scale(${1 + 0.45 * suave(empuje)}) translateY(${8 * suave(empuje)}%)` : undefined}}><Pieza i={i} /></div>
          <div style={{...rot, position: 'absolute', left: 18, bottom: 14, fontSize: 20}}>{nombres[i]}</div>
        </div>;})}
      {t >= t4 && (() => {
        // los tres huesos (galio · FDG · TC) salen de la casilla «Bone», crecen a pantalla y la cámara empuja hacia la diana mientras giran
        const w0 = lado, x0 = xs[3], w1 = 1640, h1 = w1 * 347 / 832, k = zoomEsq, w = w0 + (w1 - w0) * k, h = (w0 * 347 / 832) + (h1 - w0 * 347 / 832) * k;
        const x = x0 + (960 - x0) * k, y = (yG + lado / 2) + (500 - (yG + lado / 2)) * k;
        const f = Math.min(149, Math.floor((t - t4) * 55));
        return <div style={{position: 'absolute', left: x - w / 2, top: y - h / 2, width: w, height: h, borderRadius: 24, overflow: 'hidden', background: C.lienzo,
          boxShadow: '0 30px 80px rgba(0,0,0,.45)', zIndex: 3}}>
          <Img src={staticFile(`vertebra3/${String(f + 1).padStart(4, '0')}.jpg`)} style={{width: '100%', height: '100%',
            transform: `scale(${1 + 0.22 * suave(empuje)})`, transformOrigin: '50% 62%'}} />
        </div>;
      })()}
    </AbsoluteFill>
  );
};

const Radar: React.FC = () => {
  const e = useEscena('radar'); if (e.op <= 0) return null; const t = e.t;
  const orto = (la: number, lo: number, rot: number) => {const f = la * Math.PI / 180, l = (lo + rot) * Math.PI / 180;
    return [100 * Math.cos(f) * Math.sin(l), -100 * Math.sin(f), Math.cos(f) * Math.cos(l)];};
  const rot = t * 18, puntos: number[][] = [];
  for (let la = -80; la <= 80; la += 10) for (let lo = 0; lo < 360; lo += 10) puntos.push([la, lo]);
  const pings = Array.from({length: 14}, (_, k) => [((k * 53) % 140) - 70, (k * 97) % 360, (k * 0.37) % 1]);
  return (
    <AbsoluteFill style={e.style}>
      <svg viewBox="-110 -110 220 220" style={{position: 'absolute', left: 660, top: 175, width: 600, height: 600, transform: `scale(${0.9 + 0.1 * suave(lin(t, e.a, e.b))})`}}>
        <circle r={100} fill="none" stroke="rgba(199,125,210,.35)" strokeWidth={0.6} />
        {puntos.map(([la, lo], k) => {const [x, y, z] = orto(la, lo, rot); return <circle key={k} cx={x} cy={y} r={1.1} fill={C.violetaOsc} opacity={z > 0 ? 0.25 + 0.6 * z : 0} />;})}
        {pings.map(([la, lo, fase], k) => {const [x, y, z] = orto(la, lo, rot), p = (t * 0.8 + fase) % 1; return <circle key={'p' + k} cx={x} cy={y} r={1.5 + 4 * p} fill={C.crema} opacity={z > 0 ? 1 - p : 0} />;})}
        {pings.map((pA, k) => {const pB = pings[(k + 1) % pings.length]; const [x1, y1, z1] = orto(pA[0], pA[1], rot), [x2, y2, z2] = orto(pB[0], pB[1], rot);
          if (!(z1 > 0.1 && z2 > 0.1)) return null; const L = Math.hypot(x2 - x1, y2 - y1) * 1.3, p = (t * 0.6 + k * 0.29) % 1;
          return <path key={'a' + k} d={`M${x1},${y1} Q${(x1 + x2) / 2 * 1.35},${(y1 + y2) / 2 * 1.35} ${x2},${y2}`} fill="none" stroke={C.violetaOsc} strokeWidth={0.8}
            strokeLinecap="round" strokeDasharray={L} strokeDashoffset={L * (1 - p)} opacity={0.8 * (1 - p)} />;})}
      </svg>
      <div style={{position: 'absolute', left: 0, right: 0, top: 800, textAlign: 'center', fontFamily: MONO, fontSize: 30, letterSpacing: '0.14em', color: C.violetaOsc}}>{X.radar}</div>
    </AbsoluteFill>
  );
};

const Carga: React.FC = () => {
  // Todo nace en borrador y sale solo con su firma: los tres borradores vuelan a la casilla «My sign-off» de la barra
  const e = useEscena('carga'); const m = useMuelle(); if (e.op <= 0) return null; const t = e.t;
  const chips = X.chips;
  const puertaX = XB[3], puertaY = 66;
  return (
    <AbsoluteFill style={{opacity: e.op}}>
      {chips.map(([txt, p], i) => {const t0 = dice('carga', p) - 0.05, k = m(t0, 9);
        // se apilan una debajo de otra y se quedan; al decir «takes» vuelan JUNTAS a su casilla de firma ({{TITULAR}}, 28-sep)
        const tv = dice('carga', 'takes') + 0.1 + i * 0.07, vuela = suave(lin(t, tv, tv + 0.55));
        const x0 = 960, y0 = 390 + i * 160, x = x0 + (puertaX - x0) * vuela, y = y0 + (puertaY - y0) * vuela;
        return <div key={txt} style={{position: 'absolute', left: x, top: y, padding: '20px 40px', borderRadius: 999, fontFamily: HANKEN, fontWeight: 500,
          fontSize: 50, color: C.crema, border: '2px solid rgba(250,246,240,.35)', background: 'rgba(45,27,61,.9)', whiteSpace: 'nowrap',
          opacity: cl(k * 1.4) * (1 - lin(t, tv + 0.4, tv + 0.55)),
          transform: `translate(-50%,-50%) translateX(${(i % 2 ? 1 : -1) * 420 * (1 - k)}px) scale(${(0.85 + 0.15 * k) * (1 - 0.8 * vuela)})`}}>
          <span style={{fontFamily: MONO, fontSize: 20, letterSpacing: '0.14em', color: C.violetaOsc, marginRight: 18}}>{X.borrador}</span>{txt}</div>;})}
    </AbsoluteFill>
  );
};

const Web: React.FC = () => {
  // {{CONTACTO}}: la home con «Support {{TITULAR}}» se leía como una campaña de donaciones. Aquí, tres páginas donde comparte el caso.
  const e = useEscena('web', {sale: false}); const m = useMuelle(); if (e.op <= 0) return null; const t = e.t;
  const entra = suave(lin(t, e.b - 0.45, e.b));  // costura 3: las páginas caen al centro del anillo que viene
  const pags: [string, string, number][] = [['science.png', 'helptitular.com/science', 0], ['datos-cielo.png', 'helptitular.com/data', 1], ['esqueleto.png', 'helptitular.com/mapa-metastasis', 2]];
  return (
    <AbsoluteFill style={e.style}>
      {pags.map(([img, url, i]) => {const k = m(e.a + 0.15 + i * 0.35, 13);
        const cx = 250 + i * 330 + 380, cy = 190 + i * 60 + 260;
        return <div key={url} style={{position: 'absolute', left: 250 + i * 330, top: 190 + i * 60, width: 760, height: 520, borderRadius: 18, overflow: 'hidden',
          background: C.crema, boxShadow: '0 30px 80px rgba(0,0,0,.45)', opacity: cl(k * 1.3) * (1 - lin(t, e.b - 0.15, e.b)),
          transform: `translate(${(960 - cx) * entra}px, ${(470 - cy) * entra}px) perspective(1600px) rotateY(${-14 + 14 * k}deg) translateY(${60 * (1 - k)}px) scale(${(0.9 + 0.1 * k) * (1 - 0.9 * entra)})`}}>
          <div style={{height: 44, background: '#ece4d8', display: 'flex', alignItems: 'center', gap: 8, padding: '0 16px'}}>
            {[0, 1, 2].map((d) => <div key={d} style={{width: 12, height: 12, borderRadius: 6, background: 'rgba(45,27,61,.25)'}} />)}
            <div style={{marginLeft: 14, fontFamily: MONO, fontSize: 18, color: C.berenjena}}>{url}</div>
          </div>
          <div style={{position: 'absolute', top: 44, left: 0, right: 0, bottom: 0, background: img === 'science.png' ? '#f5efe6' : C.lienzo, display: 'flex',
            alignItems: 'center', justifyContent: 'center', overflow: 'hidden'}}>
            <Img src={staticFile(img)} style={img === 'esqueleto.png' ? {height: 460} : {width: 720}} />
          </div>
        </div>;})}
    </AbsoluteFill>
  );
};

const Cifras: React.FC = () => {
  const e = useEscena('olvido', {entra: false, sale: false}); const m = useMuelle(); if (e.op <= 0) return null; const t = e.t, t0 = tramo('olvido').t0;
  const cierra = suave(lin(t, e.b - 0.45, e.b));  // costura 4: todo se recoge en un punto, del que nace la frase de los médicos
  const pos: ['izq' | 'der', number][] = [['izq', 200], ['izq', 390], ['izq', 580], ['der', 300], ['der', 490]];
  const filas: [string, string, 'izq' | 'der', number][] = X.cifras.map(([k, lbl], i) => [k, lbl, ...pos[i]]);
  return (
    <AbsoluteFill style={{...e.style, opacity: e.op * (1 - lin(t, e.b - 0.12, e.b)), transformOrigin: '960px 470px', transform: `scale(${1 - 0.97 * cierra})`}}>
      <Mapa size={560} t={t} style={{left: 960 - 280, top: 190, transform: `scale(${(0.15 + 0.85 * m(e.a, 12)) * (0.7 + 0.3 * m(t0 - 0.2, 12))})`}} />
      {filas.map(([k, lbl, lado, top], i) => {const cuenta = Math.min(1, Math.floor(Math.max(0, t - t0 - i * 0.12) / (T.beat / 2)) / 8), ent = suave(lin(t, t0 + 0.1 + i * 0.12, t0 + 0.5 + i * 0.12));
        const golpe = 1 + 0.12 * Math.max(0, 1 - Math.abs(t - (t0 + i * 0.12 + 8 * T.beat / 2)) * 5);
        return <div key={k} style={{position: 'absolute', top, left: lado === 'izq' ? 120 : 1280, width: 520, textAlign: lado === 'izq' ? 'right' : 'left', opacity: ent,
          transform: `translateX(${(lado === 'izq' ? -1 : 1) * 60 * (1 - ent)}px)`}}>
          <div style={{fontFamily: FRAUNCES, fontWeight: 600, fontSize: 96, color: C.crema, letterSpacing: '-0.04em', lineHeight: 1, display: 'inline-block', transform: `scale(${golpe})`}}>
            {Math.round((CIFRAS as any)[k] * cuenta).toLocaleString(X.locale)}</div>
          <div style={{fontFamily: MONO, fontSize: 26, letterSpacing: '0.14em', textTransform: 'uppercase', color: C.violetaOsc, marginTop: 6}}>{lbl}</div>
        </div>;})}
    </AbsoluteFill>
  );
};

const Medicos: React.FC = () => {
  const e = useEscena('medicos', {entra: false, sale: false}); const m = useMuelle(); if (e.op <= 0) return null; const t = e.t;
  const a = m(ventana('medicos')[0], 14), b = m(dice('medicos', 'doctors') - 0.05, 12);  // la primera frase nace del punto, sin hueco vacío
  const nace = m(e.a, 12), sube = suave(lin(t, e.b - 0.45, e.b + 0.05));  // costura 5: la línea violeta sube al sitio del titular final
  const f = {fontFamily: FRAUNCES, fontWeight: 600, fontSize: 110, letterSpacing: '-0.02em', position: 'absolute' as const, left: 0, right: 0, textAlign: 'center' as const};
  return (
    <AbsoluteFill style={e.style}>
      <div style={{...f, top: 300, color: C.crema, opacity: cl(a * 1.3) * (1 - sube), transformOrigin: '960px 170px',
        transform: `translateY(${30 * (1 - a) - 80 * sube}px) scale(${0.2 + 0.8 * nace})`}}>{X.medicos[0]}</div>
      <div style={{...f, top: 450, color: sube > 0.5 ? C.crema : C.violetaOsc, opacity: cl(b * 1.3) * (1 - lin(t, e.b - 0.1, e.b + 0.05)),
        transform: `translateY(${30 * (1 - b) - 120 * sube}px) scale(${1 + 0.07 * sube})`}}>{X.medicos[1]}</div>
    </AbsoluteFill>
  );
};

const Cierre: React.FC = () => {
  const t = useT(); const m = useMuelle(); const [a] = ventana('ned'); if (t < a) return null;
  const e1 = m(dice('ned', 'still') - 0.1, 12), e2 = m(dice('web-final', 'whole') - 0.1, 14), e3 = m(dice('web-final', 'helptitular') - 0.05, 9);
  const f = {position: 'absolute' as const, left: 0, right: 0, textAlign: 'center' as const};
  return (
    <AbsoluteFill style={{opacity: 1}}>
      <div style={{position: 'absolute', left: 960 - 45, top: 220, width: 90, height: 90, transform: `scale(${0.6 + 0.4 * e1}) rotate(${45 * suave(lin(t, a, a + 1.2))}deg)`}}>
        <Estrella size={90} color={C.coral} /></div>
      <div style={{...f, top: 330, fontFamily: FRAUNCES, fontWeight: 600, fontSize: X.cierre[0].length > 24 ? 80 : 118, whiteSpace: 'nowrap', color: C.crema, opacity: cl(lin(t, a - 0.05, a + 0.1))}}>{X.cierre[0]}</div>
      <div style={{...f, top: 480, fontFamily: HANKEN, fontWeight: 500, fontSize: 46, color: C.crema, opacity: cl(e2 * 1.3)}}>{X.cierre[1]}</div>
      <div style={{...f, top: 545, fontFamily: FRAUNCES, fontWeight: 600, fontSize: 96, color: C.violetaOsc, letterSpacing: '-0.02em', opacity: cl(e3 * 1.3),
        transform: `scale(${(0.85 + 0.15 * e3) * (1 + 0.02 * Math.sin((t - dice('web-final', 'helptitular')) * Math.PI * 1.6) * e3)})`}}>helptitular.com</div>
      <div style={{...f, top: 680, fontFamily: MONO, fontSize: 26, letterSpacing: '0.14em', textTransform: 'uppercase', color: 'rgba(250,246,240,.8)',
        opacity: suave(lin(t, dice('web-final', 'helptitular') + 0.5, dice('web-final', 'helptitular') + 0.9))}}>{X.cierre[2]}</div>
      {/* fundido final corto: la tarjeta vive 2,5 s y solo el último instante funde (director + diseño, 28-sep) */}
      <AbsoluteFill style={{background: C.berenjena, opacity: lin(t, T.total - 0.3, T.total - 0.03)}} />
    </AbsoluteFill>
  );
};

// ---------- audio: la música se agacha mientras ella habla ----------
const hablando = (t: number) => TR.some((x) => t >= x.t0 - 0.15 && t < x.t1 + 0.1);
const volMusica = (f: number) => {
  const t = f / 30; let v = 0;
  for (let k = -6; k <= 6; k++) v += hablando(t + k * 0.03) ? 1 : 0;  // rampa suave de ~0,4 s
  const tNed = tramo('ned').t0, hueco = lin(t, tNed - 0.9, tNed - 0.6) * (1 - lin(t, tNed - 0.1, tNed + 0.15));  // un respiro antes del clímax
  return (0.9 - 0.62 * (v / 13)) * (1 - 0.92 * hueco);
};

export const Polaris: React.FC = () => (
  <AbsoluteFill style={{background: C.berenjena}}>
    <Cielo />
    <Apertura />
    <Problema />
    <MasAlla />
    <PolarisEsc />
    <Aprender />
    <Galeria />
    <Radar />
    <Carga />
    <Web />
    <Cifras />
    <Medicos />
    <Cierre />
    <Sequence from={Math.round((T.drop - 0.35) * 30)} durationInFrames={24}><Luz seed={3} hue={120} /></Sequence>
    <Sequence from={Math.round((dice('ned', 'still') - 0.35) * 30)} durationInFrames={24}><Luz seed={8} hue={110} /></Sequence>
    <Cadena />
    <PalabraClave />
    <Subtitulos />
    <Gancho />
    <Audio src={staticFile('voz.wav')} />
    <Audio src={staticFile('musica.wav')} volume={volMusica} />
    <Audio src={staticFile('sfx.wav')} volume={0.7} />
  </AbsoluteFill>
);
