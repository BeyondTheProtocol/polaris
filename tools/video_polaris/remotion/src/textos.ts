// Textos de pantalla del vídeo «Polaris», por idioma (28-sep-2026, versión ES). El idioma lo fija timeline.json
// (montaje.py --idioma), igual que las anclas de palabra: un solo componente, un diccionario por idioma.
// Las claves de ancla ('rare', 'two', 'takes'…) son las del EN; en otro idioma, timeline.anclas las traduce a su prefijo.
export type Textos = {
  golpes: [string, string, boolean][];        // [ancla, texto, en violeta] de la apertura cinética; en el orden en que se dicen
  dosCaras: string; muchoMas: string;          // palabra clave arriba
  gancho: [string, string]; ganchoChips: [string, string, string]; puertaHumana: string;
  masAlla: [string, string];                   // anclas 'go' y 'further'
  nombre: string; rol: string;
  equipo: string; nedSub: string;
  nodos: [string, string][];                   // Input · Goal · Polaris · Human gate · Output
  salida: [string, string][];                  // casilla Output por escena
  galeria: string[]; sufijo3d: string;
  radar: string;
  chips: [string, string][];                   // [texto, ancla] de los borradores
  borrador: string;
  cifras: [string, string][];                  // [clave de cifras.json, etiqueta]
  locale: string;
  nedDespliega: boolean;
  rotulado: Record<string, [string | null, string]>;  // de qué palabra a cuál NO se subtitula (ya está escrito en grande)  // N·E·D se despliegan en sus palabras (solo si las iniciales casan: EN)
  medicos: [string, string];
  cierre: [string, string, string];
};

const EN: Textos = {
  golpes: [['rare', 'A RARE', false], ['breast', 'BREAST CANCER.', false], ['ultra', 'ULTRA-RARE.', true], ['metastatic', 'METASTATIC.', false]],
  dosCaras: 'TWO SIDES.', muchoMas: 'AND A LOT MORE.',
  gancho: ['Ultra-rare cancer.', 'So I built Polaris.'], ganchoChips: ['3D maps', 'Trial radar', 'My sign-off'], puertaHumana: 'HUMAN GATE',
  masAlla: ['Go', 'further.'],
  nombre: '{{TITULAR}} {{APELLIDO}}', rol: 'engineer',
  equipo: 'a team of AI agents', nedSub: 'no evidence of disease',
  nodos: [['Input', 'What I need'], ['Goal', 'Written: NED'], ['Polaris', 'The experts'], ['Human gate', 'My sign-off'], ['Output', 'Whatever fits']],
  salida: [['aprender', 'Knowing my tumor'], ['3d', '3D maps'], ['radar', 'Trials, daily'], ['carga', 'Drafts to sign'], ['web', 'The website']],
  galeria: ['Breast', 'Liver', 'Skeleton', 'Bone'], sufijo3d: '· 3D',
  radar: 'TRIALS TRACKED DAILY · WORLDWIDE',
  chips: [['samples → labs', 'samples'], ['messages people send', 'messages'], ['trips for treatment', 'trips']],
  borrador: 'DRAFT',
  cifras: [['comites', 'agents'], ['tools', 'tools'], ['rutinas', 'routines running 24/7'], ['guardas', 'guardrails'], ['tests', 'test files']],
  locale: 'en-US', nedDespliega: true,
  rotulado: {problema: [null, 'metastatic.'], polaris: ['NED.', 'disease.'], 'mas-alla': ['go', 'further'], medicos: [null, 'do.'], ned: [null, '*'], 'web-final': [null, '*']},
  medicos: ["It doesn't decide.", 'My doctors do.'],
  cierre: ['Still going for NED.', 'My whole case is explained at', 'Built with Polaris, my AI agent system.'],
};

const ES: Textos = {
  golpes: [['breast', '{{DIAGNOSTICO}}', false], ['rare', 'RARO.', false], ['ultra', 'ULTRARRARO.', true], ['metastatic', 'METASTÁSICO.', false]],
  dosCaras: 'DOS CARAS.', muchoMas: 'Y MUCHO MÁS.',
  gancho: ['Un cáncer ultrarraro.', 'Así que construí Polaris.'], ganchoChips: ['Mapas 3D', 'Radar de ensayos', 'Firmo yo'], puertaHumana: 'FIRMA HUMANA',
  masAlla: ['Ir', 'más allá.'],
  nombre: '{{TITULAR}} {{APELLIDO}}', rol: 'ingeniera',
  equipo: 'un equipo de agentes de IA', nedSub: 'sin evidencia de enfermedad',
  nodos: [['Lo que entra', 'Pruebas y mensajes'], ['El objetivo', 'NED, por escrito'], ['El equipo', 'Agentes de IA'], ['Quién firma', 'Yo. Siempre.'], ['Lo que sale', 'Solo lo firmado']],
  salida: [['aprender', 'Entender mi tumor'], ['3d', 'Mis lesiones en 3D'], ['radar', 'Ensayos del mundo'], ['carga', 'Borradores para firmar'], ['web', 'helptitular.com']],
  galeria: ['Mama', 'Hígado', 'Esqueleto', 'Hueso'], sufijo3d: '· 3D',
  radar: 'ENSAYOS CLÍNICOS DE TODO EL MUNDO, CADA DÍA',
  chips: [['muestras al laboratorio', 'samples'], ['emails y profesionales', 'messages'], ['viajes del tratamiento', 'trips']],
  borrador: 'BORRADOR',
  cifras: [['comites', 'agentes'], ['tools', 'herramientas'], ['rutinas', 'rutinas en marcha 24/7'], ['guardas', 'salvaguardas'], ['tests', 'ficheros de test']],
  locale: 'es-ES', nedDespliega: false,
  rotulado: {problema: [null, 'metastásico.'], polaris: ['NED.', 'enfermedad.'], 'mas-alla': ['ir', 'allá'], medicos: [null, 'médicos.'], ned: [null, '*'], 'web-final': [null, '*']},
  medicos: ['Polaris no decide.', 'Deciden mis médicos.'],
  cierre: ['Sigo investigando hasta llegar a NED.', 'Todo mi caso está explicado en', 'Hecho con Polaris, firmado por mí.'],
};

export const TEXTOS: Record<string, Textos> = {en: EN, es: ES};
