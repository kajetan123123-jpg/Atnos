# Wealth Blueprint DataViz

Native **vector** charts for Remotion — never AI bitmaps.

## Types

`BAR_CHART` · `LINE_CHART` · `AREA_CHART` · `DONUT_CHART` · `COMPARISON` · `TIMELINE` · `MONEY_COUNTER` · `PERCENTAGE_COUNTER` · `COMPOUND_GROWTH` · `CASHFLOW` · `PORTFOLIO_ALLOCATION`

## Example

```ts
{
  type: 'BAR_CHART',
  title: 'Monthly Cash Flow',
  data: [
    {label: 'Income', value: 8000},
    {label: 'Expenses', value: 6200},
  ],
}
```

## Formatter

`formatNumber(1000)` → `1K` · `formatNumber(1_000_000)` → `1M` · `mode: 'full'` for exact values.

## Demo

```bash
npm run render:dataviz
```

Output: `demos/dataviz-showcase/wealth_blueprint_dataviz_showcase.mp4` (1080×1920@30).
