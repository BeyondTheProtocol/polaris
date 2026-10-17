// Vídeo NARRADO con su voz (28-sep-2026): una composición genérica que se construye entera a partir de
// public/narrado/timeline.json (narrado.py). Estilo de presentación inspirado en los vídeos de {{CONTACTO}} (KAI):
// rótulo numerado, titular serif con la palabra de acento en cursiva violeta, UNA ventana que vive todo el vídeo
// y cambia de forma y de contenido (nunca un corte seco: onetake), diagramas que se dibujan, y cada fila / nodo /
// cifra entra cuando ella dice su palabra. Los subtítulos quitan solos lo que ya está escrito en pantalla.
import React from 'react';
import {AbsoluteFill, Audio, Img, Loop, OffthreadVideo, Sequence, Solid, interpolate, spring, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
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
// la versión en inglés (8-oct-2026) escribe las cifras a su manera: 1.4 million, no 1,4 millones
const EN = (T as any).idioma === 'en', LOC = EN ? 'en-US' : 'es-ES', MILLONES = EN ? 'million' : 'millones';
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

// ---------- plano de ambiente por capítulo (generado con IA: SIN datos, texto, caras ni biología inventada) ----------
// Va detrás del contenido y bajo un velo berenjena para que el texto se lea; entra y sale fundido y se repite en bucle.
// En el guion: escena.fondo = ruta del clip (sin audio) y escena.fondo_dur = lo que dura, en segundos.
// O escena.fondos = varios planos por capítulo (v9, 4-oct-2026: sus vídeos de la cámara DJI en vez de planos generados):
// cada uno vive entre la palabra «desde» y la palabra «hasta», empieza en inicio_s del clip y lleva más o menos velo.
// «ancla» hace que el segundo clip_s del clip caiga justo cuando ella dice esa palabra (la puerta que se abre).
type FondoDef = {src: string; dur?: number; desde?: string; hasta?: string; velo?: number; inicio_s?: number; ancla?: {en: string; clip_s: number}; bucle?: boolean};
const fondosDe = (c: Cap): FondoDef[] => (c.escena.fondos as FondoDef[]) || (c.escena.fondo ? [{src: c.escena.fondo, dur: c.escena.fondo_dur || 5, bucle: true}] : []);
const FondoCap: React.FC = () => {
  const t = useT();
  return <>{CAPS.flatMap((c, i) => fondosDe(c).map((fd, k) => {
    const a = fd.desde ? dice(c, fd.desde) - 0.35 : inicio(i), b = fd.hasta ? dice(c, fd.hasta) - 0.1 : fin(i);
    if (t < a - 0.1 || t > b + 0.1) return null;
    const op = suave(lin(t, a, a + 0.8)) * (1 - suave(lin(t, b - 0.6, b))), v = fd.velo ?? 1;
    const salto = fd.ancla ? Math.max(0, fd.ancla.clip_s - (dice(c, fd.ancla.en) - a)) : fd.inicio_s || 0;
    const video = <OffthreadVideo src={staticFile(fd.src)} muted trimBefore={Math.round(salto * 30)} style={{width: '100%', height: '100%', objectFit: 'cover'}} />;
    return <Sequence key={c.id + k} from={Math.round(a * 30)} durationInFrames={Math.round((b - a) * 30) + 6} layout="none">
      <AbsoluteFill style={{opacity: op}}>
        {fd.bucle ? <Loop durationInFrames={Math.max(30, Math.round((fd.dur || 5) * 30) - 2)}>{video}</Loop> : video}
        <AbsoluteFill style={{background: `linear-gradient(180deg, rgba(29,17,39,${0.6 * v}) 0%, rgba(45,27,61,${0.7 * v}) 55%, rgba(29,17,39,${0.82 * v}) 100%)`}} />
      </AbsoluteFill>
    </Sequence>;
  }))}</>;
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
    <div style={{position: 'absolute', left: ZONA.x, top: ZONA.y, width: ZONA.w, height: ZONA.h, transform: `translateY(${flota}px) scale(${empuje})`}}>{/* la flotación va en el transform y no en «top»: con «top» el navegador la redondea al píxel y todo el escenario daba un saltito de 1 px cada medio segundo (medido el 6-oct en el giro de la mama) */}
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
    case 'ficha': return <EFicha c={c} w={w} h={h} />;
    case 'dosCaras': return <EDosCaras c={c} w={w} h={h} />;
    case 'contadores': return <EContadores c={c} w={w} h={h} />;
    case 'estrella': return <EEstrella c={c} w={w} h={h} />;
    case 'diagrama': return <EDiagrama c={c} w={w} h={h} />;
    case 'lista': return <ELista c={c} w={w} h={h} />;
    case 'cita': return <ECita c={c} w={w} h={h} />;
    case 'repo': return <ERepo c={c} w={w} h={h} />;
    case 'puntos': return <EPuntos c={c} w={w} h={h} />;
    case 'dias': return <EDias c={c} w={w} h={h} />;
    case 'clip': return <EClip c={c} w={w} h={h} />;
    case 'clips': return <EClips c={c} w={w} h={h} />;
    case 'visores2': return <EVisores2 c={c} w={w} h={h} />;
    case 'alcance': return <EAlcance c={c} w={w} h={h} />;
    case 'carrera': return <ECarrera c={c} w={w} h={h} />;
    case 'marca': return <EMarca c={c} w={w} h={h} />;
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

// su foto entra, se recoge en un círculo a la izquierda y a su lado se escribe el diagnóstico, línea a línea, según lo dice
// (v10, 6-oct-2026, {{TITULAR}}: «la foto sale muy grande… que la imagen se haga pequeña en círculo y al lado poner mi diagnóstico»).
// Las líneas van en «palabras» y la etiqueta en «rol» para que los subtítulos no repitan lo que ya está escrito.
const EFicha: React.FC<EP> = ({c, w, h}) => {
  const t = useT(); const m = useMuelle(); const e = c.escena; const a = c.t0 - 0.3, g = m(a + 0.05, 14);
  const ps = e.palabras as {txt: string; en: string; acento?: boolean; grande?: boolean}[];
  const p = inOut(lin(t, a + 0.45, a + 1.15));  // de foto a círculo
  const D = 400, [W0, H0] = e.vertical ? [410, 560] : [690, 460], cx1 = w * 0.25, cy = (h - 80) / 2;  // «vertical»: un retrato de medio cuerpo, para que en el círculo no salga solo la cara
  const W = W0 + (D - W0) * p, H = H0 + (D - H0) * p, cx = w / 2 + (cx1 - w / 2) * p, r = suave(lin(t, a + 1.0, a + 1.4));
  return <AbsoluteFill>
    <div style={{position: 'absolute', left: cx - W / 2, top: cy - H / 2, width: W, height: H, borderRadius: 16 + (D / 2 - 16) * p, overflow: 'hidden',
      opacity: cl(g * 1.4), transform: `scale(${0.94 + 0.06 * g})`, boxShadow: `0 0 0 ${5 * p}px rgba(201,139,211,${0.6 * p}), 0 20px 60px rgba(0,0,0,.4)`}}>
      <Img src={staticFile(e.src)} style={{width: '100%', height: '100%', objectFit: 'cover', objectPosition: e.foco || '50% 40%'}} />
    </div>
    <div style={{position: 'absolute', left: cx1 + D / 2 + 90, top: 0, bottom: 80, display: 'flex', flexDirection: 'column', justifyContent: 'center'}}>
      {e.rol && <div style={{fontFamily: MONO, fontSize: 30, letterSpacing: '0.14em', textTransform: 'uppercase', color: K.acento, marginBottom: 16, opacity: r,
        transform: `translateX(${24 * (1 - r)}px)`}}>{e.rol}</div>}
      {ps.map((x, k) => {const tx = Math.max(dice(c, x.en) - 0.08, a + 1.15), q = m(tx, 14);
        return <div key={k} style={{fontFamily: FRAUNCES, fontWeight: 600, fontSize: x.grande ? 104 : 70, lineHeight: 1.12, letterSpacing: '-0.02em', whiteSpace: 'nowrap',
          marginBottom: x.grande ? 12 : 0, color: x.acento ? K.acento : K.crema, fontStyle: x.acento ? 'italic' : 'normal',
          opacity: t < tx ? 0 : cl(q * 1.5), transform: `translateX(${40 * (1 - q)}px)`}}>{x.txt}</div>;})}
    </div>
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
        <div style={{fontFamily: FRAUNCES, fontWeight: 600, fontSize: 150, lineHeight: 1, color: K.crema, letterSpacing: '-0.04em'}}>{Math.round(v * cuenta).toLocaleString(LOC)}</div>
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
  const n = nodos.length, bw = 300, bh = 176, gap = (w - 220 - n * bw) / (n - 1), y = c.escena.abajo ? h - bh - 78 : h / 2 - bh / 2, yc = y + bh / 2;
  const ts = enOrden(nodos.map((x, k) => (x.en ? dice(c, x.en) : c.t0 + k * 0.6) - 0.1));
  const xs = nodos.map((_, k) => 110 + k * (bw + gap));
  const ultimo = ts.filter((x) => t >= x).length - 1;
  return <AbsoluteFill>
    <svg width={w} height={h} style={{position: 'absolute', inset: 0}}>
      {nodos.slice(1).map((_, k) => {const a = xs[k] + bw, b = xs[k + 1], p = suave(lin(t, ts[k + 1] - 0.35, ts[k + 1]));
        return <g key={k}><line x1={a + 8} y1={yc} x2={a + 8 + (b - a - 16) * p} y2={yc} stroke={K.acento} strokeWidth={3} strokeLinecap="round" />
          {p > 0.98 && <path d={`M${b - 20} ${yc - 9} L${b - 8} ${yc} L${b - 20} ${yc + 9}`} fill="none" stroke={K.acento} strokeWidth={3} strokeLinecap="round" />}</g>;})}
      {ultimo >= n - 1 && (() => {const u = ((t - ts[n - 1]) % 2.4) / 2.4, x = xs[0] + bw / 2 + (xs[n - 1] - xs[0]) * u;
        return <circle cx={x} cy={yc} r={9} fill={K.coral} opacity={0.9} />;})()}
    </svg>
    {nodos.map((x, k) => {const g = m(ts[k], 13), vivo = k === ultimo;
      return <div key={k} style={{position: 'absolute', left: xs[k], top: y, width: bw, height: bh, borderRadius: 20, textAlign: 'center',
        background: x.puerta ? 'rgba(255,107,71,.10)' : c.escena.abajo ? 'rgba(29,17,39,.72)' : 'rgba(250,246,240,.05)', border: `2px solid ${x.puerta ? K.coral : vivo ? K.acento : 'rgba(232,212,237,.25)'}`,
        boxShadow: vivo ? `0 0 40px ${x.puerta ? 'rgba(255,107,71,.35)' : 'rgba(201,139,211,.35)'}` : 'none',
        opacity: cl(g * 1.4), transform: `translateY(${30 * (1 - g)}px) scale(${0.9 + 0.1 * g})`, display: 'flex', flexDirection: 'column', justifyContent: 'center'}}>
        <div style={{fontFamily: FRAUNCES, fontWeight: 600, fontSize: Math.min(44, Math.floor((bw - 36) / (x.t.length * 0.56))), color: K.crema}}>{x.t}</div>
        <div style={{fontFamily: MONO, fontSize: 24, letterSpacing: '0.1em', textTransform: 'uppercase', color: x.puerta ? K.coral : K.acento, marginTop: 8}}>{x.s}</div>
      </div>;})}
  </AbsoluteFill>;
};

const ELista: React.FC<EP> = ({c, w, h}) => {
  const t = useT(); const m = useMuelle(); const items = c.escena.items as {icono: string; texto: string; en?: string}[];
  const ts = enOrden(items.map((x, k) => (x.en ? dice(c, x.en) : c.t0 + k * 0.7) - 0.12));
  const alto = Math.min(128, (h - 60) / items.length), y0 = (h - alto * items.length) / 2, ultimo = ts.filter((x) => t >= x).length - 1;
  return <AbsoluteFill>
    {items.map((it, k) => {const g = m(ts[k], 15), vivo = k === ultimo;
      const ancho = Math.min(w - 140, 1320);  // a pantalla completa, filas de ancho legible y centradas
      return <div key={k} style={{position: 'absolute', left: (w - ancho) / 2, width: ancho, top: y0 + k * alto, height: alto - 16, display: 'flex', alignItems: 'center', gap: 28,
        padding: '0 26px', borderRadius: 18, background: vivo ? 'rgba(88,45,105,.82)' : 'rgba(29,17,39,.6)', border: `1px solid ${vivo ? 'rgba(201,139,211,.5)' : 'rgba(232,212,237,.1)'}`,
        opacity: cl(g * 1.4) * (vivo || ultimo < 0 ? 1 : 0.86), transform: `translateX(${-50 * (1 - g)}px)`}}>
        <div style={{width: 62, height: 62, borderRadius: 31, background: 'rgba(168,85,181,.18)', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0}}>
          <Icono n={it.icono} size={34} traza={suave(lin(t, ts[k], ts[k] + 0.6))} /></div>
        <div style={{fontFamily: FRAUNCES, fontWeight: 600, fontSize: 52, color: K.crema, letterSpacing: '-0.01em'}}>{it.texto}</div>
      </div>;})}
  </AbsoluteFill>;
};

const ECita: React.FC<EP> = ({c, w, h}) => {
  const t = useT(); const m = useMuelle(); const tc = c.escena.en ? dice(c, c.escena.en) : c.frases[c.frases.length - 1].t0; const pre = m(c.t0 + 0.2, 16), luz = m(tc - 0.15, 14);
  const g = 0.35 * pre + 0.65 * luz, raya = suave(lin(t, tc + 0.3, tc + 1.2));
  return <AbsoluteFill style={{alignItems: 'center', justifyContent: 'center', padding: '0 110px'}}>
    <div style={{fontFamily: FRAUNCES, fontWeight: 600, fontSize: 150, lineHeight: 0.6, color: K.violeta, opacity: g}}>“</div>
    <div style={{fontFamily: FRAUNCES, fontStyle: 'italic', fontWeight: 600, fontSize: c.escena.grande ? 112 : 86, lineHeight: 1.12, textAlign: 'center', color: K.crema, maxWidth: 1500,
      textWrap: 'balance', textShadow: '0 4px 34px rgba(29,17,39,.95), 0 0 8px rgba(29,17,39,.6)', opacity: cl(g * 1.3), transform: `translateY(${30 * (1 - g)}px)`} as React.CSSProperties}>{c.escena.texto}</div>
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

// pictograma: 100 estrellas pequeñas y se encienden las de la cifra (una frecuencia general, no un dato de su caso)
const EPuntos: React.FC<EP> = ({c, w, h}) => {
  const t = useT(); const m = useMuelle(); const e = c.escena;
  const tLuz = e.en ? dice(c, e.en) - 0.1 : c.t0 + 1, fuertes = e.fuertes ?? 2, flojas = e.flojas ?? 3;
  const n = 100, cols = 20, S = 46, gap = 18, gw = cols * S + (cols - 1) * gap, x0 = (w - gw) / 2, y0 = 60;
  const pie = suave(lin(t, tLuz + 0.4, tLuz + 0.9)), tLbl = e.en_lbl ? dice(c, e.en_lbl) - 0.1 : tLuz + 0.4, rot = suave(lin(t, tLbl, tLbl + 0.5));
  return <AbsoluteFill>
    {Array.from({length: n}, (_, k) => {
      const g = m(c.t0 + 0.012 * k, 18), lit = k < fuertes + flojas, luz = lit ? suave(lin(t, tLuz + 0.08 * k, tLuz + 0.08 * k + 0.4)) : 0;
      const col = k < fuertes ? K.coral : K.acento;
      return <div key={k} style={{position: 'absolute', left: x0 + (k % cols) * (S + gap), top: y0 + Math.floor(k / cols) * (S + gap), width: S, height: S,
        opacity: cl(g * 1.3), transform: `scale(${(0.6 + 0.4 * g) * (1 + 0.3 * luz)})`,
        filter: luz > 0.01 ? `drop-shadow(0 0 ${16 * luz}px ${k < fuertes ? 'rgba(255,107,71,.85)' : 'rgba(201,139,211,.85)'})` : undefined}}>
        <svg viewBox="0 0 20 20" width={S} height={S}><path d={STAR_D} fill={luz > 0.01 ? col : 'rgba(232,212,237,.5)'} opacity={luz > 0.01 ? 0.5 + 0.5 * luz : 1} /></svg>
      </div>;})}
    <div style={{position: 'absolute', left: 0, right: 0, top: y0 + 5 * (S + gap) + 34, textAlign: 'center'}}>
      <div style={{fontFamily: FRAUNCES, fontWeight: 600, fontSize: 64, color: K.crema, letterSpacing: '-0.02em', opacity: pie, transform: `translateY(${20 * (1 - pie)}px)`}}>{e.txt}</div>
      <div style={{fontFamily: MONO, fontSize: 30, letterSpacing: '0.12em', textTransform: 'uppercase', color: K.acento, marginTop: 14, opacity: rot}}>{e.lbl}</div>
    </div>
  </AbsoluteFill>;
};

// el tiempo en días: un punto por día desde abril, que se van llenando; al decir «cada» late el de hoy
const EDias: React.FC<EP> = ({c, w, h}) => {
  const t = useT(); const m = useMuelle(); const e = c.escena; const meses = e.meses as {m: string; d: number}[];
  const total = meses.reduce((a, x) => a + x.d, 0), llena = suave(lin(t, c.t0 + 0.2, c.t0 + 2.8)), tCada = e.en ? dice(c, e.en) - 0.1 : c.t0 + 3;
  const S = 22, gx = 9, gy = 20, x0 = (w - (31 * (S + gx) - gx)) / 2 + 50, y0 = 50, txt = suave(lin(t, tCada, tCada + 0.5));
  let idx = 0;
  return <AbsoluteFill>
    {meses.map((mm, r) => <React.Fragment key={r}>
      <div style={{position: 'absolute', left: x0 - 110, top: y0 + r * (S + gy) - 3, width: 88, textAlign: 'right', fontFamily: MONO, fontSize: 22,
        letterSpacing: '0.12em', color: K.acento, opacity: cl(m(c.t0 + 0.06 * r, 16) * 1.3)}}>{mm.m}</div>
      {Array.from({length: mm.d}, (_, d) => {const k = idx++, on = (k + 1) / total <= llena + 1e-6, hoy = k === total - 1;
        const late = hoy ? (0.55 + 0.45 * Math.sin((t - tCada) * 6)) * txt : 0;
        return <div key={d} style={{position: 'absolute', left: x0 + d * (S + gx), top: y0 + r * (S + gy), width: S, height: S, borderRadius: S / 2,
          background: hoy && txt > 0.01 ? K.coral : on ? K.acento : 'rgba(232,212,237,.12)', transform: `scale(${1 + 0.7 * late})`,
          boxShadow: late ? `0 0 ${26 * late}px rgba(255,107,71,.9)` : undefined}} />;})}
    </React.Fragment>)}
    <div style={{position: 'absolute', left: 0, right: 0, top: y0 + meses.length * (S + gy) + 46, textAlign: 'center', fontFamily: FRAUNCES, fontWeight: 600,
      fontSize: 92, color: K.crema, letterSpacing: '-0.02em', opacity: txt, transform: `translateY(${20 * (1 - txt)}px)`}}>{e.txt}</div>
  </AbsoluteFill>;
};

// ---------- v9 (4-oct-2026): sus propios vídeos, los visores uno a uno, el alcance, la carrera y la marca ----------
// un plano suyo (cámara DJI) dentro de la ventana, a la vista y sin velo; el rótulo entra DESPUÉS, al decir su palabra
const EClip: React.FC<EP> = ({c, w, h}) => {
  const t = useT(); const m = useMuelle(); const e = c.escena; const a = c.t0 - 0.3, g = m(a + 0.05, 15);
  const tTxt = e.en ? dice(c, e.en) - 0.1 : a + 1.2, r = suave(lin(t, tTxt, tTxt + 0.5)), z = 1 + 0.05 * lin(t, a, a + 8);
  const W = Math.min(w - 200, (h + 70) * 16 / 9), H = W * 9 / 16;  // v10: la ventana crece hacia arriba (no hay titular) para que sus planos se vean grandes
  return <AbsoluteFill style={{alignItems: 'center', justifyContent: 'center'}}>
    <div style={{width: W, height: H, borderRadius: 22, overflow: 'hidden', position: 'relative', background: K.noche, boxShadow: '0 24px 70px rgba(0,0,0,.45)',
      border: '1px solid rgba(232,212,237,.16)', opacity: cl(g * 1.4), flexShrink: 0, transform: `translateY(-60px) scale(${0.94 + 0.06 * g})`}}>
      {/\.(png|jpe?g)$/.test(e.src)
        ? <Img src={staticFile(e.src)} style={{width: '100%', height: '100%', objectFit: 'cover', transform: `scale(${1.02 + 0.1 * lin(t, a, a + 8)})`}} />
        : <Sequence from={Math.round(a * 30)} layout="none">
          <OffthreadVideo src={staticFile(e.src)} muted trimBefore={Math.round((e.inicio_s || 0) * 30)} style={{width: '100%', height: '100%', objectFit: 'cover', transform: `scale(${z})`}} />
        </Sequence>}
      {e.texto && <div style={{position: 'absolute', left: 0, right: 0, bottom: 0, padding: '110px 46px 34px', background: 'linear-gradient(180deg, rgba(29,17,39,0), rgba(29,17,39,.9))', opacity: r}}>
        <div style={{fontFamily: FRAUNCES, fontWeight: 600, fontSize: 76, lineHeight: 1.05, color: K.crema, letterSpacing: '-0.02em', transform: `translateY(${22 * (1 - r)}px)`}}>{e.texto}</div>
      </div>}
      {e.rotulo && <Rotulo txt={e.rotulo} op={suave(lin(t, a + 0.5, a + 0.9))} />}
    </div>
  </AbsoluteFill>;
};

// el rótulo pequeño de un plano: dice dónde estamos sin taparlo
// «ruta» (vídeo para Lexie, 17-oct-2026): debajo del nombre de la herramienta, su dirección en la web, en minúscula y en acento
const Rotulo: React.FC<{txt: string; op: number; ruta?: string}> = ({txt, op, ruta}) => (
  // con ruta va arriba a la DERECHA: a la izquierda pisaba el título de la página grabada (hoja de contactos, 17-oct)
  <div style={{position: 'absolute', ...(ruta ? {right: 32} : {left: 32}), top: 30, padding: ruta ? '15px 28px 14px' : '13px 24px 11px', borderRadius: ruta ? 20 : 999, background: 'rgba(29,17,39,.82)',
    border: '1px solid rgba(232,212,237,.22)', fontFamily: MONO, opacity: op, textAlign: ruta ? 'right' : 'left', transform: `translateX(${(ruta ? 14 : -14) * (1 - op)}px)`}}>
    <div style={{fontSize: 28, letterSpacing: '0.10em', textTransform: 'uppercase', color: K.crema}}>{txt}</div>
    {ruta && <div style={{fontSize: 28, letterSpacing: '0.02em', color: K.acento, marginTop: 6}}>{ruta}</div>}
  </div>
);

// varios planos suyos en la MISMA ventana, a corte (v10, 5-oct-2026: los clips los eligió ella uno a uno y van limpios, sin
// velo ni palabras grandes encima). Cada corte entra cuando dice su palabra «desde»; «rotulo» es pequeño y dice dónde
// estamos; «texto», grande, solo donde ella pidió que el texto saliera después del plano.
const EClips: React.FC<EP> = ({c, w, h}) => {
  const t = useT(); const m = useMuelle(); const e = c.escena; const a = c.t0 - 0.3, g = m(a + 0.05, 15);
  const cs = e.cortes as {src: string; desde?: string; inicio_s?: number; rotulo?: string; ruta?: string; texto?: string; en?: string}[];
  const ts = enOrden(cs.map((x, k) => (k === 0 ? a : dice(c, x.desde) - 0.12)));
  const vivo = Math.max(0, ts.filter((x) => t >= x).length - 1);
  const W = Math.min(w - 200, (h + 70) * 16 / 9), H = W * 9 / 16;  // v10: la ventana crece hacia arriba (no hay titular) para que sus planos se vean grandes
  return <AbsoluteFill style={{alignItems: 'center', justifyContent: 'center'}}>
    <div style={{width: W, height: H, borderRadius: 22, overflow: 'hidden', position: 'relative', background: K.noche, boxShadow: '0 24px 70px rgba(0,0,0,.45)',
      border: '1px solid rgba(232,212,237,.16)', opacity: cl(g * 1.4), flexShrink: 0, transform: `translateY(-60px) scale(${0.94 + 0.06 * g})`}}>
      {[vivo - 1, vivo].filter((k) => k >= 0 && (k === vivo || t < ts[vivo] + 0.2)).map((k) => {
        const x = cs[k], op = k === vivo && k > 0 ? lin(t, ts[k], ts[k] + 0.14) : 1, z = 1 + 0.04 * lin(t, ts[k], ts[k] + 6);
        const tR = x.en ? dice(c, x.en) - 0.1 : ts[k] + 0.35, r = suave(lin(t, tR, tR + 0.4));
        return <AbsoluteFill key={k} style={{opacity: op}}>
          <Sequence from={Math.round(ts[k] * 30)} layout="none">
            <OffthreadVideo src={staticFile(x.src)} muted trimBefore={Math.round((x.inicio_s || 0) * 30)} style={{width: '100%', height: '100%', objectFit: 'cover', transform: `scale(${z})`}} />
          </Sequence>
          {x.texto && <div style={{position: 'absolute', left: 0, right: 0, bottom: 0, padding: '110px 46px 34px', background: 'linear-gradient(180deg, rgba(29,17,39,0), rgba(29,17,39,.9))', opacity: r}}>
            <div style={{fontFamily: FRAUNCES, fontWeight: 600, fontSize: 76, lineHeight: 1.05, color: K.crema, letterSpacing: '-0.02em', transform: `translateY(${22 * (1 - r)}px)`}}>{x.texto}</div>
          </div>}
          {x.rotulo && <Rotulo txt={x.rotulo} op={r} ruta={x.ruta} />}
        </AbsoluteFill>;
      })}
    </div>
  </AbsoluteFill>;
};

// los visores uno a uno ({{TITULAR}}, 4-oct: «en grande cada gráfico, incluido el de la mama, e ir haciéndolos pequeños conforme
// van saliendo»): cada pieza entra GRANDE al decir su palabra y se recoge a la columna de la izquierda cuando entra la
// siguiente; la última se queda grande, y al decir en_diana se marca como la que sirvió para elegir.
const EVisores2: React.FC<EP> = ({c, w, h}) => {
  const t = useT(); const m = useMuelle(); const e = c.escena;
  const ps = e.piezas as {dir?: string; n?: number; src?: string; nombre: string; en?: string}[];
  const ts = enOrden(ps.map((p, k) => (k === 0 ? c.t0 - 0.25 : dice(c, p.en!) - 0.15)));
  const n = ps.length - 1, S = Math.min(200, (h - 60 - (n - 1) * 20) / n), gap = 20, y0 = (h - (n * S + (n - 1) * gap)) / 2;
  const BX = 110 + S + 60, BW = w - BX - 110, BH = h - 40, BY = 20;
  const tD = e.en_diana ? dice(c, e.en_diana) - 0.1 : 1e9, d = m(tD, 12), late = t > tD ? 0.5 + 0.5 * Math.sin((t - tD) * 4) : 0;
  return <AbsoluteFill>
    {ps.map((p, k) => {
      if (t < ts[k] - 0.05) return null;
      const g = m(ts[k], 14), q = k < n ? inOut(lin(t, ts[k + 1] - 0.1, ts[k + 1] + 0.5)) : 0, diana = k === n ? d : 0;
      const x = BX + (110 - BX) * q, y = BY + (y0 + k * (S + gap) - BY) * q, W = BW + (S - BW) * q, H = BH + (S - BH) * q;
      return <div key={k} style={{position: 'absolute', left: x, top: y, width: W, height: H, borderRadius: 22 - 6 * q, overflow: 'hidden', background: K.noche, zIndex: q > 0.5 ? 1 : 2,
        border: diana > 0.02 ? `${2 + 2 * diana}px solid ${K.coral}` : '1px solid rgba(232,212,237,.14)',
        boxShadow: diana > 0.02 ? `0 0 ${40 + 30 * late}px rgba(255,107,71,${0.25 + 0.25 * late})` : '0 24px 60px rgba(0,0,0,.4)',
        opacity: cl(g * 1.3), transform: `translateY(${40 * (1 - g)}px) scale(${0.9 + 0.1 * g})`}}>
        {p.dir ? <Secuencia dir={p.dir} n={p.n || 120} t={t} desde={ts[k]} style={{width: '100%', height: '100%', objectFit: 'contain'}} />
          : <Img src={staticFile(p.src!)} style={{width: '100%', height: '100%', objectFit: 'contain', padding: `${3 - 2 * q}%`}} />}
        <div style={{position: 'absolute', left: 24, bottom: 18, fontFamily: MONO, fontSize: 30, letterSpacing: '0.14em', textTransform: 'uppercase', color: K.acento, opacity: 1 - cl(q * 3)}}>{p.nombre}</div>
        {k === n && e.diana && diana > 0.02 && <div style={{position: 'absolute', right: 22, bottom: 16, padding: '8px 20px', borderRadius: 999, background: 'rgba(255,107,71,.16)',
          border: `1px solid ${K.coral}`, fontFamily: HANKEN, fontWeight: 700, fontSize: 36, color: K.crema, opacity: cl(diana * 1.4), transform: `translateX(${30 * (1 - diana)}px)`}}>{e.diana}</div>}
      </div>;
    })}
  </AbsoluteFill>;
};

// el alcance: la cifra total cuenta, debajo el reparto por red, y a la derecha la pieza de prensa (su cabecera real)
const EAlcance: React.FC<EP> = ({c, w, h}) => {
  const t = useT(); const m = useMuelle(); const e = c.escena; const a = c.t0 - 0.2, g = m(a, 14), cuenta = suave(lin(t, a, a + 1.3));
  const redes = (e.redes || []) as {nombre: string; k?: string; txt?: string}[], pr = e.prensa as {src?: string; cita?: string; pie: string; en?: string} | undefined;
  const v = (CIFRAS[e.k] || 0) * cuenta, cifra = e.millones ? `${(Math.floor(v / 1e5) / 10).toLocaleString(LOC, {minimumFractionDigits: 1})} ${MILLONES}` : Math.round(v).toLocaleString(LOC);
  const tP = pr ? (pr.en ? dice(c, pr.en) - 0.1 : a + 0.8) : 1e9, p = m(tP, 13), ancho = pr ? w * 0.56 : w;
  return <AbsoluteFill>
    <div style={{position: 'absolute', left: 0, width: ancho, top: 0, bottom: 0, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
      opacity: cl(g * 1.4), transform: `translateY(${40 * (1 - g)}px)`}}>
      {e.pre && <div style={{fontFamily: FRAUNCES, fontStyle: 'italic', fontWeight: 600, fontSize: 54, color: K.suave, marginBottom: 6}}>{e.pre}</div>}
      <div style={{fontFamily: FRAUNCES, fontWeight: 600, fontSize: e.millones ? 132 : pr ? 150 : 170, lineHeight: 1, color: K.crema, letterSpacing: '-0.04em', whiteSpace: 'nowrap'}}>{cifra}</div>
      <div style={{marginTop: 18, fontFamily: MONO, fontSize: 30, letterSpacing: '0.1em', textTransform: 'uppercase', color: K.acento, textAlign: 'center', maxWidth: ancho - 80}}>{e.lbl}</div>
      {redes.length > 0 && <div style={{display: 'flex', gap: 12, marginTop: 36}}>
        {redes.map((r, k) => {const gk = m(a + 0.5 + k * 0.18, 15);
          return <div key={r.nombre} style={{padding: '12px 18px', borderRadius: 18, background: 'rgba(250,246,240,.05)', border: '1px solid rgba(232,212,237,.22)', textAlign: 'center',
            opacity: cl(gk * 1.4), transform: `translateY(${24 * (1 - gk)}px)`}}>
            <div style={{fontFamily: FRAUNCES, fontWeight: 600, fontSize: 42, color: K.crema, letterSpacing: '-0.02em', whiteSpace: 'nowrap'}}>{r.txt || (CIFRAS[r.k || ''] || 0).toLocaleString(LOC)}</div>
            <div style={{fontFamily: MONO, fontSize: 24, letterSpacing: '0.08em', textTransform: 'uppercase', color: K.acento, marginTop: 6}}>{r.nombre}</div>
          </div>;})}
      </div>}
    </div>
    {pr && <div style={{position: 'absolute', left: w * 0.58, top: h / 2 - 250, width: w * 0.36, opacity: cl(p * 1.4), transform: `translateX(${80 * (1 - p)}px) rotate(${2.2 - 1.2 * p}deg)`}}>
      {pr.cita
        ? <div style={{background: K.crema, borderRadius: 18, padding: '44px 46px 40px', boxShadow: '0 24px 70px rgba(0,0,0,.5)'}}>
          <div style={{fontFamily: FRAUNCES, fontWeight: 600, fontSize: 110, lineHeight: 0.5, color: K.violeta}}>“</div>
          <div style={{fontFamily: FRAUNCES, fontStyle: 'italic', fontWeight: 600, fontSize: 52, lineHeight: 1.14, color: K.berenjena, letterSpacing: '-0.01em'}}>{pr.cita}</div>
        </div>
        : <div style={{background: '#fff', borderRadius: 16, paddingBottom: 22, overflow: 'hidden', boxShadow: '0 24px 70px rgba(0,0,0,.5)'}}><Img src={staticFile(pr.src!)} style={{width: '100%', display: 'block'}} /></div>}
      <div style={{marginTop: 18, fontFamily: MONO, fontSize: 28, letterSpacing: '0.12em', textTransform: 'uppercase', color: K.suave, textAlign: 'center'}}>{pr.pie}</div>
    </div>}
  </AbsoluteFill>;
};

// la carrera ({{TITULAR}}, 4-oct: «quiero mostrar que es una carrera»): un mes por marca desde el diagnóstico; la estrella los
// recorre de un tirón hasta hoy y por delante solo queda pista sin marcar. Sin cifra de vida: no hay fuente que la sostenga.
const ECarrera: React.FC<EP> = ({c, w, h}) => {
  const t = useT(); const m = useMuelle(); const e = c.escena; const N = e.meses as number;
  const tA = e.en ? dice(c, e.en) - 0.15 : c.t0, corre = inOut(lin(t, tA, tA + 1.8)), g = m(tA - 0.1, 14);
  const tC = e.en_carrera ? dice(c, e.en_carrera) - 0.1 : 1e9, k2 = m(tC, 12);
  const x0 = 210, x1 = w - 330, y = h / 2 + 40, paso = (x1 - x0) / N, cab = x0 + (x1 - x0) * corre, mes = Math.max(1, Math.round(N * corre));
  const an = Math.floor(mes / 12), ms = mes % 12;
  const U = EN ? {a: ['year', 'years'], m: ['month', 'months'], y: ' and '} : {a: ['año', 'años'], m: ['mes', 'meses'], y: ' y '};
  const lleva = [an ? `${an} ${U.a[an === 1 ? 0 : 1]}` : '', ms || !an ? `${ms} ${U.m[ms === 1 ? 0 : 1]}` : ''].filter(Boolean).join(U.y);
  return <AbsoluteFill style={{opacity: cl(g * 1.4)}}>
    <div style={{position: 'absolute', left: 0, right: 0, top: y - 300, textAlign: 'center'}}>
      <div style={{fontFamily: FRAUNCES, fontWeight: 600, fontSize: 132, lineHeight: 1, color: K.crema, letterSpacing: '-0.03em', textShadow: '0 4px 34px rgba(29,17,39,.95), 0 0 8px rgba(29,17,39,.6)'}}>
        {lleva}</div>
      <div style={{marginTop: 16, fontFamily: MONO, fontSize: 32, letterSpacing: '0.12em', textTransform: 'uppercase', color: K.acento, textShadow: '0 4px 34px rgba(29,17,39,.95), 0 0 8px rgba(29,17,39,.6)'}}>{e.lbl}</div>
    </div>
    <svg width={w} height={h} style={{position: 'absolute', inset: 0, overflow: 'visible'}}>
      <line x1={x0} y1={y} x2={cab} y2={y} stroke={K.acento} strokeWidth={10} strokeLinecap="round" />
      <line x1={cab + 26} y1={y} x2={w - 60} y2={y} stroke="rgba(232,212,237,.35)" strokeWidth={4} strokeLinecap="round" strokeDasharray="4 22" strokeDashoffset={-t * 60} />
      {Array.from({length: N + 1}, (_, k) => {const x = x0 + k * paso, on = x <= cab + 1, anio = k % 12 === 0;
        return <line key={k} x1={x} y1={y - (anio ? 34 : 15)} x2={x} y2={y + (anio ? 34 : 15)} stroke={on ? K.crema : 'rgba(232,212,237,.2)'} strokeWidth={anio ? 6 : 3} strokeLinecap="round" />;})}
      <g transform={`translate(${cab - 34},${y - 34}) scale(${3.4 * (1 + 0.12 * Math.sin(t * 7) * (corre > 0.99 ? 1 : 0.3))})`}>
        <path d={STAR_D} fill={K.coral} style={{filter: 'drop-shadow(0 0 6px rgba(255,107,71,.9))'}} /></g>
    </svg>
    <div style={{position: 'absolute', left: x0 - 70, top: y + 50, fontFamily: MONO, fontSize: 30, letterSpacing: '0.1em', color: K.crema, textShadow: '0 4px 34px rgba(29,17,39,.95), 0 0 8px rgba(29,17,39,.6)'}}>{e.inicio}</div>
    <div style={{position: 'absolute', left: x1 - 120, top: y + 50, width: 240, textAlign: 'center', whiteSpace: 'nowrap', fontFamily: MONO, fontSize: 30, letterSpacing: '0.1em', color: K.coral, opacity: lin(corre, 0.9, 1), textShadow: '0 4px 34px rgba(29,17,39,.95), 0 0 8px rgba(29,17,39,.6)'}}>{e.hoy || 'HOY'}</div>
    {e.txt2 && <div style={{position: 'absolute', left: 0, right: 0, top: y + 110, textAlign: 'center', fontFamily: FRAUNCES, fontStyle: 'italic', fontWeight: 600, fontSize: 92,
      color: K.crema, letterSpacing: '-0.02em', textShadow: '0 4px 34px rgba(29,17,39,.95), 0 0 8px rgba(29,17,39,.6)', opacity: cl(k2 * 1.4), transform: `translateY(${24 * (1 - k2)}px)`}}>{e.txt2}</div>}
  </AbsoluteFill>;
};

// la marca: su lámina (la escalera) y el logo de Beyond the protocol tal como están en su sistema de diseño
const Monograma: React.FC<{size: number}> = ({size}) => (
  <svg width={size} height={size} viewBox="0 0 240 240">
    <path d="M192.94 24.47 L199.74 37.67 L212.94 44.47 L199.74 51.27 L192.94 64.47 L186.14 51.27 L172.94 44.47 L186.14 37.67 Z" fill={K.acento} />
    <path d="M48.33 173.84 L52.75 182.42 L61.33 186.84 L52.75 191.26 L48.33 199.84 L43.91 191.26 L35.33 186.84 L43.91 182.42 Z" fill={K.acento} />
    <path d="M55.05 44.24 L57.94 49.85 L63.55 52.74 L57.94 55.63 L55.05 61.24 L52.16 55.63 L46.55 52.74 L52.16 49.85 Z" fill={K.acento} opacity={0.5} />
    <circle cx={182.5} cy={184.7} r={3.4} fill={K.acento} /><circle cx={212.8} cy={134.7} r={2.3} fill={K.acento} opacity={0.5} />
    <circle cx={120} cy={120} r={76} fill={K.crema} />
    <text x={120} y={147} textAnchor="middle" fontFamily={FRAUNCES} fontStyle="italic" fontWeight={600} fontSize={120} fill={K.berenjena}>m</text>
  </svg>
);
const EMarca: React.FC<EP> = ({c, w, h}) => {
  const t = useT(); const m = useMuelle(); const e = c.escena; const a = c.t0 - 0.3, g = m(a + 0.1, 13);
  const tN = e.en ? dice(c, e.en) - 0.1 : a + 0.6, nb = m(tN, 12), tL = e.en_lbl ? dice(c, e.en_lbl) - 0.1 : tN + 0.9, lb = suave(lin(t, tL, tL + 0.5));
  const t2 = e.lbl2 ? dice(c, e.en2) - 0.1 : 1e9, l2 = suave(lin(t, t2, t2 + 0.5));
  const LH = h - 30, LW = LH * 0.8;
  return <AbsoluteFill>
    <div style={{position: 'absolute', left: 190, top: 15, width: LW, height: LH, padding: 18, background: K.crema, borderRadius: 10, boxShadow: '0 30px 80px rgba(0,0,0,.5)',
      opacity: cl(g * 1.4), transform: `translateY(${60 * (1 - g)}px) rotate(${-3 + 0.6 * Math.sin(t * 0.5)}deg)`}}>
      <Img src={staticFile(e.lamina)} style={{width: '100%', height: '100%', objectFit: 'cover', borderRadius: 4}} />
    </div>
    <div style={{position: 'absolute', left: 190 + LW + 110, right: 90, top: h / 2 - 190}}>
      <div style={{display: 'flex', alignItems: 'center', gap: 26, opacity: cl(nb * 1.4), transform: `translateX(${50 * (1 - nb)}px)`}}>
        <Monograma size={150} />
        <div>
          {e.pre && <div style={{fontFamily: MONO, fontSize: 30, letterSpacing: '0.16em', textTransform: 'uppercase', color: K.acento, marginBottom: 14}}>{e.pre}</div>}
          <div style={{fontFamily: FRAUNCES, fontWeight: 600, fontSize: 74, lineHeight: 1, color: K.crema, letterSpacing: '-0.025em', whiteSpace: 'nowrap'}}>{e.nombre}</div>
          {e.url && <div style={{fontFamily: MONO, fontSize: 36, color: K.acento, marginTop: 10}}>{e.url}</div>}
        </div>
      </div>
      <div style={{position: 'relative', marginTop: 54, marginLeft: 22, fontFamily: FRAUNCES, fontStyle: 'italic', fontWeight: 600, fontSize: 54, lineHeight: 1.15, color: K.suave}}>
        <div style={{opacity: lb, transform: `translateY(${20 * (1 - lb)}px)`}}>{e.lbl}</div>
        {e.lbl2 && <div style={{marginTop: 14, color: K.crema, opacity: l2, transform: `translateY(${16 * (1 - l2)}px)`}}>{e.lbl2}</div>}
      </div>
    </div>
  </AbsoluteFill>;
};

// ---------- lo mejor de la v18, dentro de la ventana ----------
// «desde»: el giro arranca en el primer fotograma cuando entra la pieza y, al llegar al último, vuelve hacia atrás. Las
// secuencias no cierran el bucle: del último fotograma al primero hay un salto, y con el reloj global ese salto caía donde
// tocara ({{TITULAR}}, 6-oct: «el de la mama pega un tropezón al girar, tiene que girar fluido»).
const Secuencia: React.FC<{dir: string; n: number; t: number; desde?: number; style: React.CSSProperties}> = ({dir, n, t, desde, style}) => {
  const f = Math.floor(Math.max(0, t - (desde ?? 0)) * 30), ida = f % (2 * (n - 1));
  const k = desde === undefined ? f % n : ida < n ? ida : 2 * (n - 1) - ida;
  return <Img src={staticFile(`${dir}/${String(1 + k).padStart(4, '0')}.jpg`)} style={style} />;
};

// varios pasos en un capítulo: cada uno entra cuando ella dice su palabra y el anterior sale borroso, en la misma ventana
const EPasos: React.FC<EP> = ({c, w, h}) => {
  const t = useT(); const pasos = c.escena.pasos as any[];
  const ts = enOrden(pasos.map((p, k) => (k === 0 ? c.t0 - 0.3 : dice(c, p.desde) - 0.25)));
  const vivo = Math.max(0, ts.filter((x) => t >= x).length - 1);
  // cuando lo que entra es una ventana con sus planos, lo anterior (palabras grandes) se va antes: si no, se funden encima del plano
  const plano = /^clips?$/.test(pasos[vivo].tipo);
  return <>{[vivo - 1, vivo].filter((k) => k >= 0).map((k) => {
    const op = k < vivo ? 1 - suave(lin(t, ts[vivo], ts[vivo] + (plano ? 0.14 : 0.3))) : k === 0 ? 1 : suave(lin(t, ts[k] + (plano ? 0.1 : 0), ts[k] + 0.45));
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
        color: p.acento ? K.acento : K.crema, opacity: cl(g * 1.5) * Math.max(0.38, 1 - 0.3 * hund), textShadow: '0 4px 34px rgba(29,17,39,.95), 0 0 8px rgba(29,17,39,.6)',
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
      <div style={{fontFamily: MONO, fontSize: 34, letterSpacing: '0.14em', textTransform: 'uppercase', color: K.acento, marginTop: 12}}>{e.rol}</div>
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
  const e = c.escena, g = m(inicio(i) + 0.55, 12), tSub = e.sub ? dice(c, e.en_sub) - 0.1 : 0, sb = e.sub ? m(tSub, 13) : 0;
  const tUrl = e.sub ? c.frases[c.frases.length - 1].t0 - 0.2 : dice(c, 'help'), u = m(tUrl - 0.1, 11);
  // con plano de ambiente (Flow, sin datos): el amanecer de fondo, un velo berenjena para que el texto se lea, y su estrella hace de la nuestra
  const fondo = e.video ? suave(lin(t, inicio(i) + 0.2, inicio(i) + 1.2)) : 0;
  return <AbsoluteFill style={{alignItems: 'center', justifyContent: 'center'}}>
    {e.video && <AbsoluteFill style={{opacity: fondo}}>
      <OffthreadVideo src={staticFile(e.video)} muted style={{width: '100%', height: '100%', objectFit: 'cover'}} />
      <AbsoluteFill style={{background: 'linear-gradient(180deg, rgba(29,17,39,.35) 0%, rgba(29,17,39,.65) 45%, rgba(29,17,39,.8) 100%)'}} />
    </AbsoluteFill>}
    {!e.video && <div style={{position: 'absolute', left: 960 - 60, top: 250, width: 120, height: 120, transform: `scale(${0.3 + 0.7 * g}) rotate(${45 * g}deg)`,
      filter: 'drop-shadow(0 0 40px rgba(255,107,71,.6))'}}><svg viewBox="0 0 20 20" width={120} height={120}><path d={STAR_D} fill={K.coral} /></svg></div>}
    {e.credito && (() => {const k = suave(lin(t, T_CREDITO, T_CREDITO + 0.6));  // nota de créditos, pequeña y sin voz
      return k > 0.01 && <div style={{position: 'absolute', bottom: 70, left: 0, right: 0, textAlign: 'center', opacity: 0.85 * k,
        fontFamily: HANKEN, fontWeight: 500, fontSize: 36, color: K.crema, textShadow: '0 2px 16px rgba(29,17,39,.9)'}}>{e.credito}</div>;})()}
    {e.foto && (() => {const q = m(inicio(i) + 0.4, 13), D = 216;  // ella, sonriendo, encima del titular (6-oct: «terminar con una imagen mía sonriendo en vez de solo texto»)
      return <div style={{position: 'absolute', left: 960 - D / 2, top: 44, width: D, height: D, borderRadius: '50%', overflow: 'hidden', opacity: cl(q * 1.4),
        transform: `scale(${0.8 + 0.2 * q})`, boxShadow: '0 0 0 5px rgba(201,139,211,.6), 0 18px 50px rgba(0,0,0,.5)'}}>
        <Img src={staticFile(e.foto)} style={{width: '100%', height: '100%', objectFit: 'cover', objectPosition: e.foco || '50% 40%'}} />
      </div>;})()}
    <div style={{position: 'absolute', top: e.sub ? (e.foto ? 286 : 270) : 410, left: 0, right: 0, textAlign: 'center', textShadow: '0 4px 40px rgba(29,17,39,.85)'}}>
      <div style={{fontFamily: FRAUNCES, fontWeight: 600, fontSize: e.titular_px || (e.sub ? 104 : 124), lineHeight: 1.14, color: K.crema, letterSpacing: '-0.03em', maxWidth: e.titular_ancho || (e.sub ? 1400 : undefined),
        margin: '0 auto', textWrap: 'balance', opacity: cl(g * 1.4), transform: `translateY(${30 * (1 - g)}px)`} as React.CSSProperties}>{e.titular}</div>
      {e.sub && <div style={{fontFamily: FRAUNCES, fontStyle: 'italic', fontWeight: 600, fontSize: 54, lineHeight: 1.15, color: K.suave, margin: '22px auto 0', maxWidth: 1400,
        textWrap: 'balance', opacity: cl(sb * 1.4), transform: `translateY(${24 * (1 - sb)}px)`} as React.CSSProperties}>{e.sub}</div>}
      <div style={{fontFamily: FRAUNCES, fontWeight: 600, fontSize: e.sub ? 84 : 96, color: K.acento, marginTop: 28, opacity: cl(u * 1.4),
        transform: `scale(${(0.85 + 0.15 * u) * (1 + 0.02 * Math.sin((t - tUrl) * 5) * u)})`}}>{e.url}</div>
      <div style={{fontFamily: MONO, fontSize: 30, letterSpacing: '0.14em', textTransform: 'uppercase', color: 'rgba(250,246,240,.85)', marginTop: 30,
        opacity: suave(lin(t, tUrl + 0.6, tUrl + 1.1))}}>{e.pie}</div>
    </div>
    <AbsoluteFill style={{background: K.noche, opacity: lin(t, T.total - 0.5, T.total - 0.05)}} />
  </AbsoluteFill>;
};

// ---------- aviso de voz clonada (art. 50.4 del Reglamento de IA) ----------
// {{TITULAR}} (3-oct-2026): «que sea un detalle, escrito en pequeño». Legal: tiene que verse como tarde al empezar.
// Así que: etiqueta pequeña en una esquina durante los primeros segundos, y la nota de créditos al final (Cierre).
const AVISO = (T as any).aviso as {texto: string; entrada: number; hasta?: number} | undefined;
const ULTIMA = CAPS[CAPS.length - 1];
const T_CREDITO = ULTIMA.frases[ULTIMA.frases.length - 1].t1 + 1.0;  // cuando ya se ha ido el subtítulo de «Gracias»
const AvisoVoz: React.FC = () => {
  const t = useT(); if (!AVISO) return null;
  const hasta = AVISO.hasta ?? AVISO.entrada + 8, v = suave(lin(t, 0.2, 0.7)) * (1 - suave(lin(t, hasta - 0.5, hasta)));
  if (v <= 0.01) return null;
  return <div style={{position: 'absolute', top: 30, right: 36, opacity: v, padding: '6px 18px', borderRadius: 999, background: 'rgba(29,17,39,.7)',
    border: '1px solid rgba(232,212,237,.22)', fontFamily: HANKEN, fontWeight: 500, fontSize: 32, color: K.crema}}>{AVISO.texto}</div>;
};

// ---------- subtítulos: quitan solos lo que ya está escrito en pantalla ----------
// REMOTION_SUBS_SIEMPRE=1 saca la variante con subtítulos en todas las frases, también donde el texto ya está en pantalla
// ({{TITULAR}}, 7-oct-2026: «haz una versión con los subtítulos que salgan siempre»). Sin la variable, el vídeo de siempre.
declare const process: {env: Record<string, string | undefined>};  // Remotion mete en el paquete las variables REMOTION_*
const SUBS_SIEMPRE = process.env.REMOTION_SUBS_SIEMPRE === '1';
const VACIAS = new Set('el la los las un una unos unas de del y a en que con por para es no se lo le mi mis me yo su sus al como mas pero o the a of and to is it'.split(' '));
const enPantalla = (c: Cap): Set<string> => {
  const e = c.escena;
  if (e.pasos) return new Set((e.pasos as any[]).flatMap((p) => [...enPantalla({...c, escena: p})]).concat([...enPantalla({...c, escena: {tipo: 'x'}})]));
  const txt: string[] = [...(e.palabras || []).map((x: any) => x.txt), e.nombre, e.rol, ...(e.piezas || []).map((x: any) => x.nombre), c.kicker, c.titulo.replace(/\*/g, ''), e.sub, e.meta, e.pregunta, e.texto, e.txt, e.txt2, e.lbl, e.lbl2, e.pre, e.titular, e.url, e.etiqueta, e.diana,
    ...(e.items || []).map((x: any) => `${x.texto || ''} ${x.lbl || ''}`), ...(e.nodos || []).map((x: any) => `${x.t} ${x.s}`), ...(e.lineas || []), ...(e.cortes || []).map((x: any) => x.texto)];
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
      return SUBS_SIEMPRE || !llenas.length || llenas.filter((w) => vistas.has(w)).length / llenas.length < 0.6;});
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
    <FondoCap />
    <Portada />
    <Titular />
    <Ventana />
    <Cierre />
    <Sequence from={Math.round((fin(0) - 0.3) * 30)} durationInFrames={24}><Luz seed={3} /></Sequence>
    <Sequence from={Math.round((inicio(CAPS.length - 1) + 0.2) * 30)} durationInFrames={24}><Luz seed={8} /></Sequence>
    <Subtitulos />
    <AvisoVoz />
    <Audio src={staticFile('narrado/voz.wav')} />
    <Audio src={staticFile('narrado/musica.wav')} volume={volMusica} />
    <Audio src={staticFile('narrado/sfx.wav')} volume={0.6} />
  </AbsoluteFill>
);
