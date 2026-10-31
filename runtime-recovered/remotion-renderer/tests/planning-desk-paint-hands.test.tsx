import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {createHash} from 'node:crypto';
import {describe, expect, it} from 'vitest';
import {AccountPhone, WritingHand} from '../src/WBPlanningDeskHands';

const svg = (child: React.ReactNode) => renderToStaticMarkup(<svg>{child}</svg>);
const sha = (html: string) => createHash('sha256').update(html).digest('hex');

describe('painted phone and hands retain their production geometry', () => {
  it('preserves the exact prior clean render when the painted system is not selected', () => {
    // Captured from the unchanged clean implementation before this material edit.
    expect(sha(svg(<WritingHand x={400} y={1000} opacity={0.8} id="hands-clean" />)))
      .toBe('9832ef1533d8538b7ea358d75bee5e9f2c88bb2a4c64f2f7c2bb314b58f099ba');
    expect(sha(svg(<AccountPhone id="phone-clean" />)))
      .toBe('f617d220281eb6009bfaacf1dd2199ddfce231eedde1a0f0116483deb9f23fcc');
  });

  it('uses independent silhouette clips with no duplicate IDs when both objects share the scene prefix', () => {
    const html = svg(<><AccountPhone id="same-scene" painted />
      <WritingHand x={400} y={1000} opacity={1} id="same-scene" painted /></>);
    const ids = [...html.matchAll(/\bid="([^"]+)"/g)].map(match => match[1]);
    const clips = [...html.matchAll(/clip-path="url\(#([^)]+)\)"/g)].map(match => match[1]);
    expect(new Set(ids).size).toBe(ids.length);
    expect(clips.length).toBeGreaterThan(6);
    expect(clips.every(id => ids.includes(id))).toBe(true);
    expect(html.match(/data-wb-object="single-account-phone"/g)).toHaveLength(1);
    expect(html.match(/data-wb-action="write-purpose-underline"/g)).toHaveLength(1);
  });

  it('keeps phone paint behind the existing legible account text and preserves its actual copy', () => {
    const clean = svg(<AccountPhone id="phone" />);
    const painted = svg(<AccountPhone id="phone" painted />);
    const labels = (html: string) => [...html.matchAll(/<text\b[^>]*>([^<]*)<\/text>/g)].map(match => match[1]);
    expect(labels(painted)).toEqual(labels(clean));
    expect(labels(painted)).toEqual(['ONE', 'ACCOUNT']);
    expect(painted.indexOf('data-wb-painted-volume="phone-case"')).toBeLessThan(painted.indexOf('id="phone-painted-glass"'));
    expect(painted.indexOf('clip-path="url(#phone-painted-glass)"')).toBeLessThan(painted.indexOf('>ONE</text>'));
    expect(painted.match(/data-wb-object="single-account-phone" transform="([^"]+)"/)?.[1])
      .toEqual(clean.match(/data-wb-object="single-account-phone" transform="([^"]+)"/)?.[1]);
  });

  it('moves the same fixed local material marks with the writing hand instead of regenerating texture', () => {
    const pose = (x: number) => svg(<WritingHand x={x} y={1000} opacity={0.8} id="fixed" painted />);
    const first = pose(400);
    expect(pose(400)).toBe(first);
    // Only the existing outer gesture transform changes; all local surface marks stay fixed.
    expect(pose(500).replace('translate(500 1000)', 'translate(400 1000)')).toBe(first);
    expect(first).toContain('data-wb-painted-volume="writing-sleeve"');
    expect(first).toContain('clip-path="url(#fixed-skin)"');
    expect(first).not.toMatch(/feTurbulence|feDisplacementMap|<animate/);
  });
});
