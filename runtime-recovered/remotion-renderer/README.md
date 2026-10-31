# Wealth Blueprint Remotion Renderer

Deterministyczny renderer technicznego wariantu referencyjnego Wealth Blueprint V1.

- obsługuje wyłącznie `HookScene`, `ImageScene`, `NumberScene`, `ChartScene`, `CTAScene`;
- renderuje 1080x1920 przy 30 FPS;
- nie generuje treści ani danych liczbowych;
- odrzuca `NumberScene` i `ChartScene`, jeśli dane nie występują w manifeście;
- produkuje cichy H.264 MP4, który finalny pipeline miksuje z audio przez FFmpeg.

Nazwa `reference-v0.1` jest celowa. Nie jest to zatwierdzony przez użytkownika
`WEALTH_BLUEPRINT_STYLE_V1`.
