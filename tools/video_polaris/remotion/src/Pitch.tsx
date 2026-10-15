// Vídeo PITCH con la marca de otro (15-oct-2026, primero: ClinicOmics para inversores). Lee el mismo
// public/narrado/timeline.json que Narrado (narrado.py), pero no lleva nada de la marca de Polaris: ni estrellas,
// ni coral, ni serif. Colores y logo salen de T.tema (el guion), así un tercero no hereda nuestra identidad.
// Tipos de escena: portada, golpes (cifras con fuente), diagrama (cadena), cita, lista, cierre.
// Cada elemento entra cuando la voz dice su palabra; los subtítulos no repiten lo que ya está escrito en grande.
import React from 'react';
import {AbsoluteFill, Audio, Easing, Img, spring, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import {loadFont as fMont} from '@remotion/google-fonts/Montserrat';
import {loadFont as fOpen} from '@remotion/google-fonts/OpenSans';
import {loadFont as fMono} from '@remotion/google-fonts/JetBrainsMono';
import T from '../public/narrado/timeline.json';

const {fontFamily: DISPLAY} = fMont('normal', {weights: ['700', '800'], subsets: ['latin', 'latin-ext']});
const {fontFamily: BODY} = fOpen('normal', {weights: ['400', '600'], subsets: ['latin', 'latin-ext']});
const {fontFamily: MONO} = fMono('normal', {weights: ['400', '500'], subsets: ['latin', 'latin-ext']});

const TEMA_BASE = {fondo: '#0B1B3A', fondo2: '#07122A', texto: '#FFFFFF', suave: '#C9D6EE', azul: '#1622F7',
  azul_sobre_navy: '#8A90FF', cian: '#01C4FF', menta: '#61D69C', logo: ''};
const C = {...TEMA_BASE, ...((T as any).tema || {})} as typeof TEMA_BASE;

type Palabra = {w: string; a: number; b: number};
type Frase = {t0: number; t1: number; texto: string; palabras: Palabra[]};
type Cap = {id: string; kicker?: string; titulo?: string; escena: any; t0: number; t1: number; frases: Frase[]};
const CAPS = T.capitulos as Cap[];
const cl = (x: number, a = 0, b = 1) => Math.min(b, Math.max(a, x));
const lin = (t: number, a: number, b: number) => cl((t - a) / (b - a));
const suave = (x: number) => 1 - Math.pow(1 - x, 3);
const useT = () => {const f = useCurrentFrame(); const {fps} = useVideoConfig(); return f / fps;};
// un capítulo entra justo antes de su voz, pero nunca mientras aún suena la última frase del anterior
// (con la voz enlazada no hay hueco entre capítulos: antes se comía «five tumor types»)
const inicio = (i: number) => (i === 0 ? 0 : Math.min(CAPS[i].t0 + 0.15, Math.max(CAPS[i].t0 - 0.45, CAPS[i - 1].t1 + 0.12)));
const fin = (i: number) => (i + 1 < CAPS.length ? inicio(i + 1) : T.total + 1);
const norm = (s: string) => s.toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/[^a-z0-9 ]/g, ' ').trim();
const dice = (c: Cap, pref?: string) => {
  if (!pref) return c.t0;
  for (const f of c.frases) for (const w of f.palabras) if (norm(w.w).startsWith(norm(pref))) return w.a;
  return c.t0;
};
// vocabulario de movimiento (comité de diseño, 15-oct): entrada expo-out, salida acelerando, sin rebotes
const EASE_IN = Easing.bezier(0.16, 1, 0.3, 1);
const ease = (t: number, a: number, d: number) => EASE_IN(lin(t, a, a + d));
// entra con subida suave a los t segundos; sale al final de su capítulo
const entra = (t: number, a: number) => {const x = ease(t, a, 0.6); return {opacity: x, transform: `translateY(${28 * (1 - x)}px)`};};
// descubre de izquierda a derecha (máscara), para cifras y líneas: nunca un valor intermedio en pantalla
const mascara = (t: number, a: number, d = 0.7) => {const x = ease(t, a, d); return {clipPath: `inset(-20% ${100 - 100 * x}% -20% -2%)`};};
// texto con *acento*
const Acento: React.FC<{s: string}> = ({s}) => (
  <>{s.split(/(\*[^*]+\*)/).map((p, k) => p.startsWith('*') ? <span key={k} style={{color: C.azul_sobre_navy}}><Tm s={p.slice(1, -1)} /></span> : <span key={k}><Tm s={p} /></span>)}</>
);

// ™ siempre en superíndice a la derecha (regla de marca de ClinicOmics, 15-oct-2026)
const Tm: React.FC<{s: string}> = ({s}) => (
  <>{s.split('™').map((p, k, a) => <React.Fragment key={k}>{p.split(/(\S+-\S+)/).map((q, j) => j % 2 ? <span key={j} style={{whiteSpace: 'nowrap'}}>{q}</span> : q)}{k < a.length - 1 ? <sup style={{fontSize: '0.42em', verticalAlign: 'super', marginLeft: '0.04em'}}>™</sup> : null}</React.Fragment>)}</>
);

// ---------- fondo: navy con un halo azul lento y las dos hebras de la hélice ----------
const Fondo: React.FC = () => {
  const t = useT();
  // el halo cambia de sitio con cada capítulo, como si la cámara se moviera (1,2 s, ease in-out)
  const POS = [[1500, 260], [1550, 300], [380, 260], [1450, 820], [500, 820], [1500, 300], [960, 540]];
  const i = Math.max(0, CAPS.findIndex((_, k) => t >= inicio(k) && t < fin(k)));
  const p0 = POS[Math.max(0, i - 1) % POS.length], p1 = POS[i % POS.length];
  const m = Easing.inOut(Easing.cubic)(lin(t, inicio(i) - 0.2, inicio(i) + 1.0));
  const x = p0[0] + (p1[0] - p0[0]) * m + 60 * Math.sin(t * 0.12), y = p0[1] + (p1[1] - p0[1]) * m + 40 * Math.cos(t * 0.09);
  // la hélice se enciende con la portada (0,18 → 0,5 en 1,2 s) y vuelve a reposo hacia el segundo 8
  const brillo = 0.18 + 0.32 * ease(t, 0.1, 1.2) * (1 - lin(t, 4, 8));
  const amp = 38 * (1 + ease(t, 0.1, 1.2) * (1 - lin(t, 4, 8)));
  return (
    <AbsoluteFill style={{background: `radial-gradient(900px 700px at ${x}px ${y}px, rgba(22,34,247,.42), rgba(22,34,247,0) 70%), linear-gradient(160deg, ${C.fondo} 0%, ${C.fondo2} 100%)`}}>
      <svg width={1920} height={1080} style={{position: 'absolute', inset: 0, opacity: brillo}}>
        {[0, 1].map((k) => {
          // parallax: con cada paneo de cámara (PANEO px) la hélice corre la mitad, el halo ya se mueve por su cuenta
          const desp = 130 * (Math.max(0, i - 1) + (i > 0 ? m : 0));
          // el desplazamiento se reduce a un múltiplo exacto de la longitud de onda: sin saltos
          const onda = (2 * Math.PI / 0.32) * 34;
          const pts = Array.from({length: 90}, (_, q) => {const xx = q * 34 - 400 - (desp % onda); const yy = 980 + amp * Math.sin((q * 34 - 400) * (0.32 / 34) + t * 0.6 + k * Math.PI); return `${xx},${yy}`;}).join(' ');
          return <polyline key={k} points={pts} fill="none" stroke={k ? C.menta : C.cian} strokeWidth={3} strokeLinecap="round" />;
        })}
      </svg>
    </AbsoluteFill>
  );
};

// ---------- cabecera de cada capítulo de contenido ----------
const Cabecera: React.FC<{c: Cap; t: number}> = ({c, t}) => (
  <div style={{position: 'absolute', left: 140, top: 110, right: 480}}>
    {c.kicker ? <div style={{fontFamily: MONO, fontSize: 30, letterSpacing: '0.16em', textTransform: 'uppercase', color: C.cian, ...entra(t, inicio(CAPS.indexOf(c)) + 0.1)}}>{c.kicker}</div> : null}
    {c.kicker ? <div style={{marginTop: 12, height: 3, borderRadius: 2, background: C.cian, width: 90 * ease(t, inicio(CAPS.indexOf(c)) + 0.25, 0.5)}} /> : null}
    {c.titulo ? <div style={{marginTop: 14, fontFamily: DISPLAY, fontWeight: 800, fontSize: 80, lineHeight: 1.05, letterSpacing: '-0.01em', color: C.texto}}>
      <Cinetico s={c.titulo} t={t} a={inicio(CAPS.indexOf(c)) + 0.2} /></div> : null}
  </div>
);

// tipografía cinética: el titular entra palabra a palabra (50 ms entre palabras, 0,5 s cada una), respetando *acento* y ™
const Cinetico: React.FC<{s: string; t: number; a: number}> = ({s, t, a}) => {
  const palabras: {w: string; acento: boolean}[] = [];
  s.split(/(\*[^*]+\*)/).forEach((p) => {
    const ac = p.startsWith('*');
    (ac ? p.slice(1, -1) : p).split(/\s+/).filter(Boolean).forEach((w) => palabras.push({w, acento: ac}));
  });
  return (
    <>{palabras.map((p, k) => {
      const x = ease(t, a + 0.05 * k, 0.5);
      return <span key={k} style={{display: 'inline-block', marginRight: '0.26em', opacity: x, transform: `translateY(${22 * (1 - x)}px)`,
        color: p.acento ? C.azul_sobre_navy : undefined}}><Tm s={p.w} /></span>;
    })}</>
  );
};

// ---------- escenas ----------
type EP = {c: Cap; t: number};
const Logo: React.FC<{w: number; style?: React.CSSProperties}> = ({w, style}) =>
  C.logo ? <Img src={staticFile(C.logo)} style={{width: w, height: 'auto', display: 'block', ...style}} /> : null;

const EPortada: React.FC<EP> = ({c, t}) => {
  const deriva = 1 + 0.03 * lin(t, 0, fin(0));  // la escena se acerca despacio mientras se habla
  return (
    <AbsoluteFill style={{justifyContent: 'center', paddingLeft: 160, gap: 34, transform: `scale(${deriva})`, transformOrigin: '20% 50%'}}>
      <div style={{opacity: ease(t, 0.15, 1.4), transform: `scale(${0.96 + 0.04 * ease(t, 0.15, 1.4)})`, transformOrigin: 'left center'}}><Logo w={1000} /></div>
      <div style={{fontFamily: MONO, fontSize: 40, letterSpacing: '0.16em', textTransform: 'uppercase', color: C.cian, ...mascara(t, 0.9, 0.8)}}>{c.escena.sub}</div>
    </AbsoluteFill>
  );
};

// el héroe: el 7× cuenta de 1 a 7 y aterriza en la palabra «seven»; el resto se descubre con máscara (sin cifras intermedias)
const EGolpes: React.FC<EP> = ({c, t}) => {
  const ps = c.escena.palabras as {txt: string; en: string; lbl?: string; src?: string; acento?: boolean}[];
  const {fps} = useVideoConfig(); const f = useCurrentFrame();
  const heroe = ps.find((p) => p.acento);
  const tH = heroe ? dice(c, heroe.en) : 1e9;
  return (
    <div style={{position: 'absolute', left: 140, right: 140, top: 380, display: 'flex', gap: 120, alignItems: 'flex-start'}}>
      {ps.map((p, k) => {
        const a = Math.max(dice(c, p.en) - 0.1, inicio(CAPS.indexOf(c)) + 0.55);  // nunca antes que su titular
        if (p.acento) {
          // sin contador: un fotograma con «6×» sería un dato falso en pantalla (regla de {{TITULAR}}). Máscara + escala que se asienta
          const asienta = spring({frame: f - Math.round((tH - 0.25) * fps), fps, config: {damping: 14, mass: 0.6, stiffness: 160}});
          return (
            <div key={k} style={{flex: 0.65, minWidth: 0}}>
              <div style={{fontFamily: DISPLAY, fontWeight: 800, fontSize: 260, lineHeight: 0.95, letterSpacing: '-0.03em', color: C.azul_sobre_navy,
                transform: `scale(${0.82 + 0.18 * asienta})`, transformOrigin: 'left bottom', whiteSpace: 'nowrap', ...mascara(t, tH - 0.25, 0.45)}}>
                {p.txt}
              </div>
              {p.lbl ? <div style={{marginTop: 18, fontFamily: BODY, fontWeight: 600, fontSize: 44, lineHeight: 1.25, color: C.texto, ...entra(t, tH + 0.2)}}>{p.lbl}</div> : null}
              {p.src ? <div style={{marginTop: 16, fontFamily: MONO, fontSize: 30, color: C.suave, ...entra(t, tH + 0.4)}}>{p.src}</div> : null}
            </div>
          );
        }
        const atenua = 1 - 0.5 * ease(t, tH - 0.6, 0.6);  // cede el protagonismo al héroe
        return (
          <div key={k} style={{flex: 1.35, minWidth: 0, opacity: atenua}}>
            <div style={{fontFamily: DISPLAY, fontWeight: 800, fontSize: 190, whiteSpace: 'nowrap', lineHeight: 1, letterSpacing: '-0.03em', color: C.texto, fontVariantNumeric: 'tabular-nums', paddingTop: 50, ...mascara(t, a)}}>{p.txt}</div>
            {p.lbl ? <div style={{marginTop: 18, fontFamily: BODY, fontWeight: 600, fontSize: 44, lineHeight: 1.25, color: C.texto, maxWidth: 1000, ...entra(t, a + 0.3)}}>{p.lbl}</div> : null}
            {p.src ? <div style={{marginTop: 16, fontFamily: MONO, fontSize: 30, color: C.suave, ...entra(t, a + 0.5)}}>{p.src}</div> : null}
          </div>
        );
      })}
    </div>
  );
};

const EDiagrama: React.FC<EP> = ({c, t}) => {
  const ns = c.escena.nodos as {t: string; s: string; en: string; puerta?: boolean}[];
  // en orden: un nodo nunca entra antes que el de su izquierda (la palabra «tissue» sonaba después de «chromatin»)
  const ts = ns.map((n) => dice(c, n.en)).reduce((acc: number[], x, k) => [...acc, k ? Math.max(x, acc[k - 1] + 0.45) : Math.min(x, c.t0 + 0.3)], []);
  // conector que se dibuja de nodo en nodo; un punto de luz menta va en su frente y cada nodo se enciende al llegar
  const N = ns.length;
  const tramo = ns.slice(1).reduce((acc, _, k) => acc + ease(t, ts[k + 1] - 0.45, 0.45), 0);  // 0 … N-1
  const frente = (100 / N) * (0.5 + tramo);
  const GAP = 44;
  return (
    <div style={{position: 'absolute', left: 140, right: 140, top: 470}}>
      <div style={{position: 'absolute', left: `${50 / N}%`, top: '50%', height: 3, width: `${frente - 50 / N}%`, background: C.cian, borderRadius: 2,
        opacity: ease(t, ts[0], 0.4)}} />
      {tramo > 0.02 && tramo < N - 1.02 ? <div style={{position: 'absolute', left: `${frente}%`, top: '50%', width: 16, height: 16, marginLeft: -8, marginTop: -6.5,
        borderRadius: 8, background: C.menta, boxShadow: `0 0 24px ${C.menta}`}} /> : null}
      <div style={{display: 'flex', alignItems: 'stretch', gap: GAP}}>
        {ns.map((n, k) => {
          const vivo = ease(t, ts[k] - 0.05, 0.25);
          return (
            <div key={k} style={{flex: 1, minWidth: 0, borderRadius: 26, padding: '30px 26px', background: n.puerta ? C.azul : C.fondo,
              border: `3px solid ${n.puerta ? C.azul_sobre_navy : vivo > 0.5 ? C.cian : 'rgba(255,255,255,.28)'}`,
              opacity: ease(t, ts[k] - 0.15, 0.5), transform: `scale(${0.94 + 0.06 * ease(t, ts[k] - 0.15, 0.6)})`}}>
              <div style={{fontFamily: DISPLAY, fontWeight: 800, fontSize: 44, lineHeight: 1.1, color: C.texto}}>{n.t}</div>
              <div style={{marginTop: 12, fontFamily: BODY, fontSize: 32, lineHeight: 1.3, color: n.puerta ? '#FFFFFF' : C.suave}}>{n.s}</div>
            </div>
          );
        })}
      </div>
    </div>
  );
};

const ECita: React.FC<EP> = ({c, t}) => (
  <div style={{position: 'absolute', left: 140, right: 240, top: 430}}>
    <div style={{fontFamily: MONO, fontSize: 34, letterSpacing: '0.14em', textTransform: 'uppercase', color: C.cian, ...entra(t, c.t0)}}>{c.escena.etiqueta}</div>
    <div style={{marginTop: 26, fontFamily: DISPLAY, fontWeight: 800, fontSize: 104, lineHeight: 1.08, letterSpacing: '-0.015em', color: C.texto, ...entra(t, c.t0 + 0.6)}}>
      <Acento s={c.escena.texto} />
    </div>
    {/* cifras de apoyo que dice la voz (p. ej. «six programs across five tumor types»): entran con máscara al decirlas */}
    {(c.escena.cifras || []).length ? <div style={{marginTop: 56, display: 'flex', gap: 80}}>
      {(c.escena.cifras as {n: string; lbl: string; en: string}[]).map((x, k) => (
        <div key={k} style={{display: 'flex', alignItems: 'baseline', gap: 18}}>
          <div style={{fontFamily: DISPLAY, fontWeight: 800, fontSize: 120, lineHeight: 1, color: C.texto, ...mascara(t, dice(c, x.en) - 0.1 + 0.25 * k, 0.5)}}>{x.n}</div>
          <div style={{fontFamily: BODY, fontWeight: 600, fontSize: 40, color: C.texto, ...entra(t, dice(c, x.en) + 0.15 + 0.25 * k)}}>{x.lbl}</div>
        </div>
      ))}
    </div> : null}
  </div>
);

const ELista: React.FC<EP> = ({c, t}) => {
  const its = c.escena.items as {texto: string; en: string}[];
  return (
    <div style={{position: 'absolute', left: 140, right: 140, top: 420, display: 'grid', gap: 22}}>
      {its.map((it, k) => {
        const [a, b] = it.texto.split(' · ');
        return (
          <div key={k} style={{display: 'flex', alignItems: 'baseline', gap: 28, paddingBottom: 20, borderBottom: '2px solid rgba(255,255,255,.12)',
            opacity: ease(t, dice(c, it.en) - 0.1, 0.5), transform: `translateX(${-40 * (1 - ease(t, dice(c, it.en) - 0.1, 0.6))}px)`}}>
            <div style={{width: 14, height: 14, borderRadius: 7, background: C.menta, flex: 'none', transform: `translateY(-10px) scale(${0.3 + 0.7 * ease(t, dice(c, it.en), 0.4)})`}} />
            <div style={{fontFamily: DISPLAY, fontWeight: 800, fontSize: 62, color: C.texto, whiteSpace: 'nowrap'}}>{a}</div>
            {b ? <div style={{fontFamily: BODY, fontSize: 40, color: C.suave}}>{b}</div> : null}
          </div>
        );
      })}
    </div>
  );
};

// equipo: los cuatro aros se dibujan al entrar (no hay hueco vacío) y cada persona se enciende cuando la voz la nombra
const EEquipo: React.FC<EP> = ({c, t}) => {
  const its = c.escena.items as {texto: string; en: string; foto?: string}[];
  const a0 = inicio(CAPS.indexOf(c)) + 0.6;  // los aros y fotos llegan después del titular
  const R = 110, L = 2 * Math.PI * R;
  return (
    <div style={{position: 'absolute', left: 140, right: 140, top: 400, display: 'grid', gridTemplateColumns: `repeat(${its.length}, 1fr)`, gap: 40}}>
      {its.map((it, k) => {
        const [nombre, rol] = it.texto.split(' · ');
        const ini = nombre.split(' ').map((w) => w[0]).join('').slice(0, 2);
        const aro = ease(t, a0 + 0.15 * k, 0.6), on = ease(t, dice(c, it.en) - 0.15, 0.4);
        return (
          <div key={k} style={{display: 'grid', justifyItems: 'start', gap: 22}}>
            <div style={{position: 'relative', width: 2 * R + 8, height: 2 * R + 8}}>
              {it.foto
                // su foto (del deck de inversores): en gris y tenue hasta que la voz la nombra, entonces a color
                ? <div style={{position: 'absolute', left: 8, top: 8, width: 2 * R - 8, height: 2 * R - 8, borderRadius: '50%', overflow: 'hidden',
                    opacity: (0.35 + 0.65 * Math.max(aro * 0.6, on)) * aro, transform: `scale(${0.96 + 0.04 * on})`}}>
                    {/* zoom por foto: recorta fondos con ruido (la de Bellmunt trae persianas detrás) */}
                    <Img src={staticFile(it.foto)} style={{width: '100%', height: '100%', objectFit: 'cover', filter: `grayscale(${1 - on})`,
                      transform: `scale(${(it as any).zoom || 1})`, transformOrigin: (it as any).foco || '50% 40%'}} />
                  </div>
                : <div style={{position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: DISPLAY, fontWeight: 800,
                    fontSize: 64, color: C.texto, opacity: 0.25 + 0.75 * on}}>{ini}</div>}
              <svg width={2 * R + 8} height={2 * R + 8} style={{position: 'absolute', inset: 0}}>
                <circle cx={R + 4} cy={R + 4} r={R} fill="none" stroke={on > 0.5 ? C.azul_sobre_navy : 'rgba(255,255,255,.35)'} strokeWidth={4}
                  strokeDasharray={L} strokeDashoffset={L * (1 - aro)} transform={`rotate(-90 ${R + 4} ${R + 4})`} />
              </svg>
            </div>
            <div style={{opacity: (0.45 + 0.55 * on) * aro}}>
              <div style={{fontFamily: DISPLAY, fontWeight: 800, fontSize: 38, lineHeight: 1.1, color: C.texto, whiteSpace: 'nowrap'}}>{nombre}</div>
              {rol ? <div style={{marginTop: 8, fontFamily: BODY, fontSize: 32, color: C.suave}}>{rol}</div> : null}
            </div>
          </div>
        );
      })}
    </div>
  );
};

const ECierre: React.FC<EP> = ({c, t}) => {
  const a = inicio(CAPS.indexOf(c));
  const ultima = c.frases[c.frases.length - 1].t0;  // «ClinicOmics. The chromatin biomarker company.»
  return (
    <AbsoluteFill style={{justifyContent: 'center', paddingLeft: 160, paddingRight: 160, gap: 30}}>
      <div style={entra(t, a + 0.2)}><Logo w={620} /></div>
      {c.escena.ask ? <div style={{position: 'absolute', left: 160, right: 160, top: 560, opacity: entra(t, a + 0.5).opacity * (1 - lin(t, ultima - 0.4, ultima))}}>
        <div style={{fontFamily: MONO, fontSize: 32, letterSpacing: '0.14em', textTransform: 'uppercase', color: C.cian}}>{c.escena.pie}</div>
        <div style={{marginTop: 18, fontFamily: DISPLAY, fontWeight: 800, fontSize: 76, lineHeight: 1.1, color: C.texto}}><Acento s={c.escena.ask} /></div>
      </div> : null}
      <div style={{fontFamily: DISPLAY, fontWeight: 800, fontSize: 104, lineHeight: 1.05, letterSpacing: '-0.015em', color: C.texto, ...entra(t, ultima)}}>{c.escena.titular}</div>
      <div style={{display: 'flex', gap: 40, alignItems: 'baseline', ...entra(t, ultima + 0.5)}}>
        {c.escena.pie ? <div style={{fontFamily: MONO, fontSize: 36, letterSpacing: '0.14em', textTransform: 'uppercase', color: C.cian}}>{c.escena.pie}</div> : null}
        {c.escena.url ? <div style={{fontFamily: MONO, fontSize: 40, color: C.suave}}>{c.escena.url}
          <div style={{marginTop: 6, height: 3, borderRadius: 2, background: C.cian, width: `${100 * ease(t, ultima + 0.9, 0.6)}%`}} /></div> : null}
      </div>
    </AbsoluteFill>
  );
};

const ESCENAS: Record<string, React.FC<EP>> = {portada: EPortada, golpes: EGolpes, diagrama: EDiagrama, cita: ECita, lista: ELista, equipo: EEquipo, cierre: ECierre};
const SIN_CABECERA = ['portada', 'cierre'];

// cámara: cada capítulo llega desde la derecha y se va hacia la izquierda, como un paneo sobre un lienzo continuo.
// La salida termina ANTES de que entre el siguiente (sin solape de texto); el halo y la hélice se mueven con parallax (Fondo)
const PANEO = 260;
const Capitulos: React.FC = () => {
  const t = useT();
  return (
    <>
      {CAPS.map((c, i) => {
        if (t < inicio(i) - 0.1 || t > fin(i)) return null;
        const sale = i + 1 < CAPS.length ? Easing.bezier(0.4, 0, 1, 1)(lin(t, fin(i) - 0.35, fin(i) - 0.05)) : 0;
        const llega = i === 0 ? 1 : ease(t, inicio(i) - 0.05, 0.9);
        const x = PANEO * (1 - llega) - PANEO * sale;
        const E = ESCENAS[c.escena.tipo];
        return (
          <AbsoluteFill key={c.id} style={{opacity: 1 - sale, transform: `translateX(${x}px)`}}>
            {SIN_CABECERA.includes(c.escena.tipo) ? null : <Cabecera c={c} t={t} />}
            {E ? <E c={c} t={t} /> : null}
          </AbsoluteFill>
        );
      })}
    </>
  );
};

// el logo de la esquina vive mientras dura el contenido (no parpadea en cada cambio de capítulo)
const LogoEsquina: React.FC = () => {
  const t = useT();
  const ia = CAPS.findIndex((c) => !SIN_CABECERA.includes(c.escena.tipo));
  const ib = CAPS.length - 1 - [...CAPS].reverse().findIndex((c) => !SIN_CABECERA.includes(c.escena.tipo));
  const o = ease(t, inicio(ia) + 0.2, 0.5) * (1 - lin(t, fin(ib) - 0.35, fin(ib) - 0.05));
  return <div style={{position: 'absolute', right: 140, top: 124, opacity: 0.9 * o}}><Logo w={260} /></div>;
};

// raíl de progreso: una marca por capítulo de contenido; la activa se llena
const Rail: React.FC = () => {
  const t = useT();
  const idx = CAPS.map((c, i) => i).filter((i) => !SIN_CABECERA.includes(CAPS[i].escena.tipo));
  const o = ease(t, inicio(idx[0]) + 0.2, 0.5) * (1 - lin(t, fin(idx[idx.length - 1]) - 0.35, fin(idx[idx.length - 1]) - 0.05));
  return (
    <div style={{position: 'absolute', left: 140, right: 140, bottom: 28, display: 'flex', gap: 10, opacity: o}}>
      {idx.map((i) => {
        const lleno = lin(t, inicio(i), inicio(i) + 0.4) > 0 ? ease(t, inicio(i), 0.4) : 0;
        const activo = t >= inicio(i) && t < fin(i);
        return <div key={i} style={{flex: 1, height: 4, borderRadius: 2, background: 'rgba(255,255,255,.14)', overflow: 'hidden'}}>
          <div style={{height: '100%', width: `${100 * lleno}%`, background: activo ? C.cian : 'rgba(1,196,255,.45)'}} /></div>;
      })}
    </div>
  );
};

// ---------- subtítulos: la frase que suena, salvo en portada/cierre (lo dicho ya está escrito en grande) ----------
const Subtitulos: React.FC = () => {
  const t = useT();
  for (const c of CAPS) {
    if (['portada', 'cierre', 'golpes', 'cita', 'lista', 'equipo'].includes(c.escena.tipo)) continue;  // ya escrito en grande
    for (const f of c.frases) {
      if (t < f.t0 - 0.05 || t > f.t1 + 0.25) continue;
      const o = lin(t, f.t0 - 0.05, f.t0 + 0.15) * (1 - lin(t, f.t1 + 0.05, f.t1 + 0.25));
      return (
        <div style={{position: 'absolute', left: 0, right: 0, bottom: 64, display: 'flex', justifyContent: 'center', opacity: o}}>
          <div style={{maxWidth: 1500, padding: '14px 26px', borderRadius: 14, background: 'rgba(7,18,42,.82)', fontFamily: BODY, fontWeight: 600,
            fontSize: 36, lineHeight: 1.35, color: C.texto, textAlign: 'center'}}>
            {f.palabras.map((w, k) => <span key={k} style={{color: t >= w.a ? C.texto : C.suave}}>{w.w} </span>)}
          </div>
        </div>
      );
    }
  }
  return null;
};

const hablando = (t: number) => CAPS.some((c) => c.frases.some((f) => t >= f.t0 && t <= f.t1));
const volMusica = (f: number) => {const t = f / 30; let v = 0; for (let k = -8; k <= 8; k++) v += hablando(t + k * 0.03) ? 1 : 0; return 0.38 - 0.28 * (v / 17);};

export const Pitch: React.FC = () => (
  <AbsoluteFill style={{background: C.fondo}}>
    <Fondo />
    <Capitulos />
    <LogoEsquina />
    <Rail />
    <Subtitulos />
    <Audio src={staticFile('narrado/voz.wav')} />
    <Audio src={staticFile('narrado/musica.wav')} volume={volMusica} />
  </AbsoluteFill>
);
