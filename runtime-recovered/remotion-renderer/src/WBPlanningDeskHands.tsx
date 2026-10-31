import React from 'react';

/** Reusable original WB gesture layers: functional hands, no arbitrary presenter portrait. */
export const WritingHand:React.FC<{x:number;y:number;opacity:number;painted?:boolean;id?:string}>=({x,y,opacity,painted=false,id='wb-writing-hand'})=><g
  data-wb-action="write-purpose-underline" opacity={opacity} transform={`translate(${x} ${y})`}>
  <path d="M-315 217 Q-230 88 -140 62 L-58 129 Q-169 213 -279 324Z" fill="#263555"/>
  {painted?<g data-wb-painted-volume="writing-sleeve">
    <defs><clipPath id={id+'-writing-sleeve'}><path d="M-315 217 Q-230 88 -140 62 L-58 129 Q-169 213 -279 324Z"/></clipPath></defs>
    <g clipPath={'url(#'+id+'-writing-sleeve)'}>
      <path d="M-329 222 Q-246 126 -157 95 L-123 123 Q-208 173 -291 302Z" fill="#526D87" opacity=".48"/>
      <path d="M-305 300 Q-215 185 -105 136 L-49 153 L-279 350Z" fill="#10213A" opacity=".5"/>
      <path d="M-304 225 Q-237 148 -161 113 M-286 238 Q-218 163 -148 132" fill="none" stroke="#7C8D9D" strokeWidth="5" opacity=".32" strokeDasharray="27 8 4 13" strokeLinecap="round"/>
      <path d="M-271 253 Q-218 195 -187 180 M-184 117 L-157 103" fill="none" stroke="#C4BBA4" strokeWidth="3" opacity=".25" strokeLinecap="round"/>
    </g>
  </g>:null}
  <path d="M-290 264 Q-215 155 -115 105" fill="none" stroke="#455778" strokeWidth="9"/>
  <path d="M-162 74 L-124 36 L-52 93 L-87 143Z" fill="#F2E5C9"/>
  <path d="M-143 59 L-88 119 M-129 47 L-75 104" stroke="#CFBD9C" strokeWidth="4"/>
  <path d="M-122 51 Q-91 12 -61 4 L-27 -17 Q-15 -28 -2 -20 L31 8 Q49 21 36 33 L5 55 Q-13 70 -24 91 L-60 108 L-111 66Z" fill="#EAB08F"/>
  <path d="M-78 18 Q-57 -2 -42 8 L-4 29 Q8 40 -4 49 L-36 38 L-64 53Z" fill="#F2C5A2"/>
  <path d="M-33 59 Q-8 26 11 20 L27 23 Q37 33 24 45 L5 62 L-13 85 L-47 96Z" fill="#C57D68"/>
  {painted?<g>
    <defs><clipPath id={id+'-skin'}><path d="M-122 51 Q-91 12 -61 4 L-27 -17 Q-15 -28 -2 -20 L31 8 Q49 21 36 33 L5 55 Q-13 70 -24 91 L-60 108 L-111 66Z"/></clipPath></defs>
    <g clipPath={'url(#'+id+'-skin)'}>
      <path d="M-131 30 Q-76 -4 -32 29 L-6 58 L-35 77 Q-83 42 -131 65Z" fill="#FFE0B4" opacity=".4"/>
      <path d="M-120 68 Q-76 70 -39 53 L-10 67 L-49 114 L-116 89Z" fill="#AD665D" opacity=".32"/>
      <path d="M-111 29 Q-80 15 -55 28 M-102 43 Q-79 31 -62 42 M-70 53 L-45 65" fill="none" stroke="#FFD5AE" strokeWidth="5" opacity=".54" strokeDasharray="19 6 4 9" strokeLinecap="round"/>
      <path d="M-121 32 Q-77 13 -38 40 L-14 66 L-34 78 Q-79 43 -121 63Z" fill="#FFE0B4" opacity=".14"/>
      <path d="M-90 80 Q-51 93 -24 71 M-68 23 L-32 46" fill="none" stroke="#BD7F68" strokeWidth="3" opacity=".17" strokeLinecap="round"/>
      <rect x="-125" y="-30" width="175" height="150" filter={'url(#'+id+'-pigment)'} opacity=".07" style={{mixBlendMode:'multiply'}}/>
    </g>
  </g>:null}
  <path d="M-64 6 L-31 -10 L3 15 M-83 18 L-41 44 M-105 44 L-72 66" stroke="#B47664" strokeWidth="3" fill="none" strokeLinecap="round"/>
  <path d="M-54 -82 L-45 -84 L6 0 L-4 5Z" fill="#D66B59"/>
  <path d="M-51 -81 L-48 -82 L1 1" stroke="#FFE4B4" strokeWidth="2"/>
  <path d="M-4 5 L1 15 L6 0Z" fill="#F5D6B2"/><path d="M1 15 L-1 10 L4 9Z" fill="#171A3B"/>
  <path d="M-18 6 Q-11 -7 -3 -3 L17 14 Q25 24 16 28 L5 25 L-8 14Z" fill="#F2C5A2"/>
  {painted?<g data-wb-painted-volume="writing-finger">
    <defs><clipPath id={id+'-writing-finger'}><path d="M-18 6 Q-11 -7 -3 -3 L17 14 Q25 24 16 28 L5 25 L-8 14Z"/></clipPath></defs>
    <g clipPath={'url(#'+id+'-writing-finger)'}>
      <path d="M-19 2 Q-5 -8 15 14" fill="none" stroke="#FFE2B9" strokeWidth="8" opacity=".48"/>
      <path d="M-9 17 L19 31" fill="none" stroke="#BC7A68" strokeWidth="5" opacity=".42"/>
    </g>
  </g>:null}
  <path d="M-117 35 L-130 51 L-88 88 L-77 72Z" fill="#171A3B"/>
  <ellipse cx="-104" cy="62" rx="17" ry="21" transform="rotate(-38 -104 62)" fill="#E2B369"/>
  <ellipse cx="-104" cy="62" rx="12" ry="16" transform="rotate(-38 -104 62)" fill="#FFF2D5"/>
  <path d="M-104 54 V62 L-97 65" stroke="#343265" strokeWidth="2" fill="none"/>
</g>;

export const AccountPhone:React.FC<{id:string;painted?:boolean}>=({id,painted=false})=><g data-wb-object="single-account-phone" transform="translate(865 716) rotate(-9)">
  <defs><linearGradient id={id+'-glass'} x2=".8" y2="1"><stop stopColor="#D9F1DE"/><stop offset="1" stopColor="#88CBBE"/></linearGradient></defs>
  <ellipse cx="26" cy="112" rx="105" ry="25" fill="#171A3B" opacity=".26"/>
  <path d="M283 218 Q170 79 86 53 L36 112 L192 289Z" fill="#263555"/>
  {painted?<g data-wb-painted-volume="holding-sleeve">
    <defs><clipPath id={id+'-holding-sleeve'}><path d="M283 218 Q170 79 86 53 L36 112 L192 289Z"/></clipPath></defs>
    <g clipPath={'url(#'+id+'-holding-sleeve)'}>
      <path d="M52 63 Q151 84 284 201 L263 234 Q147 120 58 103Z" fill="#60758D" opacity=".43"/>
      <path d="M42 120 Q147 184 196 302 L285 284 L112 115Z" fill="#12253F" opacity=".42"/>
      <path d="M85 76 Q156 107 227 173 M110 103 Q178 142 251 213" fill="none" stroke="#9BA6AD" strokeWidth="4" opacity=".28" strokeDasharray="23 8 7 13" strokeLinecap="round"/>
    </g>
  </g>:null}
  <path d="M214 227 Q156 158 90 105" fill="none" stroke="#526585" strokeWidth="6"/>
  <path d="M89 48 L121 77 L77 139 L39 109Z" fill="#F2E5C9"/>
  <path d="M93 66 Q74 12 55 2 L24 3 Q11 17 25 42 L33 68 L2 82 Q-6 104 16 123 L49 119 L76 89Z" fill="#EAB08F"/>
  {painted?<g data-wb-painted-volume="holding-hand">
    <defs><clipPath id={id+'-holding-hand'}><path d="M93 66 Q74 12 55 2 L24 3 Q11 17 25 42 L33 68 L2 82 Q-6 104 16 123 L49 119 L76 89Z"/></clipPath></defs>
    <g clipPath={'url(#'+id+'-holding-hand)'}>
      <path d="M15 8 Q63 -7 83 61 L48 87 L12 63Z" fill="#FFD7AE" opacity=".48"/>
      <path d="M4 89 Q44 111 84 65 L82 113 L20 138Z" fill="#B36D61" opacity=".35"/>
      <path d="M38 13 Q61 26 66 45 M19 93 Q37 104 53 96" fill="none" stroke="#F8D0A9" strokeWidth="5" opacity=".5" strokeDasharray="17 6 3 8" strokeLinecap="round"/>
    </g>
  </g>:null}
  <path d="M-78 -147 Q-77 -162 -60 -162 H67 Q84 -161 84 -144 V114 Q84 131 66 132 H-62 Q-81 131 -82 114Z" fill="#10182E"/>
  {painted?<g data-wb-painted-volume="phone-case">
    <defs><clipPath id={id+'-phone-case'}><path d="M-78 -147 Q-77 -162 -60 -162 H67 Q84 -161 84 -144 V114 Q84 131 66 132 H-62 Q-81 131 -82 114Z"/></clipPath></defs>
    <g clipPath={'url(#'+id+'-phone-case)'}>
      <path d="M-85 -154 Q-34 -176 81 -146 L80 -136 Q-27 -158 -72 -135 V114 L-86 123Z" fill="#668B8B" opacity=".52"/>
      <path d="M-67 125 Q15 140 77 112 V-143 L91 -150 V141 H-75Z" fill="#050D25" opacity=".52"/>
      <path d="M-75 -110 V-25 M-75 8 V89 M-52 -156 H14" fill="none" stroke="#A2B8A8" strokeWidth="3" opacity=".37" strokeDasharray="18 7 5 12" strokeLinecap="round"/>
    </g>
  </g>:null}
  <path d="M-72 -143 Q-73 -151 -61 -151 H66 Q73 -150 74 -140 V109 Q74 122 61 121 H-60 Q-70 123 -72 109Z" fill={'url(#'+id+'-glass)'}/>
  <rect x="-22" y="-151" width="49" height="11" rx="6" fill="#10182E"/>
  <path d="M-72 -106 L74 39 V83 L-72 -60Z" fill="#FFF2D5" opacity=".20"/>
  {painted?<g>
    <defs><clipPath id={id+'-painted-glass'}><path d="M-72 -143 Q-73 -151 -61 -151 H66 Q73 -150 74 -140 V109 Q74 122 61 121 H-60 Q-70 123 -72 109Z"/></clipPath></defs>
    <g clipPath={'url(#'+id+'-painted-glass)'}>
      <path d="M-82 -148 Q-15 -126 81 -91 L85 -57 Q-1 -94 -82 -106Z" fill="#F7E4BC" opacity=".32"/>
      <path d="M-80 -45 Q-12 -28 84 -7 V41 Q-1 24 -80 -10Z" fill="#77B5AD" opacity=".23"/>
      <path d="M59 -142 Q42 -24 79 119 L94 132 V-153Z" fill="#2B6F76" opacity=".23"/>
      <path d="M-69 -123 Q-34 -116 -12 -101 M-61 -105 L-38 -96 M30 54 L58 65" fill="none" stroke="#F5F0CA" strokeWidth="5" opacity=".46" strokeDasharray="25 6 4 9" strokeLinecap="round"/>
      <path d="M-96 -136 Q-14 -89 81 -59 L87 -31 Q-15 -60 -96 -101Z" fill="#D7E8C6" opacity=".22"/>
      <path d="M-84 38 Q-7 57 80 71 M-67 -138 L41 -95" fill="none" stroke="#5D9F92" strokeWidth="3" opacity=".13" strokeLinecap="round"/>
      <rect x="-75" y="-155" width="152" height="280" filter={'url(#'+id+'-pigment)'} opacity=".06" style={{mixBlendMode:'multiply'}}/>
    </g>
  </g>:null}
  <circle cx="1" cy="-77" r="28" fill="#18AAA6"/>
  <path d="M-14 -79 L1 -90 L17 -79 M-11 -76 V-63 M1 -76 V-63 M13 -76 V-63 M-16 -59 H19" stroke="#FFF2D5" strokeWidth="3" fill="none" strokeLinecap="round"/>
  <text x="1" y="-19" textAnchor="middle" fill="#171A3B" fontWeight="900" fontSize="19">ONE</text>
  <text x="1" y="10" textAnchor="middle" fill="#171A3B" fontWeight="900" fontSize="19">ACCOUNT</text>
  <path d="M-38 35 H38 M-28 55 H27" stroke="#343265" strokeWidth="3" opacity=".23"/>
  <rect x="-24" y="88" width="49" height="4" rx="2" fill="#171A3B" opacity=".3"/>
  <path d="M76 -41 Q92 -61 102 -41 L106 12 Q106 37 86 48 L74 24Z" fill="#F2C5A2"/>
  {painted?<g data-wb-painted-volume="phone-thumb">
    <defs><clipPath id={id+'-phone-thumb'}><path d="M76 -41 Q92 -61 102 -41 L106 12 Q106 37 86 48 L74 24Z"/></clipPath></defs>
    <g clipPath={'url(#'+id+'-phone-thumb)'}>
      <path d="M80 -42 Q91 -55 98 -28 L97 13 Q95 29 86 35" fill="none" stroke="#FFDFB7" strokeWidth="10" opacity=".47"/>
      <path d="M76 -26 Q78 12 91 36 L81 55 L67 23Z" fill="#B97968" opacity=".38"/>
      <path d="M86 -24 L89 -6 M91 8 L93 16" fill="none" stroke="#E3A384" strokeWidth="3" opacity=".55" strokeLinecap="round"/>
    </g>
  </g>:null}
  <path d="M83 -33 L85 17" stroke="#CC8C73" strokeWidth="3" fill="none" strokeLinecap="round"/>
</g>;
