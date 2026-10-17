// Vídeo PITCH con la marca de otro (15-oct-2026, primero: ClinicOmics para inversores). Lee el mismo
// public/narrado/timeline.json que Narrado (narrado.py), pero no lleva nada de la marca de Polaris: ni estrellas,
// ni coral, ni serif. Colores y logo salen de T.tema (el guion), así un tercero no hereda nuestra identidad.
// Tipos de escena: portada, golpes (cifras con fuente), diagrama (cadena), cita, lista, cierre.
// Cada elemento entra cuando la voz dice su palabra; los subtítulos no repiten lo que ya está escrito en grande.
import React from 'react';
import {AbsoluteFill, Audio, Easing, Img, OffthreadVideo, Sequence, spring, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import {loadFont as fMont} from '@remotion/google-fonts/Montserrat';
import {loadFont as fOpen} from '@remotion/google-fonts/OpenSans';
import {loadFont as fMono} from '@remotion/google-fonts/JetBrainsMono';
import T from '../public/narrado/timeline.json';

const {fontFamily: DISPLAY} = fMont('normal', {weights: ['300', '700', '800'], subsets: ['latin', 'latin-ext']});
const {fontFamily: BODY} = fOpen('normal', {weights: ['400', '600'], subsets: ['latin', 'latin-ext']});
const {fontFamily: MONO} = fMono('normal', {weights: ['400', '500'], subsets: ['latin', 'latin-ext']});

const TEMA_BASE = {fondo: '#0B1B3A', fondo2: '#07122A', texto: '#FFFFFF', suave: '#C9D6EE', azul: '#1622F7',
  azul_sobre_navy: '#8A90FF', cian: '#01C4FF', menta: '#61D69C', logo: ''};
const C = {...TEMA_BASE, ...((T as any).tema || {})} as typeof TEMA_BASE;
// escala tipográfica única (16-oct-2026): 17 tamaños sueltos de 32 a 260 px pasan a 9 con nombre, ~20 % más contenidos
// ({{TITULAR}}: «veo letras muy grandes»). En px a 1080p; el mínimo, 32, para que siga leyéndose en el móvil.
const TIPO = {heroe: 210, cifra: 150, numero: 100, display: 80, titulo: 64, url: 52, cuerpo: 40, subtitulo: 40, detalle: 34, mono: 34};

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
const entra = (t: number, a: number) => {const x = ease(t, a, 0.6); return {opacity: x, transform: `translateY(${12 * (1 - x)}px)`};};
// descubre de izquierda a derecha (máscara), para cifras y líneas: nunca un valor intermedio en pantalla
const mascara = (t: number, a: number, d = 0.7) => {const x = ease(t, a, d); return {clipPath: `inset(-20% ${100 - 100 * x}% -20% -2%)`};};
// texto con *acento*
const Acento: React.FC<{s: string; color?: string}> = ({s, color}) => (
  <>{s.split(/(\*[^*]+\*)/).map((p, k) => p.startsWith('*') ? <span key={k} style={{color: color || C.azul_sobre_navy}}><Tm s={p.slice(1, -1)} /></span> : <span key={k}><Tm s={p} /></span>)}</>
);

// ™ siempre en superíndice a la derecha (regla de marca de ClinicOmics, 15-oct-2026)
const Tm: React.FC<{s: string}> = ({s}) => (
  <>{s.split('™').map((p, k, a) => <React.Fragment key={k}>{p.split(/(\S+-\S+)/).map((q, j) => j % 2 ? <span key={j} style={{whiteSpace: 'nowrap'}}>{q}</span> : q)}{k < a.length - 1 ? <sup style={{fontSize: '0.42em', verticalAlign: 'super', marginLeft: '0.04em'}}>™</sup> : null}</React.Fragment>)}</>
);

// ---------- fondo: navy plano con grano (diseño, 16-oct-2026: el halo azul en todas las escenas delataba plantilla) ----------
const Fondo: React.FC = () => (
  <AbsoluteFill style={{background: `linear-gradient(160deg, ${C.fondo} 0%, ${C.fondo2} 100%)`}}>
    <svg width={1920} height={1080} style={{position: 'absolute', inset: 0, opacity: 0.07, mixBlendMode: 'overlay'}}>
      <filter id="grano"><feTurbulence type="fractalNoise" baseFrequency="0.9" numOctaves={2} stitchTiles="stitch" /><feColorMatrix type="saturate" values="0" /></filter>
      <rect width="100%" height="100%" filter="url(#grano)" />
    </svg>
  </AbsoluteFill>
);

// ---------- el hilo de cromatina: la idea gráfica propia de ClinicOmics (diseño, 16-oct-2026) ----------
// Un único trazo con «cuentas» (nucleosomas) que recorre todo el vídeo y cambia de forma con cada capítulo: onda en la
// portada, conector que pasa por los cinco pasos de ChromaLogic, columna de la lista, hilo que une a los cuatro fundadores.
// Es esquemático: sin escala ni valores, no representa ningún dato.
const NP = 160;
const P0 = 304, PASO = 328;  // centros de los cinco pasos del diagrama (x) — los usa también EDiagrama
const Y_DIAG = 470;
const R_EQ = 128, P0_EQ = 140 + R_EQ + 4, PASO_EQ = 420, Y_EQ = 400 + R_EQ + 4;  // centros de los retratos
const onda = (y0: number, amp: number, per: number, fase: number) => (u: number): [number, number] => {
  const x = -80 + u * 2080; return [x, y0 + amp * Math.sin((2 * Math.PI * (x - fase)) / per)];
};
// el hilo nunca cruza la franja de los subtítulos (y ≈ 920-1030): los tapaba ({{TITULAR}}, 16-oct-2026)
const FRANJA_SUB = 910;
const FORMA: Record<string, (u: number) => [number, number]> = {
  portada: onda(850, 26, 520, 0),
  golpes: onda(820, 12, 640, 200),
  diagrama: onda(Y_DIAG, 34, PASO, P0),            // pasa EXACTAMENTE por el centro de cada paso (seno nulo en los nodos)
  cita: onda(330, 10, 700, 120),
  lista: (u) => [96 + 10 * Math.sin(u * 9), -60 + u * 1200],  // baja por la izquierda de la lista
  equipo: onda(Y_EQ, 40, PASO_EQ, P0_EQ),          // une los cuatro retratos por su centro
  cierre: onda(850, 8, 900, 0),
};
const forma = (c: Cap) => FORMA[c.escena.tipo] || FORMA.golpes;
const Hilo: React.FC = () => {
  const t = useT();
  const i = Math.max(0, CAPS.findIndex((_, k) => t >= inicio(k) && t < fin(k)));
  const m = i === 0 ? 1 : Easing.inOut(Easing.cubic)(lin(t, inicio(i) - 0.25, inicio(i) + 0.85));
  const fa = forma(CAPS[Math.max(0, i - 1)]), fb = forma(CAPS[i]);
  const vivo = 0.6 * t;  // el hilo respira: una ola lenta que lo recorre
  const pts = Array.from({length: NP}, (_, q) => {
    const u = q / (NP - 1); const [xa, ya] = fa(u), [xb, yb] = fb(u);
    return [xa + (xb - xa) * m, ya + (yb - ya) * m + 3 * Math.sin(u * 14 - vivo)] as [number, number];
  });
  const d = 'M' + pts.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' L');
  // se dibuja al empezar y se recoge al final (entra de izquierda a derecha con el logo; sale antes del fundido)
  const dibujado = ease(t, 0.1, 1.6) * (1 - lin(t, T.total - 3.2, T.total - 1.4));
  const cuentas = pts.filter((_, q) => q % 9 === 4 && q / NP < dibujado);
  return (
    <svg width={1920} height={1080} style={{position: 'absolute', inset: 0}}>
      <path d={d} fill="none" stroke={C.cian} strokeOpacity={0.55} strokeWidth={3} strokeLinecap="round" pathLength={1}
        strokeDasharray="1 1" strokeDashoffset={1 - dibujado} />
      {cuentas.map(([x, y], k) => <circle key={k} cx={x} cy={y} r={8} fill={C.fondo} stroke={C.cian} strokeOpacity={0.7} strokeWidth={2.5} />)}
    </svg>
  );
};

// ---------- cabecera editorial: índice fino + titular con contraste de peso, que se descubre con máscara ----------
// (diseño, 16-oct-2026: el kicker mono en mayúsculas con raya, repetido igual en cinco capítulos, delataba plantilla)
const IDX = CAPS.filter((c) => !['portada', 'cierre'].includes(c.escena.tipo)).map((c) => c.id);
const Cabecera: React.FC<{c: Cap; t: number}> = ({c, t}) => {
  const a = inicio(CAPS.indexOf(c)), n = IDX.indexOf(c.id) + 1;
  return (
    <div style={{position: 'absolute', left: 140, top: 104, right: 480}}>
      <div style={{display: 'flex', alignItems: 'baseline', gap: 22, ...entra(t, a - 0.02)}}>
        <div style={{fontFamily: DISPLAY, fontWeight: 300, fontSize: TIPO.titulo, lineHeight: 1, color: C.azul_sobre_navy, fontVariantNumeric: 'tabular-nums'}}>{String(n).padStart(2, '0')}</div>
        {c.kicker ? <div style={{fontFamily: BODY, fontWeight: 600, fontSize: TIPO.detalle, color: C.suave}}>{c.kicker}</div> : null}
      </div>
      {c.titulo ? <div style={{marginTop: 18, fontFamily: DISPLAY, fontSize: TIPO.titulo, lineHeight: 1.08, letterSpacing: '-0.01em', color: C.texto,
        textWrap: 'balance', ...mascara(t, a + 0.08, 0.8)} as React.CSSProperties}><Titular s={c.titulo} /></div> : null}
    </div>
  );
};
// lo normal en trazo ligero (300) y el *acento* en negrita lavanda: el contraste lo hace la tipografía, no la animación
const Titular: React.FC<{s: string}> = ({s}) => (
  <>{s.split(/(\*[^*]+\*)/).map((p, k) => p.startsWith('*')
    ? <span key={k} style={{fontWeight: 800, color: C.azul_sobre_navy}}><Tm s={p.slice(1, -1)} /></span>
    : <span key={k} style={{fontWeight: 300}}><Tm s={p} /></span>)}</>
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
      <div style={{fontFamily: MONO, fontSize: TIPO.detalle, letterSpacing: '0.16em', textTransform: 'uppercase', color: C.cian, ...mascara(t, 0.9, 0.8)}}>{c.escena.sub}</div>
      {/* el gancho que dice la voz, escrito (LinkedIn reproduce sin sonido; Sid, 16-oct-2026) */}
      {c.escena.gancho ? <div style={{marginTop: 40, fontFamily: DISPLAY, fontWeight: 800, fontSize: TIPO.display, lineHeight: 1.08, letterSpacing: '-0.01em', color: C.texto, maxWidth: 1500}}>
        <Cinetico s={c.escena.gancho} t={t} a={dice(c, c.escena.gancho_en) - 0.1} /></div> : null}
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
    <div style={{position: 'absolute', left: 140, right: 140, top: 320, display: 'flex', gap: 120, alignItems: 'flex-start'}}>
      {ps.map((p, k) => {
        const a = Math.max(dice(c, p.en) - 0.1, inicio(CAPS.indexOf(c)) + 0.55);  // nunca antes que su titular
        if (p.acento) {
          // sin contador: un fotograma con «6×» sería un dato falso en pantalla (regla de {{TITULAR}}). Máscara + escala que se asienta
          const asienta = spring({frame: f - Math.round((tH - 0.25) * fps), fps, config: {damping: 14, mass: 0.6, stiffness: 160}});
          return (
            <div key={k} style={{flex: 1, minWidth: 0, position: 'relative', padding: '44px 48px 40px', marginTop: -44}}>
              {/* un único bloque sólido en el vídeo, CLARO (como los paneles crema de datos de Recursion): entra con cortinilla antes de «seven».
                  Nunca relleno grande de #1622F7: a {{TITULAR}} le chirría (16-oct-2026, dos veces) */}
              <div style={{position: 'absolute', inset: 0, background: '#F4F7FD', borderRadius: 4, clipPath: `inset(0 ${100 - 100 * ease(t, tH - 0.55, 0.5)}% 0 0)`}} />
              <div style={{position: 'relative', fontFamily: DISPLAY, fontWeight: 800, fontSize: TIPO.heroe, lineHeight: 0.95, letterSpacing: '-0.03em', color: C.fondo,
                transform: `scale(${0.82 + 0.18 * asienta})`, transformOrigin: 'left bottom', whiteSpace: 'nowrap', ...mascara(t, tH - 0.25, 0.45)}}>
                {p.txt}
              </div>
              {p.lbl ? <div style={{position: 'relative', marginTop: 18, fontFamily: BODY, fontWeight: 600, fontSize: TIPO.cuerpo, lineHeight: 1.25, color: C.fondo, ...entra(t, tH + 0.2)}}>{p.lbl}</div> : null}
              {p.src ? <div style={{position: 'relative', marginTop: 16, fontFamily: MONO, fontSize: TIPO.detalle, color: '#3A4A66', whiteSpace: 'nowrap', ...entra(t, tH + 0.4)}}>{p.src}</div> : null}
            </div>
          );
        }
        const atenua = 1 - 0.3 * ease(t, tH - 0.6, 0.6);  // cede el protagonismo al héroe
        return (
          <div key={k} style={{flex: 1.35, minWidth: 0, opacity: atenua}}>
            <div style={{fontFamily: DISPLAY, fontWeight: 800, fontSize: TIPO.cifra, whiteSpace: 'nowrap', lineHeight: 1, letterSpacing: '-0.03em', color: C.texto, fontVariantNumeric: 'tabular-nums', paddingTop: 50, ...mascara(t, a)}}>{p.txt}</div>
            {p.lbl ? <div style={{marginTop: 18, fontFamily: BODY, fontWeight: 600, fontSize: TIPO.cuerpo, lineHeight: 1.25, color: C.texto, maxWidth: 1000, ...entra(t, a + 0.3)}}>{p.lbl}</div> : null}
            {p.src ? <div style={{marginTop: 16, fontFamily: MONO, fontSize: TIPO.detalle, color: C.suave, ...entra(t, a + 0.5)}}>{p.src}</div> : null}
          </div>
        );
      })}
    </div>
  );
};

// nodos sobre el hilo, sin cajas (diseño, 16-oct-2026: cinco tarjetas idénticas eran el rasgo que más delataba a la IA).
// El paso activo va grande; los anteriores se quedan más pequeños; los que faltan, en hueco tenue desde el principio.
const EDiagrama: React.FC<EP> = ({c, t}) => {
  const ns = c.escena.nodos as {t: string; s: string; en: string; puerta?: boolean; icono?: string}[];
  // en orden: un nodo nunca entra antes que el de su izquierda (la palabra «tissue» sonaba después de «chromatin»)
  const ts = ns.map((n) => dice(c, n.en)).reduce((acc: number[], x, k) => [...acc, k ? Math.max(x, acc[k - 1] + 0.45) : Math.min(x, c.t0 + 0.3)], []);
  const a0 = inicio(CAPS.indexOf(c));
  const L = 210;  // lado del icono activo
  return (
    <>
      {ns.map((n, k) => {
        const vivo = ease(t, ts[k] - 0.1, 0.45);
        const pasa = k + 1 < ns.length ? ease(t, ts[k + 1] - 0.1, 0.45) : 0;  // ya no es el activo
        const hueco = ease(t, a0 + 0.35 + 0.08 * k, 0.6);
        const esc = (0.62 + 0.38 * vivo) * (1 - 0.2 * pasa);
        const op = Math.max(0.22 * hueco, vivo * (1 - 0.15 * pasa));
        const x = P0 + PASO * k;
        const anillo = n.puerta ? C.azul_sobre_navy : C.cian;
        return (
          <div key={k} style={{position: 'absolute', left: x - 150, width: 300, top: Y_DIAG - L / 2, display: 'grid', justifyItems: 'center', opacity: op}}>
            <div style={{width: L, height: L, transform: `scale(${esc})`, borderRadius: 22, overflow: 'hidden',
              boxShadow: `0 0 0 ${3 + 2 * (vivo - pasa)}px ${anillo}, 0 0 ${n.puerta ? 70 * vivo : 0}px rgba(22,34,247,.8)`, background: C.fondo2}}>
              {/* icono del tercero (sus recortes): gris mientras espera, a color al nombrarlo */}
              {n.icono ? <Img src={staticFile(n.icono)} style={{width: '100%', height: '100%', objectFit: 'cover', display: 'block', filter: `grayscale(${1 - vivo})`}} /> : null}
            </div>
            <div style={{marginTop: 26, textAlign: 'center', fontFamily: DISPLAY, fontWeight: 800, fontSize: TIPO.cuerpo, lineHeight: 1.1, color: C.texto}}>{n.t}</div>
            <div style={{marginTop: 10, textAlign: 'center', fontFamily: BODY, fontSize: TIPO.detalle, lineHeight: 1.25, color: n.puerta ? C.texto : C.suave, opacity: 0.4 + 0.6 * vivo}}>{n.s}</div>
          </div>
        );
      })}
    </>
  );
};

const ECita: React.FC<EP> = ({c, t}) => (
  <div style={{position: 'absolute', left: 140, right: 240, top: 430}}>
    <div style={{fontFamily: BODY, fontWeight: 600, fontSize: TIPO.detalle, color: C.cian, ...entra(t, c.t0)}}>{c.escena.etiqueta}</div>
    <div style={{marginTop: 26, fontFamily: DISPLAY, fontWeight: 800, fontSize: TIPO.display, lineHeight: 1.08, letterSpacing: '-0.015em', color: C.texto, ...entra(t, c.t0 + 0.6)}}>
      <Acento s={c.escena.texto} />
    </div>
    {/* cifras de apoyo que dice la voz (p. ej. «six programs across five tumor types»): entran con máscara al decirlas */}
    {(c.escena.cifras || []).length ? <div style={{marginTop: 56, display: 'flex', gap: 90, whiteSpace: 'nowrap'}}>
      {(c.escena.cifras as {n: string; lbl: string; en: string}[]).map((x, k) => (
        <div key={k} style={{display: 'flex', alignItems: 'baseline', gap: 18}}>
          <div style={{fontFamily: DISPLAY, fontWeight: 800, fontSize: TIPO.numero, lineHeight: 1, color: C.texto, ...mascara(t, dice(c, x.en) - 0.1 + 0.25 * k, 0.5)}}>{x.n}</div>
          <div style={{fontFamily: BODY, fontWeight: 600, fontSize: TIPO.cuerpo, color: C.texto, ...entra(t, dice(c, x.en) + 0.15 + 0.25 * k)}}>{x.lbl}</div>
        </div>
      ))}
    </div> : null}
  </div>
);

const ELista: React.FC<EP> = ({c, t}) => {
  const its = c.escena.items as {texto: string; en: string}[];
  const a0 = inicio(CAPS.indexOf(c)) + 0.5;
  // las filas están desde el principio como hueco (borde tenue, número): la pantalla nunca queda medio vacía (diseño, 16-oct-2026)
  return (
    <div style={{position: 'absolute', left: 170, right: 140, top: 360, display: 'grid', gap: 40}}>
      {its.map((it, k) => {
        const [a, b] = it.texto.split(' · ');
        const hueco = ease(t, a0 + 0.12 * k, 0.5), on = ease(t, dice(c, it.en) - 0.1, 0.5);
        return (
          <div key={k} style={{display: 'flex', alignItems: 'baseline', gap: 34, paddingBottom: 28, borderBottom: `2px solid rgba(255,255,255,${0.06 + 0.1 * on})`, opacity: hueco}}>
            <div style={{fontFamily: MONO, fontWeight: 500, fontSize: TIPO.detalle, color: C.cian, flex: 'none', width: 70, opacity: 0.35 + 0.65 * on}}>{String(k + 1).padStart(2, '0')}</div>
            <div style={{display: 'flex', alignItems: 'baseline', gap: 30, opacity: on, transform: `translateX(${-40 * (1 - on)}px)`}}>
              <div style={{fontFamily: DISPLAY, fontWeight: 800, fontSize: TIPO.titulo, color: C.texto, whiteSpace: 'nowrap'}}>{a}</div>
              {b ? <div style={{fontFamily: BODY, fontSize: TIPO.cuerpo, color: C.suave}}>{b}</div> : null}
            </div>
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
  const R = R_EQ, L = 2 * Math.PI * R;
  return (
    <div style={{position: 'absolute', left: 140, top: 400, display: 'grid', gridTemplateColumns: `repeat(${its.length}, ${PASO_EQ - 40}px)`, gap: 40}}>
      {its.map((it, k) => {
        const [nombre, rol] = it.texto.split(' · ');
        const ini = nombre.split(' ').map((w) => w[0]).join('').slice(0, 2);
        const aro = ease(t, a0 + 0.15 * k, 0.6), on = ease(t, dice(c, it.en) - 0.15, 0.4);
        return (
          <div key={k} style={{display: 'grid', justifyItems: 'start', gap: 22}}>
            <div style={{position: 'relative', width: 2 * R + 8, height: 2 * R + 8, borderRadius: '50%', background: C.fondo}}>
              {it.foto
                // su foto (del deck de inversores): en gris y tenue hasta que la voz la nombra, entonces a color
                ? <div style={{position: 'absolute', left: 8, top: 8, width: 2 * R - 8, height: 2 * R - 8, borderRadius: '50%', overflow: 'hidden',
                    opacity: (0.8 + 0.2 * on) * aro, transform: `scale(${0.96 + 0.04 * on})`}}>
                    {/* zoom por foto: recorta fondos con ruido (la de Bellmunt trae persianas detrás) */}
                    <Img src={staticFile(it.foto)} style={{width: '100%', height: '100%', objectFit: 'cover', filter: `grayscale(${0.35 * (1 - on)})`,
                      transform: `scale(${(it as any).zoom || 1})`, transformOrigin: (it as any).foco || '50% 40%'}} />
                  </div>
                : <div style={{position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: DISPLAY, fontWeight: 800,
                    fontSize: TIPO.titulo, color: C.texto, opacity: 0.25 + 0.75 * on}}>{ini}</div>}
              <svg width={2 * R + 8} height={2 * R + 8} style={{position: 'absolute', inset: 0}}>
                <circle cx={R + 4} cy={R + 4} r={R} fill="none" stroke={on > 0.5 ? C.azul_sobre_navy : 'rgba(255,255,255,.35)'} strokeWidth={4}
                  strokeDasharray={L} strokeDashoffset={L * (1 - aro)} transform={`rotate(-90 ${R + 4} ${R + 4})`} />
              </svg>
            </div>
            <div style={{opacity: (0.7 + 0.3 * on) * aro}}>
              <div style={{fontFamily: DISPLAY, fontWeight: 800, fontSize: TIPO.cuerpo, lineHeight: 1.1, color: C.texto, whiteSpace: 'nowrap'}}>{nombre}</div>
              {rol ? <div style={{marginTop: 8, fontFamily: BODY, fontSize: TIPO.detalle, color: C.suave}}>{rol}</div> : null}
            </div>
          </div>
        );
      })}
    </div>
  );
};

// cierre en claro (16-oct-2026): el referente visto, Recursion 101, cierra sobre blanco con su logo y su forma de marca; y el
// logo azul sobre blanco es el uso principal del kit de ClinicOmics. Rompe el «todo navy con brillo» que delataba plantilla.
// Sobre blanco nada de cian (el kit lo reserva para navy): acentos y píldora en el azul del logo.
const CLARO = {fondo: '#FFFFFF', texto: '#0B1B3A', suave: '#3A4A66'};
const ECierre: React.FC<EP> = ({c, t}) => {
  const a = inicio(CAPS.indexOf(c));
  const ultima = c.frases[c.frases.length - 1].t0;  // «ClinicOmics. Functional omics for precision oncology.»
  const cortina = ease(t, a - 0.12, 0.7);
  return (
    <AbsoluteFill style={{background: CLARO.fondo, clipPath: `inset(0 ${100 - 100 * cortina}% 0 0)`}}>
      <AbsoluteFill style={{justifyContent: 'center', paddingLeft: 160, paddingRight: 160, gap: 30}}>
        <div style={entra(t, a - 0.02)}><Img src={staticFile('narrado/logo-clinicomics.png')} style={{width: 620, height: 'auto', display: 'block'}} /></div>
        {c.escena.ask ? <div style={{position: 'absolute', left: 160, right: 160, top: 560, opacity: entra(t, a + 0.5).opacity * (1 - lin(t, ultima - 0.4, ultima))}}>
          <div style={{fontFamily: DISPLAY, fontWeight: 800, fontSize: TIPO.titulo, lineHeight: 1.1, color: CLARO.texto, maxWidth: 1250, textWrap: 'balance'} as React.CSSProperties}><Acento s={c.escena.ask} color={C.azul} /></div>
        </div> : null}
        <div style={{fontFamily: DISPLAY, fontWeight: 800, fontSize: TIPO.display, lineHeight: 1.05, letterSpacing: '-0.015em', color: CLARO.texto, textWrap: 'balance', ...entra(t, ultima)} as React.CSSProperties}>{c.escena.titular}</div>
        <div style={{display: 'flex', flexDirection: 'column', gap: 22, alignItems: 'flex-start', ...entra(t, ultima + 1.5)}}>  {/* 1,5 s de respiro tras el lema */}
          {c.escena.pie ? <div style={{fontFamily: BODY, fontWeight: 600, fontSize: TIPO.detalle, color: C.azul}}>{c.escena.pie}</div> : null}
          {c.escena.url ? <div style={{fontFamily: MONO, fontWeight: 500, fontSize: TIPO.url, color: CLARO.texto, padding: '10px 30px', borderRadius: 999,
            border: `3px solid ${C.azul}`}}>{c.escena.url}</div> : null}
        </div>
        {/* a quién escribir: sin web propia todavía, sus correos (Henry, 16-oct-2026) */}
        {c.escena.contacto ? <div style={{fontFamily: MONO, fontSize: TIPO.detalle, color: CLARO.suave, ...entra(t, ultima + 1.8)}}>{c.escena.contacto}</div> : null}
      </AbsoluteFill>
    </AbsoluteFill>
  );
};

const ESCENAS: Record<string, React.FC<EP>> = {portada: EPortada, golpes: EGolpes, diagrama: EDiagrama, cita: ECita, lista: ELista, equipo: EEquipo, cierre: ECierre};
const SIN_CABECERA = ['portada', 'cierre'];

// cámara: cada capítulo llega desde la derecha y se va hacia la izquierda, como un paneo sobre un lienzo continuo.
// Relevo sin hueco ni solape: el saliente acaba en fin-0,02 y el texto entrante empieza ahí mismo. Antes quedaban 0,25-0,4 s
// de pantalla vacía; un fundido cruzado dejaba dos titulares legibles a la vez (16-oct-2026).
const PANEO = 0;  // corte limpio en la frontera de frase: el hilo que cambia de forma da la continuidad (diseño, 16-oct-2026)
const Capitulos: React.FC = () => {
  const t = useT();
  return (
    <>
      {CAPS.map((c, i) => {
        if (t < inicio(i) - 0.1 || t > fin(i)) return null;
        const sale = i + 1 < CAPS.length ? Easing.bezier(0.4, 0, 1, 1)(lin(t, fin(i) - 0.32, fin(i) - 0.02)) : 0;
        const llega = i === 0 ? 1 : ease(t, inicio(i) - 0.1, 0.9);
        const x = PANEO * (1 - llega) - PANEO * 0.45 * sale;  // 0,45: a recorrido entero la «G» salía cortada por el borde (diseño, 16-oct-2026)
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
        const lleno = lin(t, inicio(i), fin(i));  // se llena a lo largo del capítulo: antes se llenaba en 0,4 s y parecía quieto
        const activo = t >= inicio(i) && t < fin(i);
        return <div key={i} style={{flex: 1, height: 4, borderRadius: 2, background: 'rgba(255,255,255,.14)', overflow: 'hidden'}}>
          <div style={{height: '100%', width: `${100 * lleno}%`, background: activo ? C.cian : 'rgba(1,196,255,.45)'}} /></div>;
      })}
    </div>
  );
};

// ---------- subtítulos: SIEMPRE, toda la narración ({{TITULAR}}, 14-oct-2026: «los subtítulos siempre visibles aunque esté escrito en el texto») ----------
const Subtitulos: React.FC = () => {
  const t = useT();
  for (const c of CAPS) {
    for (const f of c.frases) {
      if (t < f.t0 - 0.05 || t > f.t1 + 0.25) continue;
      const o = lin(t, f.t0 - 0.05, f.t0 + 0.15) * (1 - lin(t, f.t1 + 0.05, f.t1 + 0.25));
      // a trozos de ≤9 palabras (una línea) cortando en la puntuación: una frase larga en 3 líneas tapaba el diagrama (16-oct-2026)
      const trozos: Palabra[][] = [[]];
      f.palabras.forEach((w) => {const u = trozos[trozos.length - 1]; u.push(w);
        const enlace = /^(a|an|the|of|to|in|on|and|or|its|our|that|which|from|with|for|by|as)$/i.test(w.w);
        if ((u.length >= 9 && !enlace) || u.length >= 11 || (u.length >= 5 && /[.,:;]$/.test(w.w))) trozos.push([]);});
      const llenos = trozos.filter((x) => x.length);
      const k = Math.max(0, llenos.findIndex((x, j) => j === llenos.length - 1 || t < llenos[j + 1][0].a - 0.05));
      const trozo = llenos[k] || f.palabras;
      return (
        <div style={{position: 'absolute', left: 0, right: 0, bottom: 64, display: 'flex', justifyContent: 'center', opacity: o}}>
          <div style={{maxWidth: 1640, padding: '14px 28px', borderRadius: 14, background: 'rgba(7,18,42,.82)', fontFamily: BODY, fontWeight: 600,
            fontSize: TIPO.subtitulo, lineHeight: 1.3, color: C.texto, textAlign: 'center'}}>
            {trozo.map((w, j) => <span key={j} style={{color: t >= w.a ? C.texto : C.suave}}>{w.w} </span>)}
          </div>
        </div>
      );
    }
  }
  return null;
};

// ---------- planos reales (stock con licencia Pexels, 16-oct-2026) ----------
// A pantalla completa sobre una frase, en blanco y negro con un velo navy y grano ({{TITULAR}}: «juega con blanco y negro»):
// así no parecen stock y casan con la marca. Solo manos y objetos, nunca caras (la licencia prohíbe insinuar respaldo).
type Broll = {src: string; cap: string; frase: number; dur?: number; zoom?: number; foco?: string};
const BROLL = ((T as any).broll || []) as Broll[];
const PlanoReal: React.FC<{b: Broll}> = ({b}) => {
  const t = useT();
  const c = CAPS.find((x) => x.id === b.cap);
  if (!c || !c.frases[b.frase]) return null;
  const a = c.frases[b.frase].t0 - 0.15, z = a + Math.min(b.dur || 4, c.frases[b.frase].t1 - c.frases[b.frase].t0 + 0.3);
  const o = lin(t, a, a + 0.35) * (1 - lin(t, z - 0.35, z));
  const empuje = 1 + 0.04 * lin(t, a, z);  // la cámara se acerca despacio
  return (
    <AbsoluteFill style={{opacity: o, background: C.fondo2}}>
      <Sequence from={Math.round(a * 30)} layout="none">
        <OffthreadVideo src={staticFile(b.src)} muted style={{width: '100%', height: '100%', objectFit: 'cover',
          transform: `scale(${(b.zoom || 1) * empuje})`, transformOrigin: b.foco || '50% 50%',
          filter: 'grayscale(1) contrast(1.35) brightness(0.8)'}} />
      </Sequence>
      {/* velo navy en multiplicar: el blanco y negro se tiñe de la marca sin volverse azul eléctrico */}
      <AbsoluteFill style={{background: `linear-gradient(160deg, ${C.fondo} 0%, #2A3B66 100%)`, mixBlendMode: 'multiply', opacity: 0.45}} />
      <svg width={1920} height={1080} style={{position: 'absolute', inset: 0, opacity: 0.12, mixBlendMode: 'overlay'}}>
        <filter id="granoPlano"><feTurbulence type="fractalNoise" baseFrequency="0.85" numOctaves={2} seed={Math.floor(t * 12)} /><feColorMatrix type="saturate" values="0" /></filter>
        <rect width="100%" height="100%" filter="url(#granoPlano)" />
      </svg>
    </AbsoluteFill>
  );
};
const PlanosReales: React.FC = () => <>{BROLL.map((b, k) => <PlanoReal key={k} b={b} />)}</>;

const hablando = (t: number) => CAPS.some((c) => c.frases.some((f) => t >= f.t0 && t <= f.t1));
const volMusica = (f: number) => {const t = f / 30; let v = 0; for (let k = -8; k <= 8; k++) v += hablando(t + k * 0.03) ? 1 : 0; return 0.38 - 0.28 * (v / 17);};

// el vídeo no acaba en corte seco: funde a navy los últimos 0,8 s (diseño, 16-oct-2026)
const FundidoFinal: React.FC = () => {const t = useT(); return <AbsoluteFill style={{background: CAPS[CAPS.length - 1].escena.tipo === 'cierre' ? '#FFFFFF' : C.fondo2, opacity: lin(t, T.total - 0.8, T.total - 0.05)}} />;};

export const Pitch: React.FC = () => (
  <AbsoluteFill style={{background: C.fondo}}>
    <Fondo />
    <Hilo />
    <Capitulos />
    <LogoEsquina />
    <Rail />
    <PlanosReales />
    <Subtitulos />
    <FundidoFinal />
    <Audio src={staticFile('narrado/voz.wav')} />
    <Audio src={staticFile('narrado/musica.wav')} volume={volMusica} />
  </AbsoluteFill>
);
