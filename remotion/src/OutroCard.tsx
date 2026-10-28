import React from 'react';
import {
  AbsoluteFill,
  interpolate,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';

export type OutroCardProps = {
  lines: string[];
  accent: string;
  durationSec: number;
};

/**
 * 片尾落版（透明背景，叠在片尾画面上）：
 * 署名/来源逐行淡入，顶部细线点缀，末段整体淡出。
 */
export const OutroCard: React.FC<OutroCardProps> = ({lines, accent, durationSec}) => {
  const frame = useCurrentFrame();
  const {fps, durationInFrames} = useVideoConfig();
  const fadeOutStart = durationInFrames - Math.round(0.7 * fps);
  const fadeOut = interpolate(frame, [fadeOutStart, durationInFrames], [1, 0], {
    extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
  });

  return (
    <AbsoluteFill style={{justifyContent: 'center', alignItems: 'center',
                          opacity: fadeOut}}>
      <div style={{display: 'flex', flexDirection: 'column', alignItems: 'center',
                   marginTop: 180}}>
        <div style={{width: 60, height: 2, background: accent, opacity: 0.8,
                     marginBottom: 30}} />
        {lines.map((line, i) => {
          const at = Math.round((0.3 + i * 0.45) * fps);
          const o = interpolate(frame, [at, at + Math.round(0.45 * fps)], [0, 0.95],
            {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
          return (
            <div key={i} style={{
              fontFamily: '"Microsoft YaHei", sans-serif', fontSize: 26,
              color: '#f5f1e8', letterSpacing: '0.12em', marginBottom: 14,
              opacity: o, textShadow: '0 1px 12px rgba(0,0,0,0.85)',
            }}>
              {line}
            </div>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};
