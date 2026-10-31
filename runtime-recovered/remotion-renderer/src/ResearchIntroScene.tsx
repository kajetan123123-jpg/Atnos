import React from 'react';
import {interpolate,useCurrentFrame} from 'remotion';
import type {BrandSpec,ManifestScene} from './contracts';
import {SceneShell} from './design';
import {MeasuredText} from './typography';

/** Introduce the actual research alternatives; never a predicted price chart. */
export const ResearchIntroScene:React.FC<{scene:ManifestScene;brand:BrandSpec;subjects:string[]}>=({scene,brand,subjects})=>{
  const frame=useCurrentFrame();
  if(subjects.length!==3||new Set(subjects).size!==3)throw new Error('Research intro requires three distinct planned subjects');
  return <SceneShell scene={scene} brand={brand} showBrandMark={false} compactCaptions>
    <div aria-label={scene.narration} style={{position:'absolute',left:76,top:146,width:896,color:brand.text}}>
      <MeasuredText text={scene.narration} width={896} maxSize={68} minSize={48} maxLines={3}/>
    </div>
    {subjects.map((subject,index)=>{
      const reveal=interpolate(frame,[index*8,index*8+12],[0,1],{extrapolateLeft:'clamp',extrapolateRight:'clamp'});
      const top=566+index*282;
      const accent=['#187F83','#C99A51','#AB5848'][index];
      return <div key={subject} data-wb-research-alternative={subject} style={{position:'absolute',left:76,top,width:896,height:230,opacity:reveal}}>
        <svg width="896" height="230" viewBox="0 0 896 230" style={{position:'absolute',inset:0}}>
          <path d="M9 14 Q448 -3 883 17 L888 220 Q448 210 6 224Z" fill="#0C1633" transform="translate(8 8)"/>
          <path d="M9 14 Q448 -3 883 17 L888 220 Q448 210 6 224Z" fill="#F0E1C2"/>
          <path d="M19 30 Q430 17 865 30 M20 203 Q440 190 867 205" fill="none" stroke="#B59A6B" strokeWidth="4" opacity=".3"/>
          <path d="M40 60 Q43 110 38 175" stroke={accent} strokeWidth="12" strokeLinecap="round" fill="none"/>
          <path d="M73 181 Q406 193 811 179" stroke={accent} strokeWidth="5" strokeLinecap="round" pathLength="1" strokeDasharray="1" strokeDashoffset={1-reveal} fill="none"/>
        </svg>
        <div style={{position:'absolute',left:74,top:66,width:750,color:'#171A3B'}}><MeasuredText text={subject} width={750} maxSize={62} minSize={48} maxLines={2}/></div>
      </div>;
    })}
  </SceneShell>;
};
