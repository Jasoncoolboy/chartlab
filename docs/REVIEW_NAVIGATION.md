# Review navigation and presentation (2026-10-27)

The viewer permits panning beyond either data edge. Its toolbar wraps at narrow widths.
Embedded single-timeframe pages can use `ChartLab.navigation({timeframes, current, onChange})`
to show the host's complete timeframe set inside the viewer. The host callback must capture
and restore its navigation anchor. `ValkNavigationActive` retains compatibility with the
existing engulfingCore bridge that takes ownership from initial-fit retries.

Set `reviewPresentation: true` in a chart spec for the reusable review theme: hidden grid,
timeframe colours, lighter/thinner zones, collision-aware labels and short entry segments.
The alternate asset `assets/review_viewer.html` is also available through `viewer=` or the CLI.
The default viewer retains normal presentation. The review theme reads timeframe tokens in
zone labels; explicit pending opacity0 remains an outline. Screenshot references are approximate
and their placement must come from the producer's evidence. No trading logic is implemented.

Both templates share the same navigation fix. Verify both when changing shared navigation.
