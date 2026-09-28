// Vídeo NARRADO con su voz (28-sep-2026): una composición genérica que se construye entera a partir de
// public/narrado/timeline.json (narrado.py). Estilo de presentación inspirado en los vídeos de {{CONTACTO}} (KAI):
// rótulo numerado, titular serif con la palabra de acento en cursiva violeta, UNA ventana que vive todo el vídeo
// y cambia de forma y de contenido (nunca un corte seco: onetake), diagramas que se dibujan, y cada fila / nodo /
// cifra entra cuando ella dice su palabra. Los subtítulos quitan solos lo que ya está escrito en pantalla.
import React from 'react';
import {AbsoluteFill, Audio, Img, OffthreadVideo, Sequence, Solid, interpolate, spring, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import {lightLeak} from '@remotion/effects/light-leak';
import {loadFont as fFraunces} from '@remotion/google-fonts/Fraunces';
import {loadFont as fHanken} from '@remotion/google-fonts/HankenGrotesk';
import {loadFont as fMono} from '@remotion/google-fonts/JetBrainsMono';
import T from '../public/narrado/timeline.json';

const {fontFamily: FRAUNCES} = fFraunces('normal', {weights: ['600'], subsets: ['latin', 'latin-ext']});
fFraunces('italic', {weights: ['600'], subsets: ['latin', 'latin-ext']});
const {fontFamily: HANKEN} = fHanken('normal', {weights: ['500', '700'], subsets: ['latin', 'latin-ext']});
const {fontFamily: MONO} = fMono('normal', {weights: ['400', '600'], subsets: ['latin', 'latin-ext']});

// tokens de helptitular-site/tokens.css; «acento» es el violeta {{TITULAR}} aclarado para leerse sobre berenjena
const K = {berenjena: '#2D1B3D', noche: '#1D1127', crema: '#FAF6F0', violeta: '#A855B5', suave: '#E8D4ED', acento: '#C98BD3', coral: '#FF6B47'};
const STAR_D = 'M10 1.6 C10.8 5,11.4 6.2,12.6 7.4 C14 8.8,16.4 9.4,18.4 10 C16.2 10.8,14.2 11.4,12.8 12.7 C11.5 13.9,10.9 15.7,10 18.4 C9.3 15.9,8.5 14.2,7.2 12.9 C5.8 11.6,3.4 10.7,1.6 10 C3.8 9.1,5.8 8.4,7.2 7.1 C8.4 6,9.2 4.2,10 1.6 Z';

// ---------- tiempo ----------
type Palabra = {w: string; a: number; b: number};
type Frase = {t0: number; t1: number; texto: string; palabras: Palabra[]};
type Cap = {id: string; kicker: string; titulo: string; escena: any; t0: number; t1: number; frases: Frase[]};
const CAPS = T.capitulos as Cap[];
const CIFRAS = (T as any).cifras as Record<string, number>;
const cl = (x: number, a = 0, b = 1) => Math.min(b, Math.max(a, x));
const lin = (t: number, a: number, b: number) => cl((t - a) / (b - a));
const suave = (x: number) => 1 - Math.pow(1 - x, 3);
const inOut = (x: number) => (x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2);
const useT = () => {const f = useCurrentFrame(); const {fps} = useVideoConfig(); return f / fps;};
const useMuelle = () => {
  const f = useCurrentFrame(); const {fps} = useVideoConfig();
  return (tSeg: number, damping = 15, mass = 0.7) => spring({frame: f - Math.round(tSeg * fps), fps, config: {damping, mass, stiffness: 140}});
};
// ventana de cada capítulo: empieza un poco antes de su primera frase y dura hasta que empieza el siguiente
const inicio = (i: number) => (i === 0 ? 0 : CAPS[i].t0 - 0.55);
const fin = (i: number) => (i + 1 < CAPS.length ? inicio(i + 1) : T.total + 1);
const capEn = (t: number) => Math.max(0, CAPS.findIndex((_, i) => t >= inicio(i) && t < fin(i)));
const enOrden = (ts: number[]) => ts.reduce((acc: number[], x, k) => [...acc, k ? Math.max(x, acc[k - 1] + 0.35) : x], []);
const norm = (s: string) => s.toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/[^a-z0-9 ]/g, ' ');
const dice = (c: Cap, pref?: string) => {
  if (!pref) return c.t0;
  for (const f of c.frases) for (const w of f.palabras) if (norm(w.w).trim().startsWith(norm(pref).trim())) return w.a;
  return c.t0;
};

// ---------- fondo: noche con estrellas lentas y las barras de «otras ventanas» al fondo (KAI) ----------
const Fondo: React.FC = () => {
  const t = useT();
  const est = React.useMemo(() => {let s = 11; const r = () => (s = (s * 16807) % 2147483647) / 2147483647;
    return Array.from({length: 70}, () => ({x: r() * 1920, y: r() * 1080, z: r(), f: r() * 6.28}));}, []);
  return (
    <AbsoluteFill style={{background: `radial-gradient(ellipse at 50% 42%, #3d2552 0%, ${K.berenjena} 45%, ${K.noche} 100%)`}}>
      {[0, 1, 2, 3, 4, 5].map((k) => <div key={k} style={{position: 'absolute', left: -40 + 30 * Math.sin(t * 0.07 + k), top: 120 + k * 150, width: 300 + 90 * (k % 3), height: 26,
        borderRadius: 13, background: 'rgba(232,212,237,.045)'}} />)}
      {[0, 1, 2, 3, 4].map((k) => <div key={'d' + k} style={{position: 'absolute', right: -60 + 30 * Math.cos(t * 0.06 + k), top: 180 + k * 170, width: 260 + 70 * (k % 2), height: 26,
        borderRadius: 13, background: 'rgba(232,212,237,.04)'}} />)}
      <svg width={1920} height={1080} style={{position: 'absolute', inset: 0}}>
        {est.map((e, k) => {const x = ((e.x - t * (4 + 14 * e.z)) % 1940 + 1940) % 1940 - 10;
          return <path key={k} d={STAR_D} fill={K.suave} opacity={(0.08 + 0.3 * e.z) * (0.6 + 0.4 * Math.sin(t * 1.1 + e.f))} transform={`translate(${x},${e.y}) scale(${(3 + 7 * e.z) / 20})`} />;})}
      </svg>
    </AbsoluteFill>
  );
};

// ---------- titular: rótulo numerado + titular con la palabra de acento en cursiva violeta ----------
const partes = (s: string) => s.split(/(\*[^*]+\*)/).filter(Boolean).map((p) => ({txt: p.replace(/\*/g, ''), acento: p.startsWith('*')}));
const Titular: React.FC = () => {
  const t = useT(); const m = useMuelle(); const i = capEn(t); const c = CAPS[i];
  if (!c.titulo || c.escena.tipo === 'portada' || c.escena.tipo === 'cierre') return null;
  const a = inicio(i) + 0.1, sale = suave(lin(t, fin(i) - 0.35, fin(i)));
  const palabras = partes(c.titulo).flatMap((p) => p.txt.split(/(\s+)/).filter((w) => w.trim()).map((w) => ({w, acento: p.acento})));
  return (
    <div style={{position: 'absolute', left: 120, top: 62, opacity: 1 - sale, transform: `translateY(${-24 * sale}px)`}}>
      <div style={{fontFamily: MONO, fontSize: 22, letterSpacing: '0.16em', textTransform: 'uppercase', color: K.acento, opacity: suave(lin(t, a, a + 0.3)),
        transform: `translateX(${-20 * (1 - suave(lin(t, a, a + 0.4)))}px)`}}>{c.kicker}</div>
      <div style={{marginTop: 10, fontFamily: FRAUNCES, fontWeight: 600, fontSize: 76, lineHeight: 1.05, letterSpacing: '-0.02em', color: K.crema, whiteSpace: 'nowrap'}}>
        {palabras.map((p, k) => {const g = m(a + 0.08 + k * 0.06, 16);
          return <span key={k} style={{display: 'inline-block', marginRight: 20, opacity: cl(g * 1.4), transform: `translateY(${40 * (1 - g)}px)`,
            fontStyle: p.acento ? 'italic' : 'normal', color: p.acento ? K.acento : K.crema}}>{p.w}</span>;})}
      </div>
    </div>
  );
};

// ---------- el escenario ----------
// Escenario a PANTALLA COMPLETA ({{TITULAR}}, 28-sep: «el otro era más visual que tenerlo todo metido en una pantalla de
// navegador»). Nada de marco de ventana: el contenido ocupa el cuadro bajo el titular, con cámara que flota y empuja.
const ZONA = {x: 0, y: 190, w: 1920, h: 770};
const Ventana: React.FC = () => {
  const t = useT(); const i = capEn(t); const c = CAPS[i];
  if (c.escena.tipo === 'portada' && t < fin(0) - 0.3) return null;
  const flota = Math.sin(t * 0.5) * 5, empuje = 1 + 0.035 * inOut(lin(t, inicio(i) + 0.7, fin(i)));
  return (
    <div style={{position: 'absolute', left: ZONA.x, top: ZONA.y + flota, width: ZONA.w, height: ZONA.h, transform: `scale(${empuje})`}}>
      <Contenido w={ZONA.w} h={ZONA.h} />
    </div>
  );
};

// el contenido del capítulo vivo sale borroso hacia arriba y el nuevo entra desde abajo, dentro de la misma ventana
const Contenido: React.FC<{w: number; h: number}> = ({w, h}) => {
  const t = useT(); const i = capEn(t);
  const vistas = [i - 1, i].filter((k) => k >= 0).map((k) => {
    const ent = suave(lin(t, inicio(k) + 0.25, inicio(k) + 0.75)), sal = k < i ? suave(lin(t, inicio(i), inicio(i) + 0.3)) : 0;
    return {k, op: k < i ? 1 - sal : ent, dy: k < i ? -30 * sal : 30 * (1 - ent)};
  }).filter((v) => v.op > 0.01);
  return <>{vistas.map((v) => (
    <div key={v.k} style={{position: 'absolute', inset: 0, opacity: v.op, transform: `translateY(${v.dy}px)`, filter: v.op < 0.98 ? `blur(${8 * (1 - v.op)}px)` : undefined}}>
      <Escena c={CAPS[v.k]} w={w} h={h} />
    </div>))}</>;
};

// ---------- iconos de línea ----------
const ICONOS: Record<string, string> = {
  firma: 'M4 20 L8 19 L19 8 L16 5 L5 16 Z M14 7 L17 10 M3 23 H21',
  candado: 'M6 11 H18 V21 H6 Z M8.5 11 V8 A3.5 3.5 0 0 1 15.5 8 V11 M12 15 V17',
  alerta: 'M12 3 L22 20 H2 Z M12 9 V14 M12 17 V17.5',
  microscopio: 'M9 3 L14 3 L14 12 L9 12 Z M11.5 12 V15 M5 21 H19 M7 18 A6 6 0 0 0 18 14',
  cubo: 'M12 2 L21 7 V17 L12 22 L3 17 V7 Z M3 7 L12 12 L21 7 M12 12 V22',
  radar: 'M12 12 m-9 0 a9 9 0 1 0 18 0 a9 9 0 1 0 -18 0 M12 12 m-5 0 a5 5 0 1 0 10 0 a5 5 0 1 0 -10 0 M12 12 L19 6',
  caja: 'M3 8 L12 3 L21 8 V16 L12 21 L3 16 Z M3 8 L12 13 L21 8 M12 13 V21 M7.5 5.5 L16.5 10.5',
  web: 'M12 12 m-9 0 a9 9 0 1 0 18 0 a9 9 0 1 0 -18 0 M3 12 H21 M12 3 C8 8,8 16,12 21 C16 16,16 8,12 3',
  repo: 'M5 3 H17 V19 H5 Z M5 15 H17 M8 19 V22 L9.5 21 L11 22 V19',
};
const Icono: React.FC<{n: string; size?: number; color?: string; traza?: number}> = ({n, size = 40, color = K.acento, traza = 1}) => (
  <svg viewBox="0 0 24 24" width={size} height={size} style={{overflow: 'visible'}}>
    <path d={ICONOS[n] || ICONOS.web} fill="none" stroke={color} strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round" pathLength={1}
      strokeDasharray={1} strokeDashoffset={1 - traza} />
  </svg>
);

// ---------- escenas ----------
const Escena: React.FC<{c: Cap; w: number; h: number; e?: any}> = ({c: c0, w, h, e: e0}) => {
  const e = e0 || c0.escena, c = e0 ? {...c0, escena: e} : c0;
  switch (e.tipo) {
    case 'pasos': return <EPasos c={c} w={w} h={h} />;
    case 'golpes': return <EGolpes c={c} w={w} h={h} />;
    case 'foto': return <EFoto c={c} w={w} h={h} />;
    case 'anillo': return <EAnillo c={c} w={w} h={h} />;
    case 'visores': return <EVisores c={c} w={w} h={h} />;
    case 'ned': return <ENed c={c} w={w} h={h} />;
    case 'imagen': return <EImagen c={c} w={w} h={h} />;
    case 'dosCaras': return <EDosCaras c={c} w={w} h={h} />;
    case 'contadores': return <EContadores c={c} w={w} h={h} />;
    case 'estrella': return <EEstrella c={c} w={w} h={h} />;
    case 'diagrama': return <EDiagrama c={c} w={w} h={h} />;
    case 'lista': return <ELista c={c} w={w} h={h} />;
    case 'cita': return <ECita c={c} w={w} h={h} />;
    case 'repo': return <ERepo c={c} w={w} h={h} />;
    default: return null;
  }
};
type EP = {c: Cap; w: number; h: number};

const EImagen: React.FC<EP> = ({c, w, h}) => {
  const t = useT(); const z = 1 + 0.06 * lin(t, c.t0, c.t1 + 1);
  return <AbsoluteFill style={{alignItems: 'center', justifyContent: 'center'}}>
    <Img src={staticFile(c.escena.src)} style={{maxWidth: w - 120, maxHeight: h - 90, borderRadius: 16, transform: `scale(${z})`, boxShadow: '0 20px 60px rgba(0,0,0,.35)'}} />
  </AbsoluteFill>;
};

// un tumor, dos caras: la estrella se parte en dos mitades que se separan (sin cifras clínicas en pantalla)
const EDosCaras: React.FC<EP> = ({c, w, h}) => {
  const t = useT(); const m = useMuelle(); const e = c.escena; const tDos = dice(c, e.en || 'dos') - 0.1;
  const nace = m(c.t0 + 0.1, 12), abre = suave(lin(t, tDos, tDos + 0.7)), rot = m(tDos + 0.5, 16);
  const S = 300, cx = w / 2, cy = h / 2 - 20;
  return <AbsoluteFill>
    {([['izq', -1, K.acento, e.izq], ['der', 1, K.coral, e.der]] as [string, number, string, string][]).map(([lado, s, col, lbl]) => (
      <div key={lado} style={{position: 'absolute', left: cx - S / 2 + s * 170 * abre, top: cy - S / 2, width: S, height: S, transform: `scale(${0.3 + 0.7 * nace})`}}>
        <svg viewBox="0 0 20 20" width={S} height={S} style={{overflow: 'visible', filter: `drop-shadow(0 0 30px ${lado === 'izq' ? 'rgba(201,139,211,.55)' : 'rgba(255,107,71,.5)'})`}}>
          <defs><clipPath id={'n-' + lado}><rect x={lado === 'izq' ? 0 : 10} y={0} width={10} height={20} /></clipPath></defs>
          <path d={STAR_D} fill={col} clipPath={`url(#n-${lado})`} />
        </svg>
        <div style={{position: 'absolute', top: S + 20, left: lado === 'izq' ? -40 : 80, width: 260, textAlign: 'center', fontFamily: FRAUNCES, fontStyle: 'italic', fontWeight: 600,
          fontSize: 40, color: col, opacity: cl(rot * 1.4), transform: `translateY(${20 * (1 - rot)}px)`}}>{lbl}</div>
      </div>))}
  </AbsoluteFill>;
};

const EContadores: React.FC<EP> = ({c, w, h}) => {
  const t = useT(); const m = useMuelle(); const items = c.escena.items as {k: string; lbl: string}[];
  return <AbsoluteFill style={{display: 'flex', flexDirection: 'row', alignItems: 'center', justifyContent: 'space-evenly', padding: '0 40px'}}>
    {items.map((it, k) => {const t0 = c.t0 + 0.3 + k * 0.35, g = m(t0, 14), cuenta = suave(lin(t, t0, t0 + 1.4)), v = CIFRAS[it.k] || 0;
      return <div key={it.k} style={{textAlign: 'center', opacity: cl(g * 1.4), transform: `translateY(${40 * (1 - g)}px)`}}>
        <div style={{fontFamily: FRAUNCES, fontWeight: 600, fontSize: 150, lineHeight: 1, color: K.crema, letterSpacing: '-0.04em'}}>{Math.round(v * cuenta)}</div>
        <div style={{marginTop: 14, fontFamily: MONO, fontSize: 24, letterSpacing: '0.14em', textTransform: 'uppercase', color: K.acento}}>{it.lbl}</div>
      </div>;})}
  </AbsoluteFill>;
};

const EEstrella: React.FC<EP> = ({c, w, h}) => {
  const t = useT(); const m = useMuelle(); const e = c.escena;
  const tMeta = dice(c, 'N-E-D') || c.t0, tPreg = dice(c, 'pregunta'), g = m(c.t0 + 0.2, 12), meta = m(tMeta - 0.05, 11), preg = m(tPreg - 0.1, 14);
  return <AbsoluteFill>
    <div style={{position: 'absolute', left: 150, top: h / 2 - 150, width: 300, height: 300, transform: `scale(${0.4 + 0.6 * g}) rotate(${20 * Math.sin(t * 0.4)}deg)`,
      filter: 'drop-shadow(0 0 40px rgba(201,139,211,.6))'}}>
      <svg viewBox="0 0 20 20" width={300} height={300}><path d={STAR_D} fill={K.acento} /></svg>
    </div>
    {[0, 1, 2, 3, 4, 5].map((k) => {const a = t * 0.8 + k * 1.05; return <div key={k} style={{position: 'absolute', left: 300 + 190 * Math.cos(a) - 5, top: h / 2 + 70 * Math.sin(a) - 5,
      width: 10, height: 10, borderRadius: 5, background: K.suave, opacity: 0.5 * g}} />;})}
    <div style={{position: 'absolute', left: 600, top: h / 2 - 190, opacity: cl(meta * 1.4), transform: `translateX(${40 * (1 - meta)}px)`}}>
      <div style={{display: 'inline-block', padding: '8px 18px', borderRadius: 999, background: 'rgba(168,85,181,.3)', border: `1px solid ${K.violeta}`,
        fontFamily: MONO, fontSize: 22, letterSpacing: '0.12em', color: K.suave}}>GOAL</div>
      <div style={{fontFamily: FRAUNCES, fontWeight: 600, fontSize: 200, lineHeight: 1, color: K.crema, letterSpacing: '-0.04em', marginTop: 10}}>{e.meta}</div>
      <div style={{fontFamily: FRAUNCES, fontStyle: 'italic', fontWeight: 600, fontSize: 46, color: K.acento}}>{e.sub}</div>
    </div>
    <div style={{position: 'absolute', left: 600, top: h / 2 + 150, padding: '16px 28px', borderRadius: 16, border: `2px solid ${K.coral}`, background: 'rgba(255,107,71,.08)',
      fontFamily: HANKEN, fontWeight: 700, fontSize: 38, color: K.crema, opacity: cl(preg * 1.4), transform: `scale(${0.9 + 0.1 * preg})`}}>{e.pregunta}</div>
  </AbsoluteFill>;
};

const EDiagrama: React.FC<EP> = ({c, w, h}) => {
  const t = useT(); const m = useMuelle(); const nodos = c.escena.nodos as {t: string; s: string; en?: string; puerta?: boolean}[];
  const n = nodos.length, bw = 280, bh = 170, gap = (w - 120 - n * bw) / (n - 1), y = h / 2 - bh / 2;
  const ts = enOrden(nodos.map((x, k) => (x.en ? dice(c, x.en) : c.t0 + k * 0.6) - 0.1));
  const xs = nodos.map((_, k) => 60 + k * (bw + gap));
  const ultimo = ts.filter((x) => t >= x).length - 1;
  return <AbsoluteFill>
    <svg width={w} height={h} style={{position: 'absolute', inset: 0}}>
      {nodos.slice(1).map((_, k) => {const a = xs[k] + bw, b = xs[k + 1], p = suave(lin(t, ts[k + 1] - 0.35, ts[k + 1]));
        return <g key={k}><line x1={a + 8} y1={h / 2} x2={a + 8 + (b - a - 16) * p} y2={h / 2} stroke={K.acento} strokeWidth={3} strokeLinecap="round" />
          {p > 0.98 && <path d={`M${b - 20} ${h / 2 - 9} L${b - 8} ${h / 2} L${b - 20} ${h / 2 + 9}`} fill="none" stroke={K.acento} strokeWidth={3} strokeLinecap="round" />}</g>;})}
      {ultimo >= n - 1 && (() => {const u = ((t - ts[n - 1]) % 2.4) / 2.4, x = xs[0] + bw / 2 + (xs[n - 1] - xs[0]) * u;
        return <circle cx={x} cy={h / 2} r={9} fill={K.coral} opacity={0.9} />;})()}
    </svg>
    {nodos.map((x, k) => {const g = m(ts[k], 13), vivo = k === ultimo;
      return <div key={k} style={{position: 'absolute', left: xs[k], top: y, width: bw, height: bh, borderRadius: 20, textAlign: 'center',
        background: x.puerta ? 'rgba(255,107,71,.10)' : 'rgba(250,246,240,.05)', border: `2px solid ${x.puerta ? K.coral : vivo ? K.acento : 'rgba(232,212,237,.25)'}`,
        boxShadow: vivo ? `0 0 40px ${x.puerta ? 'rgba(255,107,71,.35)' : 'rgba(201,139,211,.35)'}` : 'none',
        opacity: cl(g * 1.4), transform: `translateY(${30 * (1 - g)}px) scale(${0.9 + 0.1 * g})`, display: 'flex', flexDirection: 'column', justifyContent: 'center'}}>
        <div style={{fontFamily: FRAUNCES, fontWeight: 600, fontSize: 44, color: K.crema}}>{x.t}</div>
        <div style={{fontFamily: MONO, fontSize: 19, letterSpacing: '0.1em', textTransform: 'uppercase', color: x.puerta ? K.coral : K.acento, marginTop: 8}}>{x.s}</div>
      </div>;})}
  </AbsoluteFill>;
};

const ELista: React.FC<EP> = ({c, w, h}) => {
  const t = useT(); const m = useMuelle(); const items = c.escena.items as {icono: string; texto: string; en?: string}[];
  const ts = enOrden(items.map((x, k) => (x.en ? dice(c, x.en) : c.t0 + k * 0.7) - 0.12));
  const alto = Math.min(110, (h - 60) / items.length), y0 = (h - alto * items.length) / 2, ultimo = ts.filter((x) => t >= x).length - 1;
  return <AbsoluteFill>
    {items.map((it, k) => {const g = m(ts[k], 15), vivo = k === ultimo;
      const ancho = Math.min(w - 140, 1150);  // a pantalla completa, filas de ancho legible y centradas
      return <div key={k} style={{position: 'absolute', left: (w - ancho) / 2, width: ancho, top: y0 + k * alto, height: alto - 16, display: 'flex', alignItems: 'center', gap: 28,
        padding: '0 26px', borderRadius: 18, background: vivo ? 'rgba(168,85,181,.16)' : 'rgba(250,246,240,.03)', border: `1px solid ${vivo ? 'rgba(201,139,211,.5)' : 'rgba(232,212,237,.1)'}`,
        opacity: cl(g * 1.4) * (vivo || ultimo < 0 ? 1 : 0.72), transform: `translateX(${-50 * (1 - g)}px)`}}>
        <div style={{width: 62, height: 62, borderRadius: 31, background: 'rgba(168,85,181,.18)', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0}}>
          <Icono n={it.icono} size={34} traza={suave(lin(t, ts[k], ts[k] + 0.6))} /></div>
        <div style={{fontFamily: FRAUNCES, fontWeight: 600, fontSize: 42, color: K.crema, letterSpacing: '-0.01em'}}>{it.texto}</div>
      </div>;})}
  </AbsoluteFill>;
};

const ECita: React.FC<EP> = ({c, w, h}) => {
  const t = useT(); const m = useMuelle(); const tc = c.frases[c.frases.length - 1].t0; const pre = m(c.t0 + 0.2, 16), luz = m(tc - 0.15, 14);
  const g = 0.35 * pre + 0.65 * luz, raya = suave(lin(t, tc + 0.3, tc + 1.2));
  return <AbsoluteFill style={{alignItems: 'center', justifyContent: 'center', padding: '0 110px'}}>
    <div style={{fontFamily: FRAUNCES, fontWeight: 600, fontSize: 150, lineHeight: 0.6, color: K.violeta, opacity: g}}>“</div>
    <div style={{fontFamily: FRAUNCES, fontStyle: 'italic', fontWeight: 600, fontSize: 68, lineHeight: 1.15, textAlign: 'center', color: K.crema,
      opacity: cl(g * 1.3), transform: `translateY(${30 * (1 - g)}px)`}}>{c.escena.texto}</div>
    <div style={{width: 520, height: 4, marginTop: 34, borderRadius: 2, background: K.acento, transform: `scaleX(${raya})`}} />
  </AbsoluteFill>;
};

const ERepo: React.FC<EP> = ({c, w, h}) => {
  const t = useT(); const m = useMuelle(); const e = c.escena; const g = m(c.t0 + 0.1, 14);
  return <AbsoluteFill style={{padding: `60px ${Math.max(80, (w - 1150) / 2)}px`}}>
    <div style={{display: 'flex', alignItems: 'center', gap: 22, opacity: cl(g * 1.4)}}>
      <Icono n="repo" size={58} color={K.crema} traza={suave(lin(t, c.t0, c.t0 + 0.8))} />
      <div style={{fontFamily: MONO, fontSize: 40, color: K.crema}}>BeyondTheProtocol / <b>polaris</b></div>
      <div style={{marginLeft: 12, padding: '6px 16px', borderRadius: 999, border: '1px solid rgba(232,212,237,.4)', fontFamily: MONO, fontSize: 20, color: K.suave}}>Public</div>
    </div>
    <div style={{marginTop: 50}}>
      {(e.lineas as string[]).map((l, k) => {const gk = m(c.t0 + 0.6 + k * 0.45, 15);
        return <div key={k} style={{display: 'flex', alignItems: 'center', gap: 18, marginBottom: 30, opacity: cl(gk * 1.4), transform: `translateX(${-30 * (1 - gk)}px)`}}>
          <div style={{width: 14, height: 14, borderRadius: 7, background: k === 0 ? K.coral : K.acento}} />
          <div style={{fontFamily: FRAUNCES, fontWeight: 600, fontSize: 52, color: K.crema}}>{l}</div>
        </div>;})}
    </div>
  </AbsoluteFill>;
};

// ---------- lo mejor de la v18, dentro de la ventana ----------
const Secuencia: React.FC<{dir: string; n: number; t: number; style: React.CSSProperties}> = ({dir, n, t, style}) => (
  <Img src={staticFile(`${dir}/${String(1 + (Math.floor(t * 30) % n)).padStart(4, '0')}.jpg`)} style={style} />
);

// varios pasos en un capítulo: cada uno entra cuando ella dice su palabra y el anterior sale borroso, en la misma ventana
const EPasos: React.FC<EP> = ({c, w, h}) => {
  const t = useT(); const pasos = c.escena.pasos as any[];
  const ts = enOrden(pasos.map((p, k) => (k === 0 ? c.t0 - 0.3 : dice(c, p.desde) - 0.25)));
  const vivo = Math.max(0, ts.filter((x) => t >= x).length - 1);
  return <>{[vivo - 1, vivo].filter((k) => k >= 0).map((k) => {
    const op = k < vivo ? 1 - suave(lin(t, ts[vivo], ts[vivo] + 0.3)) : k === 0 ? 1 : suave(lin(t, ts[k], ts[k] + 0.45));
    if (op <= 0.01) return null;
    return <div key={k} style={{position: 'absolute', inset: 0, opacity: op, transform: `translateY(${(k < vivo ? -24 : 24) * (1 - op)}px)`,
      filter: op < 0.98 ? `blur(${8 * (1 - op)}px)` : undefined}}><Escena c={{...c, t0: Math.max(c.t0, ts[k] + 0.25)}} w={w} h={h} e={pasos[k]} /></div>;
  })}</>;
};

// golpes cinéticos (v18): cada palabra entra grande al decirla y empuja hacia arriba a la anterior
const EGolpes: React.FC<EP> = ({c, w, h}) => {
  const t = useT(); const m = useMuelle(); const ps = c.escena.palabras as {txt: string; en: string; acento?: boolean}[];
  const ts = enOrden(ps.map((p) => dice(c, p.en) - 0.05));
  const llegadas = ts.filter((x) => t >= x).length;
  return <AbsoluteFill style={{alignItems: 'center', justifyContent: 'center'}}>
    {ps.map((p, k) => {if (t < ts[k]) return null; const g = m(ts[k], 11, 0.6), hund = llegadas - 1 - k;
      const shake = hund === 0 ? Math.sin(t * 90) * 6 * Math.max(0, 1 - (t - ts[k]) * 6) : 0;
      return <div key={k} style={{position: 'absolute', fontFamily: FRAUNCES, fontWeight: 600, fontSize: 150, letterSpacing: '-0.035em', whiteSpace: 'nowrap',
        color: p.acento ? K.acento : K.crema, opacity: cl(g * 1.5) * Math.max(0, 1 - 0.45 * hund),
        transform: `translate(${shake}px, ${-120 * hund}px) scale(${(1.6 - 0.6 * g) * (1 - 0.28 * Math.min(2, hund))})`,
        filter: g < 0.95 ? `blur(${12 * (1 - g)}px)` : hund > 0 ? `blur(${2.5 * hund}px)` : undefined}}>{p.txt}</div>;})}
    {ts.map((x, k) => {const q = lin(t, x, x + 0.6); if (q <= 0 || q >= 1) return null;
      return <div key={'f' + k} style={{position: 'absolute', width: 200 + 1100 * suave(q), height: 200 + 1100 * suave(q), borderRadius: '50%',
        background: 'radial-gradient(circle, rgba(201,139,211,.35), transparent 60%)', opacity: 1 - q}} />;})}
  </AbsoluteFill>;
};

// su foto de ingeniera (v18) con el rótulo de nombre
const EFoto: React.FC<EP> = ({c, w, h}) => {
  const t = useT(); const m = useMuelle(); const e = c.escena; const g = m(c.t0, 13), r = suave(lin(t, c.t0 + 0.3, c.t0 + 0.8));
  return <AbsoluteFill>
    <Img src={staticFile(e.src)} style={{position: 'absolute', left: w * 0.12, bottom: -40 - 200 * (1 - g), height: h * 1.25, opacity: cl(g * 1.3),
      WebkitMaskImage: 'linear-gradient(#000 72%, transparent 96%)'}} />
    <div style={{position: 'absolute', left: w * 0.52, top: h * 0.36, opacity: r, transform: `translateX(${40 * (1 - r)}px)`}}>
      <div style={{fontFamily: FRAUNCES, fontWeight: 600, fontSize: 72, color: K.crema, letterSpacing: '-0.02em'}}>{e.nombre}</div>
      <div style={{fontFamily: MONO, fontSize: 28, letterSpacing: '0.16em', textTransform: 'uppercase', color: K.acento, marginTop: 10}}>{e.rol}</div>
    </div>
  </AbsoluteFill>;
};

// el anillo de Polaris (v18) con las cifras del día contando alrededor
const EAnillo: React.FC<EP> = ({c, w, h}) => {
  const t = useT(); const m = useMuelle(); const items = c.escena.items as {k: string; lbl: string}[]; const g = m(c.t0, 12), S = Math.min(h - 60, 520);
  return <AbsoluteFill>
    <div style={{position: 'absolute', left: w / 2 - S / 2, top: h / 2 - S / 2, width: S, height: S, borderRadius: '50%', overflow: 'hidden', transform: `scale(${0.6 + 0.4 * g})`, opacity: cl(g * 1.4)}}>
      <Secuencia dir="mapa" n={120} t={t} style={{width: '100%', height: '100%'}} />
      <div style={{position: 'absolute', left: '50%', top: '50%', width: '20%', height: '20%', transform: 'translate(-50%,-50%)', borderRadius: '50%', background: K.violeta,
        display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: FRAUNCES, fontWeight: 600, fontSize: S * 0.042, color: K.crema, boxShadow: '0 0 60px 20px rgba(168,85,181,.45)'}}>POLARIS</div>
    </div>
    {items.map((it, k) => {const izq = k % 2 === 0, fila = Math.floor(k / 2), t0 = c.t0 + 0.3 + k * 0.25, e = m(t0, 14), cuenta = suave(lin(t, t0, t0 + 1.3));
      return <div key={it.k} style={{position: 'absolute', top: h / 2 - 150 + fila * 190, [izq ? 'right' : 'left']: w / 2 + S / 2 - 10 + 40, width: 360,
        textAlign: izq ? 'right' : 'left', opacity: cl(e * 1.4), transform: `translateX(${(izq ? -1 : 1) * 40 * (1 - e)}px)`} as React.CSSProperties}>
        <div style={{fontFamily: FRAUNCES, fontWeight: 600, fontSize: 110, lineHeight: 1, color: K.crema, letterSpacing: '-0.04em'}}>{Math.round((CIFRAS[it.k] || 0) * cuenta)}</div>
        <div style={{fontFamily: MONO, fontSize: 22, letterSpacing: '0.14em', textTransform: 'uppercase', color: K.acento, marginTop: 6}}>{it.lbl}</div>
      </div>;})}
  </AbsoluteFill>;
};

// los visores 3D de sus propios escáneres (v18), girando
const EVisores: React.FC<EP> = ({c, w, h}) => {
  const t = useT(); const m = useMuelle(); const ps = c.escena.piezas as {dir?: string; n?: number; src?: string; nombre: string}[];
  const tZoom = c.escena.en_zoom ? dice(c, c.escena.en_zoom) : 1e9, z = suave(lin(t, tZoom - 0.1, tZoom + 0.6));
  const gap = 30, lado = (w - 100 - gap * (ps.length - 1)) / ps.length, x0 = (w - (lado * ps.length + gap * (ps.length - 1))) / 2;
  return <AbsoluteFill>
    {ps.map((p, k) => {const g = m(c.t0 + 0.2 + k * 0.18, 13), ultimo = k === ps.length - 1;
      const alto = h - 110;  // tarjetas altas: llenan la ventana en vez de dejar hueco arriba y abajo
      const grande = ultimo ? z : 0, L = lado + (Math.min(w - 120, (h - 80) * 2.4) - lado) * grande, H = alto + (h - 80 - alto) * grande;
      const x = x0 + k * (lado + gap) + ((w - L) / 2 - (x0 + k * (lado + gap))) * grande, y = (h - alto) / 2 - 10 + ((h - H) / 2 - ((h - alto) / 2 - 10)) * grande;
      return <div key={k} style={{position: 'absolute', left: x, top: y, width: L, height: H, borderRadius: 22, overflow: 'hidden', background: K.noche, zIndex: ultimo ? 2 : 1,
        border: '1px solid rgba(232,212,237,.14)', boxShadow: '0 24px 60px rgba(0,0,0,.4)', opacity: cl(g * 1.3) * (ultimo ? 1 : 1 - z),
        transform: `translateY(${50 * (1 - g)}px) scale(${0.88 + 0.12 * g})`}}>
        {p.dir ? <Secuencia dir={p.dir} n={p.n || 120} t={t} style={{width: '100%', height: '100%', objectFit: 'cover'}} />
          : <Img src={staticFile(p.src!)} style={{height: '94%', margin: '3% auto', display: 'block'}} />}
        <div style={{position: 'absolute', left: 18, bottom: 14, fontFamily: MONO, fontSize: 20, letterSpacing: '0.14em', textTransform: 'uppercase', color: K.acento}}>{p.nombre}</div>
      </div>;})}
  </AbsoluteFill>;
};

// NED (v18): cada inicial se despliega en su palabra; debajo, en su idioma
const ENed: React.FC<EP> = ({c, w, h}) => {
  const t = useT(); const m = useMuelle(); const e = c.escena;
  const tN = dice(c, 'N-E-D') - 0.1, tAbre = dice(c, e.en_abre || 'sin') - 0.2, n = m(tN, 11), abre = suave(lin(t, tAbre, tAbre + 0.6));
  const tr = (txt: string, d: number) => txt.slice(0, Math.round(txt.length * suave(lin(t, tAbre + d, tAbre + d + 0.35))));
  const ini = {color: K.crema}, resto = {color: K.acento}, hueco = <span style={{display: 'inline-block', width: `${0.28 * abre}em`}} />;
  return <AbsoluteFill style={{alignItems: 'center', justifyContent: 'center'}}>
    <div style={{fontFamily: FRAUNCES, fontWeight: 600, fontSize: 250 - 150 * abre, letterSpacing: `${-0.04 + 0.03 * abre}em`, lineHeight: 1, whiteSpace: 'nowrap',
      opacity: cl(n * 1.4), transform: `scale(${0.7 + 0.3 * n})`}}>
      <span style={ini}>N</span><span style={resto}>{tr('o', 0)}</span>{hueco}<span style={ini}>E</span><span style={resto}>{tr('vidence', 0.15)}</span>{hueco}
      <span style={{...resto, opacity: abre}}>{abre > 0.5 ? 'of' : ''}</span>{hueco}<span style={ini}>D</span><span style={resto}>{tr('isease', 0.3)}</span>
    </div>
    <div style={{marginTop: 30, fontFamily: FRAUNCES, fontStyle: 'italic', fontWeight: 600, fontSize: 52, color: K.suave, opacity: suave(lin(t, tAbre + 0.5, tAbre + 0.9))}}>{e.sub}</div>
  </AbsoluteFill>;
};

// destello de luz (v18) en los momentos grandes
const Luz: React.FC<{seed: number}> = ({seed}) => {
  const f = useCurrentFrame(); const {durationInFrames, width, height} = useVideoConfig();
  return <AbsoluteFill style={{opacity: 0.45, mixBlendMode: 'screen'}}><Solid width={width} height={height}
    effects={[lightLeak({seed, hueShift: 115, progress: interpolate(f, [0, durationInFrames - 1], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'})})]} /></AbsoluteFill>;
};

// ---------- portada y cierre: la estrella de la que nace la ventana y en la que acaba ----------
// Portada: si el guion trae un plano de ambiente (Google Flow, SIN datos ni texto: regla del 25-sep), va de fondo y
// su propia estrella es la protagonista; al final nuestra estrella la releva en el mismo sitio y se encoge en el
// punto del que nace la ventana (continuidad, sin corte).
const Portada: React.FC = () => {
  const t = useT(); const m = useMuelle(); const c = CAPS[0]; if (c.escena.tipo !== 'portada' || t > fin(0) + 0.6) return null;
  const e = c.escena, tit = m(c.t0 + 0.1, 13), va = inOut(lin(t, fin(0) - 0.2, fin(0) + 0.5));
  const video = e.video ? 1 - lin(t, fin(0) - 0.45, fin(0) + 0.1) : 0, releva = e.video ? lin(t, fin(0) - 0.45, fin(0) - 0.1) : m(0.25, 12);
  return <AbsoluteFill style={{alignItems: 'center', justifyContent: 'center'}}>
    {e.video && <AbsoluteFill style={{opacity: video * lin(t, 0, 0.6)}}><OffthreadVideo src={staticFile(e.video)} muted style={{width: '100%', height: '100%', objectFit: 'cover'}} /></AbsoluteFill>}
    <div style={{position: 'absolute', left: 960 - 90, top: 540 - 90, width: 180, height: 180, opacity: cl(releva * 1.2),
      transform: `scale(${(e.video ? 1 : 0.3 + 0.7 * releva) * (1 - 0.93 * va)}) rotate(${45 * va + 135 * va}deg)`, filter: 'drop-shadow(0 0 50px rgba(201,139,211,.7))'}}>
      <svg viewBox="0 0 20 20" width={180} height={180}><path d={STAR_D} fill={e.video ? K.crema : K.acento} /></svg>
    </div>
    <div style={{position: 'absolute', top: 690, left: 0, right: 0, textAlign: 'center', opacity: cl(tit * 1.4) * (1 - va), transform: `translateY(${40 * (1 - tit)}px)`,
      textShadow: '0 4px 40px rgba(29,17,39,.8)'}}>
      <div style={{fontFamily: FRAUNCES, fontWeight: 600, fontSize: 170, lineHeight: 1, color: K.crema, letterSpacing: '-0.04em'}}>{c.titulo}</div>
      <div style={{fontFamily: FRAUNCES, fontStyle: 'italic', fontWeight: 600, fontSize: 48, color: K.acento, marginTop: 12}}>{e.sub}</div>
    </div>
  </AbsoluteFill>;
};

const Cierre: React.FC = () => {
  const t = useT(); const m = useMuelle(); const i = CAPS.length - 1, c = CAPS[i]; if (c.escena.tipo !== 'cierre' || t < inicio(i) + 0.3) return null;
  const e = c.escena, g = m(inicio(i) + 0.55, 12), tUrl = dice(c, 'help'), u = m(tUrl - 0.1, 11);
  // con plano de ambiente (Flow, sin datos): el amanecer de fondo, un velo berenjena para que el texto se lea, y su estrella hace de la nuestra
  const fondo = e.video ? suave(lin(t, inicio(i) + 0.2, inicio(i) + 1.2)) : 0;
  return <AbsoluteFill style={{alignItems: 'center', justifyContent: 'center'}}>
    {e.video && <AbsoluteFill style={{opacity: fondo}}>
      <OffthreadVideo src={staticFile(e.video)} muted style={{width: '100%', height: '100%', objectFit: 'cover'}} />
      <AbsoluteFill style={{background: 'linear-gradient(180deg, rgba(29,17,39,.15) 0%, rgba(29,17,39,.55) 45%, rgba(29,17,39,.75) 100%)'}} />
    </AbsoluteFill>}
    {!e.video && <div style={{position: 'absolute', left: 960 - 60, top: 250, width: 120, height: 120, transform: `scale(${0.3 + 0.7 * g}) rotate(${45 * g}deg)`,
      filter: 'drop-shadow(0 0 40px rgba(255,107,71,.6))'}}><svg viewBox="0 0 20 20" width={120} height={120}><path d={STAR_D} fill={K.coral} /></svg></div>}
    <div style={{position: 'absolute', top: 410, left: 0, right: 0, textAlign: 'center', textShadow: '0 4px 40px rgba(29,17,39,.85)'}}>
      <div style={{fontFamily: FRAUNCES, fontWeight: 600, fontSize: 124, color: K.crema, letterSpacing: '-0.03em', opacity: cl(g * 1.4), transform: `translateY(${30 * (1 - g)}px)`}}>{e.titular}</div>
      <div style={{fontFamily: FRAUNCES, fontWeight: 600, fontSize: 96, color: K.acento, marginTop: 30, opacity: cl(u * 1.4),
        transform: `scale(${(0.85 + 0.15 * u) * (1 + 0.02 * Math.sin((t - tUrl) * 5) * u)})`}}>{e.url}</div>
      <div style={{fontFamily: MONO, fontSize: 24, letterSpacing: '0.16em', textTransform: 'uppercase', color: 'rgba(250,246,240,.7)', marginTop: 34,
        opacity: suave(lin(t, tUrl + 0.6, tUrl + 1.1))}}>{e.pie}</div>
    </div>
    <AbsoluteFill style={{background: K.noche, opacity: lin(t, T.total - 0.5, T.total - 0.05)}} />
  </AbsoluteFill>;
};

// ---------- subtítulos: quitan solos lo que ya está escrito en pantalla ----------
const VACIAS = new Set('el la los las un una unos unas de del y a en que con por para es no se lo le mi mis me yo su sus al como mas pero o the a of and to is it'.split(' '));
const enPantalla = (c: Cap): Set<string> => {
  const e = c.escena;
  if (e.pasos) return new Set((e.pasos as any[]).flatMap((p) => [...enPantalla({...c, escena: p})]).concat([...enPantalla({...c, escena: {tipo: 'x'}})]));
  const txt: string[] = [...(e.palabras || []).map((x: any) => x.txt), e.nombre, e.rol, ...(e.piezas || []).map((x: any) => x.nombre), c.kicker, c.titulo.replace(/\*/g, ''), e.sub, e.meta, e.pregunta, e.texto, e.titular, e.url, e.etiqueta,
    ...(e.items || []).map((x: any) => `${x.texto || ''} ${x.lbl || ''}`), ...(e.nodos || []).map((x: any) => `${x.t} ${x.s}`), ...(e.lineas || [])];
  return new Set(norm(txt.filter(Boolean).join(' ')).split(/\s+/).filter((w) => w && !VACIAS.has(w)));
};
const TROZOS = CAPS.flatMap((c) => {
  const vistas = enPantalla(c);
  return c.frases.flatMap((f) => {
    const out: Palabra[][] = []; let cur: Palabra[] = [];
    for (const w of f.palabras) {
      if (cur.length && (cur.length >= 4 || [...cur, w].map((p) => p.w).join(' ').length > 26)) {out.push(cur); cur = [];}
      cur.push(w); if (/[,.!?:]$/.test(w.w) && cur.length >= 2) {out.push(cur); cur = [];}
    }
    if (cur.length) out.push(cur);
    // un trozo cuyas palabras con peso ya están casi todas en pantalla no se subtitula (el mismo texto dos veces, no)
    return out.filter((tz) => {const llenas = tz.flatMap((p) => norm(p.w).split(/\s+/)).filter((w) => w && !VACIAS.has(w));  // «helptitular.com» cuenta como dos palabras
      return !llenas.length || llenas.filter((w) => vistas.has(w)).length / llenas.length < 0.6;});
  });
});
const Subtitulos: React.FC = () => {
  const t = useT();
  const i = TROZOS.findIndex((c, k) => t >= c[0].a - 0.05 && t < (TROZOS[k + 1] ? Math.min(TROZOS[k + 1][0].a - 0.05, c[c.length - 1].b + 0.6) : c[c.length - 1].b + 0.6));
  if (i < 0) return null;
  const c = TROZOS[i], g = suave(lin(t, c[0].a - 0.05, c[0].a + 0.15));
  return <div style={{position: 'absolute', left: 0, right: 0, bottom: 44, textAlign: 'center', fontFamily: HANKEN, fontWeight: 700, fontSize: 46, color: K.crema,
    textShadow: '0 2px 0 rgba(29,17,39,.9), 0 0 16px rgba(29,17,39,.9)', transform: `translateY(${8 * (1 - g)}px)`, opacity: g}}>
    {c.map((w, k) => {const activa = t >= w.a && t < (c[k + 1] ? c[k + 1].a : w.b + 0.3);
      return <span key={k} style={{display: 'inline-block', padding: '2px 11px', borderRadius: 10, background: activa ? K.violeta : 'transparent', textShadow: activa ? 'none' : undefined,
        opacity: t >= w.a - 0.03 ? 1 : 0.55}}>{w.w}</span>;})}
  </div>;
};

// ---------- audio: la cama musical se agacha mientras ella habla ----------
const FRASES = CAPS.flatMap((c) => c.frases);
const hablando = (t: number) => FRASES.some((f) => t >= f.t0 - 0.2 && t < f.t1 + 0.15);
const volMusica = (f: number) => {const t = f / 30; let v = 0; for (let k = -8; k <= 8; k++) v += hablando(t + k * 0.03) ? 1 : 0; return 0.42 - 0.3 * (v / 17);};

export const Narrado: React.FC = () => (
  <AbsoluteFill style={{background: K.berenjena}}>
    <Fondo />
    <Portada />
    <Titular />
    <Ventana />
    <Cierre />
    <Sequence from={Math.round((fin(0) - 0.3) * 30)} durationInFrames={24}><Luz seed={3} /></Sequence>
    <Sequence from={Math.round((inicio(CAPS.length - 1) + 0.2) * 30)} durationInFrames={24}><Luz seed={8} /></Sequence>
    <Subtitulos />
    <Audio src={staticFile('narrado/voz.wav')} />
    <Audio src={staticFile('narrado/musica.wav')} volume={volMusica} />
    <Audio src={staticFile('narrado/sfx.wav')} volume={0.6} />
  </AbsoluteFill>
);
