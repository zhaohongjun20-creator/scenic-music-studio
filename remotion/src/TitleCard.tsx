import React from 'react';
import {
  AbsoluteFill,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';

export type TitleCardProps = {
  title: string;
  subtitle: string;
  accent: string;
  durationSec: number;
};

/**
 * 片头标题卡（透明背景，叠在片头画面上）：
 * 主标题楷体呼吸缩放 + 上下细线从中心展开 + 副标题延迟淡入，末段整体淡出。
 */
export const TitleCard: React.FC<TitleCardProps> = ({
  title, subtitle, accent, durationSec,
}) => {
  const frame = useCurrentFrame();
  const {fps, durationInFrames} = useVideoConfig();

  const enter = spring({frame, fps, config: {damping: 200, mass: 0.6}});
  const scale = interpolate(enter, [0, 1], [1.06, 1]);
  const fadeOutStart = durationInFrames - Math.round(0.5 * fps);
  const fadeOut = interpolate(frame, [fadeOutStart, durationInFrames], [1, 0], {
    extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
  });
  const lineSpread = interpolate(frame, [Math.round(0.25 * fps), Math.round(0.9 * fps)],
    [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  const subOpacity = interpolate(frame, [Math.round(0.7 * fps), Math.round(1.2 * fps)],
    [0, 0.92], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});

  return (
    <AbsoluteFill style={{justifyContent: 'center', alignItems: 'center',
                          opacity: fadeOut}}>
      <div style={{display: 'flex', flexDirection: 'column', alignItems: 'center',
                   transform: `scale(${scale})`, marginTop: -60}}>
        <div style={{width: 420 * lineSpread, height: 1, background: accent,
                     opacity: 0.75, marginBottom: 36}} />
        <div style={{
          fontFamily: '"KaiTi", "STKaiti", "Noto Serif SC", serif',
          fontSize: 104, color: '#ffffff', letterSpacing: '0.14em',
          textShadow: '0 2px 26px rgba(0,0,0,0.75), 0 0 4px rgba(0,0,0,0.5)',
          fontWeight: 400, whiteSpace: 'nowrap',
        }}>
          {title}
        </div>
        <div style={{
          fontFamily: '"Microsoft YaHei", sans-serif', fontSize: 30,
          color: '#f2ede2', letterSpacing: '0.42em', marginTop: 34,
          opacity: subOpacity,
          textShadow: '0 1px 14px rgba(0,0,0,0.8)',
        }}>
          {subtitle}
        </div>
        <div style={{width: 420 * lineSpread, height: 1, background: accent,
                     opacity: 0.75, marginTop: 36}} />
      </div>
    </AbsoluteFill>
  );
};
