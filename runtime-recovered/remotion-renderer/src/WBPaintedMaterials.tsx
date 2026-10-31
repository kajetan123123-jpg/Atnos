import React from 'react';

/** Fixed pigments and marks in world coordinates: they never regenerate with the frame. */
export const WBPaintedDefinitions:React.FC<{id:string}>=({id})=><>
  <filter id={id+'-brush-edge'} x="-10%" y="-10%" width="120%" height="120%" colorInterpolationFilters="sRGB">
    <feTurbulence type="fractalNoise" baseFrequency=".017 .071" numOctaves={3} seed={17} result="brush"/>
    <feDisplacementMap in="SourceGraphic" in2="brush" scale={3.4} xChannelSelector="R" yChannelSelector="G"/>
  </filter>
  <filter id={id+'-pigment'} x="0" y="0" width="100%" height="100%" colorInterpolationFilters="sRGB">
    <feTurbulence type="fractalNoise" baseFrequency=".18 .27" numOctaves={3} seed={53}/>
    <feColorMatrix type="saturate" values="0"/>
    <feComponentTransfer><feFuncA type="linear" slope="0" intercept="1"/></feComponentTransfer>
  </filter>
  <filter id={id+'-wash'} x="-10%" y="-10%" width="120%" height="120%"><feGaussianBlur stdDeviation="7"/></filter>
  <filter id={id+'-pigment-pool'} x="0" y="0" width="100%" height="100%" colorInterpolationFilters="sRGB">
    <feTurbulence type="fractalNoise" baseFrequency=".009 .038" numOctaves={2} seed={91}/>
    <feColorMatrix type="saturate" values="0"/>
  </filter>
  <clipPath id={id+'-paper-material'}><path d="M177 1058 L475 963 Q536 951 550 990 Q581 954 643 989 L952 1093 L880 1439 Q693 1369 521 1418 Q350 1311 168 1300Z"/></clipPath>
  <clipPath id={id+'-wood-material'}><path d="M-83 985 Q429 828 1164 1058 L1300 2400 H-220Z"/></clipPath>
  <clipPath id={id+'-wall-material'}><path d="M0 0 H1080 V920 Q462 789 0 985Z"/></clipPath>
  <clipPath id={id+'-sky-material'}><path d="M603 420 H971 V923 H603Z"/></clipPath>
</>;

/** Local color washes, directional dry-brush strokes and paper fibres; not a screen noise veil. */
export const WBPaintedMaterials:React.FC<{id:string;surface?:'wall'|'sky'|'wood'|'paper'}>=({id,surface})=><g data-wb-material="fixed-gouache-pigments" data-wb-surface={surface??'all'} pointerEvents="none">
  {(!surface || surface==='wall')?<g clipPath={'url(#'+id+'-wall-material)'}>
    <g data-wb-paint-technique="wall-pigment-pooling">
      <path d="M-40 272 Q127 236 314 309 L329 456 Q166 392 -36 434Z" fill="#5B7580" opacity=".24" filter={'url(#'+id+'-wash)'}/>
      <path d="M332 303 Q469 253 571 325 L560 632 Q471 656 347 587Z" fill="#20264D" opacity=".42"/>
      <path d="M-34 571 Q94 534 237 588 L242 839 Q113 783 -33 850Z" fill="#467887" opacity=".27"/>
      <path d="M348 666 Q430 637 548 691 L542 857 Q442 818 350 865Z" fill="#8A958B" opacity=".12"/>
      <path d="M32 395 L39 548 M53 429 L59 576 M346 355 L350 520 M369 396 L374 558 M469 683 L475 807" stroke="#D2CBA5" strokeWidth="5" strokeDasharray="53 9 19 12 36" fill="none" opacity=".12"/>
    </g>
    <path d="M-30 431 Q229 365 511 458 Q780 365 1114 494 L1102 587 Q772 481 504 560 Q205 462 -26 526Z" fill="#CED7CE" opacity=".09" filter={'url(#'+id+'-wash)'}/>
    <path d="M-25 541 Q362 484 742 570 L1038 631 L1051 649 Q609 564 171 602 L-23 624Z" fill="#93B7B1" opacity=".08"/>
    <path d="M-50 679 Q409 582 1121 743 L1098 783 Q415 642 -21 723Z" fill="#101F42" opacity=".12"/>
    <path d="M24 742 Q220 717 466 753 M254 868 Q526 814 796 878 M785 520 Q962 549 1098 536" fill="none" stroke="#D1D8C6" strokeWidth="4" opacity=".07" strokeLinecap="round"/>
  </g>:null}
  {(!surface || surface==='sky')?<g clipPath={'url(#'+id+'-sky-material)'}>
    <g data-wb-paint-technique="layered-sky-wash">
      <path d="M586 420 Q726 451 799 423 Q880 401 989 433 L995 477 Q900 446 817 469 Q720 483 583 452Z" fill="#FFF5CF" opacity=".34"/>
      <path d="M587 564 Q665 533 745 563 L813 583 L891 573 L994 608 L995 647 Q895 602 818 614 Q730 585 684 588 L586 601Z" fill="#E4C7A4" opacity=".31"/>
      <path d="M615 628 Q741 597 846 648 M641 638 Q744 616 816 647 M843 623 L947 651" stroke="#FFF3D3" strokeWidth="9" strokeDasharray="41 8 67 12 25" opacity=".30" fill="none" strokeLinecap="round"/>
    </g>
    <path d="M580 470 Q766 458 1006 487 L1000 529 Q766 484 580 506Z" fill="#FFF1CA" opacity=".22"/>
    <path d="M580 580 Q692 551 804 594 Q914 611 1008 579 L1007 612 Q926 638 795 615 Q679 583 580 615Z" fill="#EFE0BC" opacity=".19"/>
    <path d="M621 652 Q804 675 1004 646 M581 689 Q789 714 1000 686 M719 456 Q825 444 967 469" stroke="#FFF4D8" strokeWidth="7" fill="none" opacity=".11" strokeLinecap="round"/>
  </g>:null}
  {(!surface || surface==='wood')?<g clipPath={'url(#'+id+'-wood-material)'}>
    <g data-wb-paint-technique="directional-desk-brushwork">
      <path d="M-65 1024 Q293 879 644 1011 L673 1044 Q306 935 -60 1091Z" fill="#7DB1A5" opacity=".34"/>
      <path d="M-61 1175 Q187 1075 396 1104 Q751 1135 1160 1278 L1153 1332 Q793 1196 399 1172 Q137 1151 -59 1245Z" fill="#3E9395" opacity=".33"/>
      <path d="M-36 1450 Q247 1302 590 1407 Q888 1514 1165 1433 L1159 1506 Q885 1572 579 1474 Q242 1375 -41 1514Z" fill="#0E455E" opacity=".30"/>
      <path d="M132 1137 Q552 1026 934 1235 Q1032 1335 902 1511 Q533 1587 179 1393Z" fill="#122A48" opacity=".28" filter={'url(#'+id+'-wash)'}/>
      <path d="M-17 1137 Q122 1090 232 1091 M-5 1154 Q99 1116 188 1115 M-21 1348 Q106 1305 212 1308 M37 1389 Q140 1356 238 1370 M803 1525 Q959 1568 1119 1512 M831 1545 Q980 1580 1094 1544" stroke="#A3C8B1" strokeWidth="7" strokeDasharray="59 8 31 6 83 12" opacity=".22" fill="none" strokeLinecap="round"/>
      <path d="M-7 1284 Q74 1258 140 1255 M784 1623 Q946 1669 1093 1611 M375 1642 Q494 1675 591 1671" stroke="#062E48" strokeWidth="12" strokeDasharray="71 11 35 13" opacity=".27" fill="none" strokeLinecap="round"/>
      <rect x="0" y="900" width="1080" height="1500" filter={'url(#'+id+'-pigment-pool)'} opacity=".13" style={{mixBlendMode:'multiply'}}/>
    </g>
    <path d="M-47 1119 Q348 1004 708 1108 Q864 1122 1164 1199 L1167 1231 Q919 1164 704 1159 Q341 1047 -47 1176Z" fill="#82BCAE" opacity=".18"/>
    <path d="M-63 1324 Q262 1191 595 1281 Q915 1378 1172 1339 L1177 1408 Q828 1431 578 1333 Q216 1271 -57 1396Z" fill="#063D59" opacity=".22"/>
    <path d="M-11 1523 Q417 1431 713 1542 Q936 1612 1129 1548 L1140 1561 Q922 1640 694 1581 Q394 1466 -26 1561Z" fill="#88BDAD" opacity=".13"/>
    <path d="M37 1049 Q273 993 439 1018 M-17 1213 Q71 1187 164 1199 M70 1480 Q240 1428 336 1440 M867 1536 Q989 1550 1140 1517" stroke="#CBE0BD" strokeWidth="3" strokeLinecap="round" fill="none" opacity=".17"/>
    <rect x="0" y="900" width="1080" height="1500" filter={'url(#'+id+'-pigment)'} opacity=".075" style={{mixBlendMode:'multiply'}}/>
  </g>:null}
  {(!surface || surface==='paper')?<g clipPath={'url(#'+id+'-paper-material)'}>
    <g data-wb-paint-technique="paper-volume-and-drybrush">
      <path d="M182 1086 Q320 1013 469 982 L496 1028 Q332 1072 181 1154Z" fill="#FFFBE5" opacity=".63"/>
      <path d="M181 1170 Q291 1123 445 1157 L470 1199 Q318 1176 181 1239Z" fill="#DCC496" opacity=".17"/>
      <path d="M184 1275 Q352 1301 491 1389 L502 1416 Q328 1331 177 1310Z" fill="#A48C62" opacity=".29"/>
      <path d="M479 1002 Q486 1134 479 1344 L502 1398 L518 1394 Q501 1170 526 997Z" fill="#B4935D" opacity=".25" filter={'url(#'+id+'-wash)'}/>
      <path d="M575 1005 Q647 996 915 1115 L915 1141 Q714 1062 590 1051Z" fill="#FFFBE4" opacity=".68"/>
      <path d="M581 1168 Q709 1163 905 1231 L887 1284 Q700 1222 584 1226Z" fill="#D2B985" opacity=".15"/>
      <path d="M570 1391 Q712 1350 868 1407 L879 1440 Q724 1397 553 1427Z" fill="#A78E62" opacity=".27"/>
      <path d="M208 1117 Q296 1071 407 1044 M227 1131 Q323 1089 435 1062 M613 1060 Q740 1090 883 1153 M627 1079 Q731 1104 852 1158 M207 1285 Q326 1311 417 1353 M646 1375 Q734 1377 838 1408" stroke="#FFFEF0" strokeWidth="6" strokeDasharray="39 7 68 11 24" opacity=".40" fill="none" strokeLinecap="round"/>
      <path d="M227 1269 Q339 1296 444 1354 M664 1354 Q749 1360 848 1397" stroke="#B69D72" strokeWidth="4" strokeDasharray="25 6 54 9" opacity=".25" fill="none"/>
      <rect x="150" y="945" width="820" height="525" filter={'url(#'+id+'-pigment-pool)'} opacity=".12" style={{mixBlendMode:'multiply'}}/>
    </g>
    <path d="M210 1090 Q363 1018 468 1005 L484 1018 Q352 1045 225 1119Z" fill="#FFFDF0" opacity=".64"/>
    <path d="M187 1267 Q364 1287 495 1376 L491 1407 Q355 1307 177 1304Z" fill="#B99F72" opacity=".20"/>
    <path d="M585 1006 Q746 1038 915 1118 L906 1134 Q723 1062 584 1029Z" fill="#FFFDF0" opacity=".56"/>
    <path d="M856 1321 L864 1421 Q722 1382 576 1402 L562 1382 Q712 1364 856 1321Z" fill="#BCA076" opacity=".16"/>
    <path d="M526 1023 Q514 1199 515 1388 L533 1405 Q530 1196 544 1012Z" fill="#A88957" opacity=".18" filter={'url(#'+id+'-wash)'}/>
    <path d="M211 1216 Q293 1192 395 1224 M625 1110 Q746 1135 863 1174 M234 1291 Q347 1300 454 1357 M621 1325 Q744 1342 839 1391" stroke="#C9B792" strokeWidth="2" opacity=".22" fill="none" strokeLinecap="round"/>
    <rect x="150" y="945" width="820" height="525" filter={'url(#'+id+'-pigment)'} opacity=".085" style={{mixBlendMode:'multiply'}}/>
  </g>:null}
</g>;
