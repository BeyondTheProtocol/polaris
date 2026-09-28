import React from 'react';
import {Composition} from 'remotion';
import {Polaris} from './Polaris';
import timeline from '../public/timeline.json';

// La duración sale del timeline (montaje.py), nunca de un número a mano.
export const Root: React.FC = () => (
  <Composition id="Polaris" component={Polaris} durationInFrames={Math.round(timeline.total * 30)} fps={30} width={1920} height={1080} />
);
