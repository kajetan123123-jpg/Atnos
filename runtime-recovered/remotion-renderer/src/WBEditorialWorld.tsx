import React from 'react';
import {CarDrawing, INK, PAPER, GOLD} from './ReserveObjects';

/** Original layered vector candidate, awaiting aesthetic acceptance. */
export const AlexEditorial: React.FC<{look:number;id?:string}> = ({look,id='alex'}) => <g data-wb-character="alex-vector-candidate">
  <defs>
    <linearGradient id={`${id}-skin`} x1="0" y1="0" x2="1" y2=".7"><stop stopColor="#F6D0A3"/><stop offset=".48" stopColor="#E9B58D"/><stop offset="1" stopColor="#B9755D"/></linearGradient>
    <linearGradient id={`${id}-jacket`} x1="0" y1="0" x2="1" y2=".4"><stop stopColor="#527F91"/><stop offset=".4" stopColor="#315368"/><stop offset="1" stopColor="#192D40"/></linearGradient>
  </defs>
  <ellipse cx="74" cy="548" rx="99" ry="15" fill={INK} opacity=".24"/>
  <g data-wb-layer="legs">
    <path d="M6 286 Q34 294 62 289 L51 402 Q45 453 25 527 L-18 521 L8 396Z" fill="#243A48"/>
    <path d="M64 289 L117 284 Q126 340 114 398 L139 518 L94 528 L69 411Z" fill="#314A58"/>
    <path d="M65 312 Q92 349 99 392 L124 513 L103 516 L78 412Z" fill="#1B2B3C"/>
    <path d="M12 309 L22 391 L12 491 M82 310 L94 405 L91 499" fill="none" stroke="#506271" strokeWidth="5"/>
    <path d="M-6 512 L39 514 L51 534 Q50 548 24 548 H-28 Q-39 541 -26 531Z M77 512 L117 512 L134 533 Q136 545 119 548 H70 L65 539Z" fill={PAPER}/>
    <path d="M-30 543 H44 M69 543 H130" stroke="#A8B4B0" strokeWidth="5"/>
    <path d="M2 526 L25 527 M83 526 L105 526" stroke="#426579" strokeWidth="3"/>
  </g>
  <g data-wb-layer="torso">
    <path d="M36 119 Q-12 126 -22 163 Q-15 235 -5 292 Q62 310 124 287 L142 166 Q128 137 97 119Z" fill={`url(#${id}-jacket)`}/>
    <path d="M34 124 L60 149 L85 126 L104 293 L34 296Z" fill={PAPER}/>
    <path d="M23 130 L52 146 L40 183 L17 165 L11 291 H-5 L-14 170Z" fill="#254254"/>
    <path d="M89 129 L65 146 L81 183 L103 165 L116 289 H128 L137 166Z" fill="#3B6074"/>
    <path d="M13 191 L34 191 L33 215 Q20 223 11 215Z" fill="#3E6478"/>
    <path d="M12 192 L34 192 M107 190 L125 190 M11 281 L24 282 M114 282 L122 282" stroke="#81969C" strokeWidth="2"/>
    {[191,225,260].map(y=><circle key={y} cx="105" cy={y} r="2.8" fill={GOLD}/>)}
    <path d="M49 167 Q65 178 80 165" fill="none" stroke="#D6CAB8" strokeWidth="3"/>
    <path d="M36 183 Q40 223 35 269 L72 281 L82 273 Q54 240 55 185Z" fill="#D5C7AF" opacity=".65"/>
    <path d="M-11 158 Q-3 136 22 132 M1 237 L7 266 M115 251 Q112 269 109 278" fill="none" stroke="#81A5AE" strokeWidth="3" strokeLinecap="round" opacity=".6"/>
    <path d="M16 228 L31 222 M105 237 L119 230 M14 267 L30 273" fill="none" stroke="#152B3D" strokeWidth="4" strokeLinecap="round"/>
  </g>
  <g data-wb-layer="head" transform={`rotate(${7+look*5} 64 125)`}>
    <path d="M40 101 L40 131 Q65 151 86 129 L83 94Z" fill="#CE9574"/>
    <path d="M31 27 Q64 8 96 37 Q107 59 99 82 L108 91 L96 97 Q95 115 75 126 Q53 127 35 107 Q25 84 24 61Z" fill={`url(#${id}-skin)`}/>
    <path d="M35 83 Q46 91 61 88 Q63 104 76 109 L95 102 Q93 118 75 126 Q51 125 35 107Z" fill="#AF715D" opacity=".28"/>
    <path d="M34 42 Q58 28 75 39 L69 62 Q51 63 39 76Z" fill="#FFE0B7" opacity=".55"/>
    <path d="M27 68 Q12 54 16 77 Q20 92 32 86" fill="#DEA380"/>
    <path d="M22 74 L29 78" fill="none" stroke="#B67962" strokeWidth="3"/>
    <path d="M25 72 Q8 52 17 35 L9 27 L28 26 Q21 5 47 9 Q65 -8 89 9 L108 4 L101 18 Q121 28 106 51 L95 34 Q79 47 59 39 Q43 39 37 58 L35 78Z" fill="#252B32"/>
    <path d="M24 30 Q44 12 69 20 Q83 26 99 19 L87 32 Q58 20 34 43Z" fill="#5E5147"/>
    <path d="M32 27 Q52 15 68 20 M30 45 Q48 30 61 31" fill="none" stroke="#BA9571" strokeWidth="2.5" strokeLinecap="round"/>
    <path d="M43 62 Q52 57 63 61 M77 62 Q85 58 94 64" fill="none" stroke="#302C2D" strokeWidth="4" strokeLinecap="round"/>
    <path d="M43 72 Q53 65 64 72 Q55 80 45 76Z M77 74 Q86 69 94 75 L91 79 Q83 80 77 74Z" fill="#FFF2DD"/>
    <ellipse cx={58+look} cy="73" rx="3.3" ry="4" fill={INK}/><ellipse cx={89+look} cy="75" rx="2.8" ry="3.7" fill={INK}/>
    <path d="M74 72 Q73 86 83 89 L76 93" fill="none" stroke="#AD6B56" strokeWidth="2.5" strokeLinecap="round"/>
    <path d="M58 105 Q73 101 85 105" fill="none" stroke="#725042" strokeWidth="2.5" strokeLinecap="round"/>
    <path d="M63 110 L77 110" stroke="#FFE0B7" strokeWidth="2" strokeLinecap="round"/>
    <path d="M42 98 Q54 114 69 116 Q83 114 94 100" fill="none" stroke="#7A5B4B" strokeWidth="5" opacity=".32"/>
  </g>
  <g data-wb-action="inspect-phone" transform={`rotate(${-5+look*3} 129 174)`}>
    <path d="M126 145 Q153 151 162 181 Q168 205 151 234 Q121 249 79 219 L90 191 L128 204 L131 174Z" fill={`url(#${id}-jacket)`}/>
    <path d="M147 178 Q153 203 138 224 L106 220" fill="none" stroke="#6B94A3" strokeWidth="4" strokeLinecap="round"/>
    <path d="M131 206 L115 205 L109 235 L127 239Z" fill="#254254"/>
    <path d="M94 192 Q75 168 63 175 L55 188 Q56 207 80 218 L91 216Z" fill="#E9B58D"/>
    <rect x="47" y="153" width="40" height="62" rx="8" fill={INK} transform="rotate(-12 67 184)"/>
    <rect x="52" y="159" width="30" height="49" rx="4" fill="#9BC6C5" transform="rotate(-12 67 184)"/>
    <path d="M75 188 Q64 178 60 185 Q57 189 72 199 L85 207" fill="#E9B58D"/>
    <path d="M65 185 L72 190" fill="none" stroke="#BD8267" strokeWidth="2"/>
    <circle cx="125" cy="222" r="10" fill={INK}/><circle cx="125" cy="222" r="7" fill={PAPER}/>
    <path d="M125 217 V222 L129 224" fill="none" stroke={INK} strokeWidth="1.5"/>
  </g>
  <path data-wb-layer="resting-arm" d="M-16 151 Q-42 166 -41 206 L-32 267 L-10 262 L-15 200 L3 179Z" fill="#254254"/>
  <path d="M-32 255 L-11 253 L-7 281 Q-10 298 -21 289 L-27 276 L-29 291 Q-40 283 -32 255Z" fill="#DDA27E"/>
</g>;

export const EditorialGarage: React.FC<{id:string;progress:number}> = ({id,progress}) => <g data-wb-world="editorial-garage-v1">
  <defs>
    <linearGradient id={`${id}-wall`} x2="1" y2=".5"><stop stopColor="#416D78"/><stop offset="1" stopColor="#142A3D"/></linearGradient>
    <linearGradient id={`${id}-floor`} x2=".2" y2="1"><stop stopColor="#668B81"/><stop offset="1" stopColor="#213D49"/></linearGradient>
    <linearGradient id={`${id}-window`} x2="0" y2="1"><stop stopColor="#FFF1D0"/><stop offset="1" stopColor="#C7C5A1"/></linearGradient>
    <radialGradient id={`${id}-pool`}><stop stopColor="#F3D49B" stopOpacity=".28"/><stop offset="1" stopColor="#F3D49B" stopOpacity="0"/></radialGradient>
  </defs>
  <path d="M0 76 Q452 37 936 76 V696 H0Z" fill={`url(#${id}-wall)`}/>
  <path d="M0 596 L936 582 V860 H0Z" fill={`url(#${id}-floor)`}/>
  <path d="M0 596 L118 538 V75 L0 76Z" fill="#132D3E" opacity=".4"/>
  <path d="M0 860 L454 595 M350 860 L539 594 M936 822 L614 590 M0 764 L936 730" fill="none" stroke="#96B2A1" strokeWidth="2" opacity=".18"/>
  <ellipse cx="603" cy="679" rx="323" ry="167" fill={`url(#${id}-pool)`}/>
  <path d="M0 599 L936 585" stroke="#80A09A" strokeWidth="5"/>
  <path d="M38 104 H413 V508 H38Z" fill="#1B3443"/>
  {[162,222,282,342,402,462].map(y=><path key={y} d={`M44 ${y} H408`} stroke="#3F5B68" strokeWidth="7"/>)}
  <path d="M449 111 H642 V347 H449Z" fill="#9BB8B3"/>
  <path d="M457 122 H635 V337 H457Z" fill={`url(#${id}-window)`}/>
  <path d="M465 337 V277 L486 255 L510 267 V337 M567 337 V248 L590 229 L619 259 V337" fill="#648584" opacity=".32"/>
  <path d="M546 120 V341 M452 229 H637" stroke="#456775" strokeWidth="10"/>
  <path d="M474 121 L579 337 H613 L505 121Z" fill={PAPER} opacity=".32"/>
  <path d="M495 346 L726 701 L385 716 L445 348Z" fill={GOLD} opacity=".09"/>
  <path d="M718 110 H894 V388 H718Z" fill="#1B3443"/>
  <path d="M725 117 H888 V381 H725Z" fill="#325769"/>
  {[0,1,2,3,4,5].map(y=><g key={y}>{[0,1,2,3,4,5].map(x=><circle key={x} cx={736+x*27} cy={134+y*39} r="2" fill="#829591" opacity=".5"/>)}</g>)}
  <path d="M756 158 V300 M789 155 L814 208 L793 244 M852 163 V279" fill="none" stroke={GOLD} strokeWidth="10" strokeLinecap="round"/>
  <path d="M748 157 L755 174 L765 155 M842 165 H862 V187 H843Z" fill="none" stroke={GOLD} strokeWidth="7"/>
  <path d="M743 457 H919 V592 H743Z" fill="#315568"/>
  <path d="M753 482 H909 M753 522 H909 M753 563 H909" stroke="#65868A" strokeWidth="3"/>
  <path d="M803 483 H853 M803 523 H853 M803 564 H853" stroke={GOLD} strokeWidth="5"/>
  <path d="M736 446 H926 V459 H736Z" fill="#CFA777"/>
  <path d="M782 391 H843 V445 H782Z" fill={GOLD}/><path d="M792 390 V373 H830 V390" fill="none" stroke={GOLD} strokeWidth="8"/>
  <path d="M686 73 V139 M689 131 Q724 130 731 158 H645 Q650 131 686 131Z" fill="#18313F" stroke="#83A09A" strokeWidth="3"/>
  <path d="M655 158 H720" stroke={PAPER} strokeWidth="6"/>
  <g transform="translate(-12 307) scale(.80)"><CarDrawing id={id}/></g>
  <path d="M163 690 H486 M176 698 H503" stroke="#9DB4A7" strokeWidth="3" opacity=".3"/>
  <path d="M719 716 L888 817 Q775 839 678 782 L618 718Z" fill="#142939" opacity=".35"/>
  <g transform="translate(665 173) scale(1.01)"><AlexEditorial id={id} look={progress}/></g>
</g>;
