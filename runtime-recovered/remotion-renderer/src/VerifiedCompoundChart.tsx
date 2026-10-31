import React from 'react';
import {useCurrentFrame} from 'remotion';
import type {BrandSpec, ManifestScene} from './contracts';
import {SceneShell} from './design';
import {MeasuredText} from './typography';
import {COMPOUND_FRAME as box, compoundProgress, compoundTiming} from './compound-series';

/** One verified example, plotted in real dollars and elapsed years, with a reading hold. */
export const VerifiedCompoundChart: React.FC<{scene: ManifestScene; brand: BrandSpec}> = ({scene,brand}) => {
  const frame = useCurrentFrame();
  const series = scene.verified_series!;
  const progress = compoundProgress(scene,frame/30);
  const paperId = 'compound-paper-' + scene.scene_id.replace(/[^\w-]/g,'');
  const ink = '#171A3B', teal = '#187F83';
  const x = (year: number) => 58+year/series.horizon_years*732;
  const y = (value: number) => 522-value/series.values[3]*430;
  const points = Array.from({length:81},(_,index) => {
    const year = series.horizon_years*progress*index/80;
    return `${x(year)},${y(series.principal*(1+series.annual_rate)**year)}`;
  });
  return <SceneShell scene={scene} brand={brand} captionStyle="plain_phrase">
    <div style={{position:'absolute',left:box.left,top:box.headingTop,width:box.width,color:brand.text}}>
      <MeasuredText text={scene.headline || 'ONE DEPOSIT. TIME TO GROW.'} width={box.width} maxSize={60} minSize={44} maxLines={2}/>
      <div style={{marginTop:24,color:brand.accent}}><MeasuredText text={series.assumption_label} width={box.width} maxSize={38} minSize={32} maxLines={2} weight={700}/></div>
    </div>
    <div data-wb-compound-timing={compoundTiming(scene).source} style={{position:'absolute',left:box.left,top:box.plotTop,width:box.width}}>
      <svg width={box.width} height={box.plotHeight} viewBox="0 0 848 640" aria-label="Hypothetical annual compound growth, no extra deposits">
        <defs>
          <linearGradient id={paperId} x2=".8" y2="1"><stop stopColor="#FFF9E9"/><stop offset="1" stopColor="#E8D5B1"/></linearGradient>
        </defs>
        <path data-wb-chart-material="cream-paper" d="M5 9 Q420 -2 839 8 L845 628 Q425 640 5 630Z" fill={`url(#${paperId})`}/>
        <path d="M16 25 Q384 12 825 24 M14 609 Q414 622 831 608" stroke="#BCA47A" strokeWidth={3} opacity={.35} fill="none"/>
        <path d="M58 58 V522 H790" fill="none" stroke={ink} strokeWidth={3}/>
        {[0,.25,.5,.75,1].map(fraction => <line key={fraction} x1={58} x2={790} y1={522-fraction*430} y2={522-fraction*430} stroke={teal} opacity={.20} strokeWidth={1}/>)}
        <polyline points={points.join(' ')} fill="none" stroke={teal} strokeWidth={7} strokeLinejoin="round" strokeLinecap="round"/>
        {series.years.map((year,i) => {
          const visible = i===0 || progress*series.horizon_years >= year;
          return <g key={year} opacity={visible ? 1 : 0}>
            <circle cx={x(year)} cy={y(series.values[i])} r={9} fill={brand.accent} stroke={ink} strokeWidth={3}/>
            <text x={x(year)} y={y(series.values[i])-27} fill={ink} fontSize={42} fontWeight={900} textAnchor={i===0?'start':i===3?'end':'middle'}>
              {series.unit==='dollars'?'$':''}{series.display_values[i]}{series.unit==='PLN'?' PLN':''}
            </text>
          </g>;
        })}
        {series.years.map((year,i) => <text key={year} x={x(year)} y={575} fill={ink} fontSize={32} textAnchor={i===0?'start':i===3?'end':'middle'} fontWeight={700}>Year {year}</text>)}
      </svg>
    </div>
    <div style={{position:'absolute',left:box.left,top:box.noteTop,width:box.width,color:brand.text}}>
      <MeasuredText text="One deposit. No additional contributions." width={box.width} maxSize={34} minSize={30} maxLines={2} weight={700}/>
      <div style={{marginTop:16,opacity:.8}}><MeasuredText text={series.exclusions_label} width={box.width} maxSize={30} minSize={28} maxLines={2} weight={700}/></div>
    </div>
  </SceneShell>;
};
