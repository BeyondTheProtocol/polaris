import React from 'react';
import {Composition} from 'remotion';
import {Polaris} from './Polaris';
import {Narrado} from './Narrado';
import timeline from '../public/timeline.json';
import narrado from '../public/narrado/timeline.json';

// La duración sale del timeline (montaje.py / narrado.py), nunca de un número a mano.
export const Root: React.FC = () => (
  <>
    <Composition id="Polaris" component={Polaris} durationInFrames={Math.round(timeline.total * 30)} fps={30} width={1920} height={1080} />
    <Composition id="Narrado" component={Narrado} durationInFrames={Math.round(narrado.total * 30)} fps={30} width={1920} height={1080} />
  </>
);
