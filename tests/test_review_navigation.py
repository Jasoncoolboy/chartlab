"""Shared navigation adapter and explicit review-theme regressions."""
import tempfile
import unittest
from pathlib import Path
from tests.test_viewer import chart, BROWSER, _walk, M15, run_page


class TestReviewTheme(unittest.TestCase):
    def test_review_theme_is_explicit(self):
        spec = chart.spec('EURUSD', {'M15': _walk(100, M15)}, default_tf='M15')
        with tempfile.TemporaryDirectory() as tmp:
            normal = chart.render(
                spec, Path(tmp)/'normal.html', inline_lib=True
            ).read_text(encoding='utf-8')
            spec['reviewPresentation'] = True
            review = chart.render(
                spec, Path(tmp)/'review.html', inline_lib=True
            ).read_text(encoding='utf-8')
        self.assertNotIn('review-presentation-v1', normal)
        self.assertIn('review-presentation-v1', review)
        self.assertIn('reviewLabels.some', review)
        self.assertIn('fixRightEdge:false,fixLeftEdge:false', normal)
        self.assertIn('fixRightEdge:false,fixLeftEdge:false', review)

    @unittest.skipUnless(BROWSER, 'headless browser unavailable')
    def test_navigation_adapter_and_free_edges_on_both_templates(self):
        for review in (False, True):
            with (self.subTest(review=review),
                  tempfile.TemporaryDirectory() as tmp):
                spec = chart.spec(
                    'EURUSD', {'M15': _walk(100, M15)}, default_tf='M15')
                spec['reviewPresentation'] = review
                page = chart.render(
                    spec, Path(tmp)/'chart.html', inline_lib=True)
                r = run_page(page, '''return (async()=>{
                  let picked=null;
                  ChartLab.navigation({timeframes:['M15','H1'],current:'M15',
                    onChange:tf=>picked=tf});
                  document.querySelector('[data-navigation-tf=H1]').click();
                  const ts=ChartLab.api().chart.timeScale();
                  ts.setVisibleLogicalRange({from:80,to:125});
                  await ChartLab.frame();
                  const right=ts.getVisibleLogicalRange();
                  ts.setVisibleLogicalRange({from:-20,to:20});
                  await ChartLab.frame();
                  return {picked,right,left:ts.getVisibleLogicalRange(),
                    data:ChartLab.api().series.data()[0],
                    grid:ChartLab.api().chart.options().grid};
                })()''')
                self.assertEqual(r['picked'], 'H1')
                self.assertGreater(r['right']['to'], 110)
                self.assertLess(r['left']['from'], -10)
                self.assertEqual(r['data']['close'], 1.1002)
                if review:
                    self.assertFalse(r['grid']['vertLines']['visible'])


if __name__ == '__main__':
    unittest.main()
