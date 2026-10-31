import React, {useEffect, useState} from 'react';
import {continueRender, delayRender} from 'remotion';

export type TextLayout = {lines: string[]; size: number};
// Keep currency and short syntactic units together when wrapping, without dropping words.
export const textUnits = (text: string): string[] => text.trim().split(/\s+/).reduce<string[]>((units, word) => {
  const last = units[units.length - 1] ?? '';
  if (/^(?:[$€£]|USD|GBP|EUR)$/i.test(last) || (/^\d[\d,.]*$/.test(last) && /^(?:dollars?|pounds?|euros?|USD|GBP|EUR)[,.!?]?$/i.test(word))) {
    units[units.length - 1] += ` ${word}`;
  } else units.push(word);
  return units;
}, []);

export function fitText(text: string, width: number, maxSize: number, minSize: number, maxLines: number,
  measure: (text: string, size: number) => number): TextLayout {
  for (let size = maxSize; size >= minSize; size--) {
    const lines: string[] = [];
    let line = '';
    let invalid = false;
    for (const unit of textUnits(text)) {
      if (measure(unit, size) > width) {invalid = true; break;}
      const next = line ? `${line} ${unit}` : unit;
      if (measure(next, size) > width) {lines.push(line); line = unit;} else line = next;
    }
    if (line) lines.push(line);
    if (!invalid && lines.length <= maxLines) return {lines, size};
  }
  throw new Error(`WB text does not fit its safe region: ${text}`);
}

const useMeasuredLayout = (text:string,width:number,maxSize:number,minSize:number,maxLines:number,weight:number,fontFamily='Atnos') => {
  const key = JSON.stringify([text, width, maxSize, minSize, maxLines, weight, fontFamily]);
  const [measured, setMeasured] = useState<{key: string; layout: TextLayout} | null>(null);
  const layout = measured?.key === key ? measured.layout : null;
  useEffect(() => {
    const handle = delayRender('Measure WB text using loaded font');
    let disposed = false;
    const measure = async () => {
      await document.fonts.load(`${weight} ${maxSize}px ${fontFamily}`);
      await document.fonts.ready;
      const ctx = document.createElement('canvas').getContext('2d');
      if (!ctx || !document.fonts.check(`${weight} ${maxSize}px ${fontFamily}`)) throw new Error('WB font unavailable');
      const result = fitText(text, width, maxSize, minSize, maxLines, (value, size) => {
        ctx.font = `${weight} ${size}px ${fontFamily}`; return ctx.measureText(value).width;
      });
      if (!disposed) setMeasured({key, layout: result});
    };
    measure().catch((error) => {if (!disposed) setMeasured(() => {throw error;});}).finally(() => continueRender(handle));
    return () => {disposed = true;};
  }, [text, width, maxSize, minSize, maxLines, weight, fontFamily]);
  return layout;
};

export const MeasuredText: React.FC<{text: string; width: number; maxSize: number; minSize: number;
  maxLines: number; weight?: number; align?: 'left' | 'center'; fontFamily?: string}> = ({text, width, maxSize, minSize, maxLines, weight=900, align='left',fontFamily='Atnos'}) => {
  const layout=useMeasuredLayout(text,width,maxSize,minSize,maxLines,weight,fontFamily);
  if (!layout) return <div data-wb-measuring="true" />;
  return <div data-wb-measured-text={text} style={{width, fontSize: layout.size, fontWeight: weight, fontFamily,lineHeight: 1.2, textAlign: align}}>
    {layout.lines.map((line, index) => <div key={index} style={{whiteSpace: 'pre'}}>{line}</div>)}
  </div>;
};

export const MeasuredSvgText: React.FC<{text:string;x:number;y:number;width:number;maxSize:number;
  minSize:number;maxLines:number;weight?:number;fill:string}> = ({text,x,y,width,maxSize,minSize,maxLines,weight=900,fill}) => {
  const layout=useMeasuredLayout(text,width,maxSize,minSize,maxLines,weight);
  if (!layout) return <text data-wb-measuring="true" visibility="hidden">{text}</text>;
  const first=y-(layout.lines.length-1)*layout.size*.6;
  return <text data-wb-measured-svg={text} x={x} y={first} textAnchor="middle" fill={fill}
    fontFamily="Atnos" fontSize={layout.size} fontWeight={weight}>
    {layout.lines.map((line,index)=><tspan key={index} x={x} dy={index?layout.size*1.2:0}>{line}</tspan>)}
  </text>;
};
