import React from 'react';
import {MeasuredSvgText} from './typography';
export const INK='var(--reserve-ink, #142633)', PAPER='var(--reserve-paper, #F0E5D2)', GOLD='var(--reserve-gold, #C99D60)', BLUE='var(--reserve-blue, #426579)', RUST='var(--reserve-rust, #995E4B)';

/** Original native renderer illustrations. Candidate artwork, not user-approved library assets. */
export const ReserveObjectDefs: React.FC<{id:string}> = ({id}) => <defs>
  <linearGradient id={`${id}-car`} x1="0" y1="0" x2=".3" y2="1"><stop stopColor="var(--reserve-mint, #6A8291)"/><stop offset=".5" stopColor="var(--reserve-blue, #314D60)"/><stop offset="1" stopColor="var(--reserve-ink, #172B3D)"/></linearGradient>
  <linearGradient id={`${id}-glass`} x2=".8" y2="1"><stop stopColor="#0F2735"/><stop offset="1" stopColor="#65818B"/></linearGradient>
  <linearGradient id={`${id}-paper`} x1="0" y1="0" x2=".65" y2="1"><stop stopColor="#FFF4DF"/><stop offset="1" stopColor="#DCCBAC"/></linearGradient>
  <linearGradient id={`${id}-light`}><stop stopColor={GOLD} stopOpacity=".16"/><stop offset="1" stopColor={GOLD} stopOpacity="0"/></linearGradient>
</defs>;

export const CarDrawing: React.FC<{id:string}> = ({id}) => <g data-wb-object="car-repair" stroke={INK} strokeLinejoin="round">
  <ellipse cx="450" cy="401" rx="368" ry="34" fill="#050C12" opacity=".4" stroke="none" />
  <path d="M115 210 L191 194 L269 99 Q290 80 342 80 H545 Q589 83 610 116 L656 188 L731 207 Q790 222 795 284 L789 335 H106 L87 289 Q81 237 115 210Z" fill={`url(#${id}-car)`} strokeWidth="5" />
  <path d="M215 192 L286 107 Q297 98 339 98 H423 V190Z" fill={`url(#${id}-glass)`} strokeWidth="4" />
  <path d="M441 98 H537 Q565 100 584 124 L626 187 H441Z" fill={`url(#${id}-glass)`} strokeWidth="4" />
  <path d="M442 198 V315 M246 207 L228 302 M668 220 L681 296" fill="none" strokeWidth="3" opacity=".85" />
  <path d="M117 243 Q410 213 762 244" fill="none" stroke={PAPER} strokeWidth="3" opacity=".38" />
  <path d="M284 113 L259 175 M330 108 L306 168 M498 108 L541 169" stroke={PAPER} strokeWidth="4" opacity=".12" />
  <path d="M123 329 Q172 309 218 336 M524 335 Q581 303 637 333" fill="none" stroke={PAPER} strokeWidth="3" opacity=".25" />
  {[211,660].map(x=><g key={x}>
    <circle cx={x} cy="326" r="72" fill="#101C24" strokeWidth="5" />
    <circle cx={x} cy="326" r="46" fill="#ACAC9F" strokeWidth="3" />
    <circle cx={x} cy="326" r="30" fill="#566877" strokeWidth="3" />
    {[0,60,120,180,240,300].map(angle=><path key={angle} d={`M${x} 290 V312`} transform={`rotate(${angle} ${x} 326)`} stroke={PAPER} strokeWidth="6" />)}
    <circle cx={x} cy="326" r="10" fill={GOLD} strokeWidth="2" />
    <path d={`M${x-54} 305 Q${x} 238 ${x+53} 305`} fill="none" stroke={PAPER} opacity=".24" strokeWidth="2" />
  </g>)}
  <path d="M693 197 L777 126 L809 180 L748 222Z" fill="#536D7D" strokeWidth="4" />
  <path d="M744 219 L777 141" fill="none" stroke={GOLD} strokeWidth="3" />
  <path d="M748 231 L786 242 L782 265 L746 259Z" fill={GOLD} strokeWidth="3" />
  <path d="M103 245 L137 242 L140 267 H100Z" fill={RUST} strokeWidth="3" />
  <path d="M88 295 H135 M765 291 H799" fill="none" stroke="#B6AC91" strokeWidth="8" />
  <path d="M363 217 H403 M499 217 H535" stroke={GOLD} strokeWidth="5" />
  <path d="M131 224 L180 213 M143 231 L174 224 M474 291 L530 288" stroke={INK} strokeWidth="1.5" opacity=".45" />
  <g transform="translate(690 16)"><path d="M0 49 L43 -24 L86 49Z" fill={RUST} stroke={GOLD} strokeWidth="3" />
    <path d="M43 0 V24" stroke={PAPER} strokeWidth="6" /><circle cx="43" cy="36" r="3" fill={PAPER} stroke="none" /></g>
</g>;

export const ExpenseSheet: React.FC<{label:string;id:string;medical?:boolean;planned?:boolean;focus?:'calendar'|'list';progress?:number}> = ({label,id,medical,planned,focus='calendar',progress=1}) => <g data-wb-object={planned?'planned-bill':'essential-expense'}>
  <path d="M-143 -178 L154 -183 L162 196 L139 186 L118 198 L98 186 L77 198 L57 186 L35 198 L15 186 L-8 198 L-31 186 L-53 198 L-75 186 L-99 198 L-119 186 L-143 196Z" fill={`url(#${id}-paper)`} stroke={INK} strokeWidth="3" />
  <path d="M-126 -161 L140 -164 M-126 -157 L136 -160" stroke={GOLD} strokeWidth="1.3" opacity=".4" />
  {planned ? (focus==='list' ? <g data-wb-action="record-known-bills" stroke={BLUE} fill="none" strokeWidth="4">
    <path d="M-90 -130 H90 M-90 -95 H90 M-90 -60 H90" opacity=".2"/>
    <path d="M-87 -130 H-22 M8 -130 H88 M-87 -95 H-22 M8 -95 H88 M-87 -60 H-22 M8 -60 H88"
      strokeDasharray="430" strokeDashoffset={430*(1-progress)}/>
    <path d="M-9 -145 V-48" stroke={GOLD}/>
  </g> : <g data-wb-action="show-known-due-date" stroke={BLUE} strokeWidth="4">
    <rect x="-77" y="-137" width="154" height="94" rx="9" fill={PAPER}/>
    <path d="M-77 -111 H77 M-48 -149 V-127 M48 -149 V-127" fill="none"/>
    <circle cy="-77" r="23" fill={GOLD} stroke={INK}/>
    <text y="-65" textAnchor="middle" fill={INK} stroke="none" fontSize="33" fontWeight="900">?</text>
  </g>) : medical?<path d="M-24 -123 H24 V-104 H44 V-56 H24 V-37 H-24 V-56 H-44 V-104 H-24Z" fill={RUST}/>:
    <path d="M-29 -125 Q-9 -144 13 -121 L2 -110 L15 -94 L28 -105 Q45 -76 22 -59 L-9 -82 L-48 -43 L-63 -58 L-26 -100Z" fill={BLUE} stroke={INK} strokeWidth="2"/>}
  <MeasuredSvgText text={label} x={0} y={17} width={256} maxSize={30} minSize={26} maxLines={2} fill={INK}/>
  <path d="M-94 76 H99 M-94 100 H66 M-94 122 H95" stroke={BLUE} strokeWidth="3" opacity=".35" />
  <MeasuredSvgText text={planned?(focus==='list'?'BILL / DUE DATE':'KNOWN DUE DATE'):'ESSENTIAL NEED'} x={0} y={165} width={256} maxSize={25} minSize={21} maxLines={1} weight={700} fill={INK}/>
</g>;

export const ReserveDrawing: React.FC<{id:string}> = ({id}) => <g data-wb-object="reserve-envelope" stroke={INK} strokeWidth="3">
  <path d="M-163 -96 L-153 -119 H141 L164 -96 V113 Q164 129 146 129 H-146 Q-163 129 -163 112Z" fill={`url(#${id}-paper)`}/>
  <path d="M-163 -92 L0 31 L164 -92" fill="#DDCEB0"/><path d="M-163 120 L-45 11 M163 120 L45 11" fill="none" stroke={BLUE} opacity=".75"/>
  <path d="M-160 -90 L-1 21 L158 -90" fill="none" stroke={GOLD} strokeWidth="2"/><path d="M-144 109 L-144 116 H145" fill="none" opacity=".25"/>
  <circle cy="44" r="34" fill={GOLD}/><text x="0" y="57" textAnchor="middle" fill={INK} stroke="none" fontWeight="900" fontSize="37">?</text>
  <path d="M-101 -112 H90 M-96 -106 H61" stroke={BLUE} strokeWidth="1" opacity=".18"/>
</g>;

export const GoalDrawing: React.FC<{id:string}> = ({id}) => <g data-wb-object="future-goal-notebook" stroke={INK} strokeWidth="3">
  <path d="M-142 -131 H127 Q146 -131 146 -109 V132 H-142Z" fill={BLUE}/><path d="M-119 -118 H128 V112 H-119Z" fill={`url(#${id}-paper)`}/>
  <path d="M-113 119 H123 M-99 124 H123" fill="none" stroke={PAPER} opacity=".65"/>
  <path d="M-91 77 L-20 -16 L9 25 L42 -49 L99 77Z" fill="#C9C3AA" stroke={BLUE}/><path d="M42 -48 V-85 L84 -71 L42 -56" fill={GOLD}/>
  <path d="M-67 77 Q-42 56 -10 65 Q22 67 42 39" fill="none" stroke={GOLD} strokeWidth="4"/>
  <path d="M-81 -91 H-29 M-81 -73 H7" fill="none" stroke={BLUE} opacity=".3"/><path d="M-119 -117 V113" fill="none" stroke={GOLD} strokeWidth="5"/>
</g>;
