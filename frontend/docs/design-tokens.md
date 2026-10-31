# ATNOS Design Token Contract

`src/styles/tokens.css` jest jedynym miejscem z literalnymi wartościami kolorów, glow, typografii, spacingu, radiusów, duration i z-indexów.

## Rodziny tokenów

- Kolory tła:
  - `bg-void` tylko dla application root i scrimów.
  - `bg-shell` tylko dla chrome shella.
  - `bg-panel` tylko dla paneli treściowych.
  - `bg-elevated` tylko dla hover, selected i active state.
- Akcenty:
  - `accent-primary` (#ff4f00) — CTA, focus, active, user bubbles, live glow.
  - `accent-secondary` — ciepły wariant akcentu (highlight / secondary CTA).
  - `accent-on-primary` — tekst na wypełnionym pomarańczowym tle.
- Semantyka statusów:
  - `sem-success`, `sem-warning`, `sem-error` tylko dla statusów systemowych.
- Typografia:
  - `display`, `h1`, `h2`, `h3`, `body-lg`, `body`, `body-sm`, `label`, `mono-sm`.
- Spacing:
  - tylko `4, 8, 12, 16, 24, 32, 48, 64`.
- Motion:
  - `ease-*` i `dur-*` są jedynym źródłem dla timing functions i durations.

## Reguły

- Nie używaj literalnych `hex`, `rgba`, `rgb`, `hsl` ani arbitralnych klas typu `bg-[#...]`, `text-[#...]`, `shadow-[...]` poza `tokens.css`.
- Nie używaj dekoracyjnych gradientów jako ornamentu produktu. Gradient może istnieć tylko wtedy, gdy niesie konkretną funkcję i przechodzi osobny review.
- Nie dodawaj przypadkowych glow. Dozwolone są wyłącznie glow z kontraktu tokenów.
- Nie używaj off-scale spacingu poza skalą `4, 8, 12, 16, 24, 32, 48, 64`.
- Nie wpisuj ręcznie typograficznych wartości w krytycznych shellowych komponentach. Używaj utility classes opartych o tokeny.
- `bg-void` jest tylko dla root/background i scrimów. Nie używaj go jako karty.
- `bg-elevated` jest tylko dla hover/selected/active state, nigdy jako stanu spoczynkowego panelu.
- Z-indexy mogą pochodzić tylko z kontraktu shellowego: `page`, `hud`, `sidebar-drawer`, `pipeline-sheet`, `approval`, `toast`, `palette`, `modal`.

## Typografia

- `Chakra Petch` na nazwy, nagłówki i `label`.
- `Sora` dla tekstu kierowanego do człowieka.
- `JetBrains Mono` dla danych systemowych, modeli, hashy i telemetrii.
- `mono-sm` nie jest stylem dekoracyjnym. Używaj go wyłącznie dla stringów emitowanych przez system.
- `.type-label` jest kanonicznym wzorcem dla shell headers typu `MODEL`, `VRAM`, `NETWORK`: uppercase, 11/16, tracking `0.12em`.

## Ruch

- Animacja ma odzwierciedlać stan systemu, nie dekorację.
- `prefers-reduced-motion` wyłącza ruch i skraca przejścia do natychmiastowych zmian.

## Manualny Check Fontów

- Sprawdź na żywym renderze string: `Zażółć gęślą jaźń. Łódź. Pchnąć w tę łódź jeża lub ośm skrzyń fig.`
- Zweryfikuj go osobno dla `Chakra Petch`, `Sora` i `JetBrains Mono`.
- Jeśli którykolwiek font gubi diakrytyki albo fallbackuje do innej rodziny, traktuj to jako błąd kontraktu.
# Centrum asystenta

Widok `/assistant` używa istniejących powierzchni i typografii oraz tokenów
`--assistant-accent`, `--assistant-accent-dim`, `--assistant-note`, `--assistant-grid`.
Miętowy akcent oznacza elementy pamięci i połączenie; fioletowy odróżnia źródła
Obsidiana. Graf pokazuje wyłącznie zapisane przypisania dokumentów do tematów.
Nie sugeruje semantycznych relacji, których indeks nie przechowuje. Ruch rdzenia
uruchamia się przy rzeczywistym stanie thinking/executing, respektuje reduced
motion i low-perf. Panel głosowy otwiera istniejący przepływ; nie włącza mikrofonu.
