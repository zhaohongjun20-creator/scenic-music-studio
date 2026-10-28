import React from 'react';
import {Composition} from 'remotion';
import {TitleCard, TitleCardProps} from './TitleCard';
import {OutroCard, OutroCardProps} from './OutroCard';

const FPS = 30;

export const RemotionRoot: React.FC = () => {
  return (
    <>
      <Composition
        id="TitleCard"
        component={TitleCard as React.FC<Record<string, unknown>>}
        durationInFrames={FPS * 3}
        fps={FPS}
        width={1920}
        height={1080}
        defaultProps={{
          title: '把治愈系山海的风，寄给你',
          subtitle: '治愈系山海 · 纯音乐风景',
          accent: '#e8d9b0',
          durationSec: 3,
        }}
        calculateMetadata={({props}) => ({
          durationInFrames: Math.round(
            ((props as unknown as TitleCardProps).durationSec) * FPS),
        })}
      />
      <Composition
        id="OutroCard"
        component={OutroCard as React.FC<Record<string, unknown>>}
        durationInFrames={Math.round(FPS * 3.5)}
        fps={FPS}
        width={1920}
        height={1080}
        defaultProps={{
          lines: ['音乐：Awake — johnfrantz17 (CC BY 3.0)',
                  '视频素材：Pexels · Pexels License'],
          accent: '#e8d9b0',
          durationSec: 3.5,
        }}
        calculateMetadata={({props}) => ({
          durationInFrames: Math.round(
            ((props as unknown as OutroCardProps).durationSec) * FPS),
        })}
      />
    </>
  );
};
