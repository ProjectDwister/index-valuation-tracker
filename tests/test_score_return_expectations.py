import unittest

import pandas as pd

from score_return_expectations import build_score_return_expectations, historical_scores


class ScoreReturnExpectationTests(unittest.TestCase):
    def test_historical_score_uses_only_prior_pe_in_same_regime(self):
        d = pd.DataFrame({
            "Date": ["2019-03-31", "2019-06-30", "2019-09-30", "2021-03-31", "2021-06-30"],
            "Regime": ["Standalone"] * 3 + ["Consolidated"] * 2,
            "PE": [10, 20, 100, 50, 60],
            "EPS_Growth_YoY": [0, 0, 0, 0, 0],
        })
        scored = historical_scores(d, lambda _: 20, min_expanding_obs=2)
        scores = scored["Historical_Composite_Score"].tolist()
        self.assertTrue(pd.isna(scores[0]))
        self.assertAlmostEqual(scores[1], .70 * 25 + .30 * 20)
        self.assertAlmostEqual(scores[2], .70 * (100 / 6) + .30 * 20)
        self.assertTrue(pd.isna(scores[3]))
        self.assertAlmostEqual(scores[4], scores[1])

    def test_nearest_matured_quarters_and_minimum_sample(self):
        d = pd.DataFrame({
            "Date": pd.date_range("2000-03-31", periods=10, freq="QE"),
            "Composite_Score": [80, 81, 82, 83, 84, 85, 86, 87, 90, 10],
            "Fwd_1Y": [.10, .20, .30, .40, .50, .60, .70, .80, None, .99],
            "Fwd_3Y": [.01, .02, .03, .04, None, None, None, None, None, None],
        })
        result = build_score_return_expectations(d, 80, lambda _: 20)
        one = result["horizons"]["1"]
        self.assertEqual(one["n"], 8)
        self.assertAlmostEqual(one["median_cagr"], .45)
        self.assertEqual(one["score_min"], 80)
        self.assertEqual(one["score_max"], 87)
        three = result["horizons"]["3"]
        self.assertEqual(three["n"], 4)
        self.assertIsNone(three["median_cagr"])

    def test_canonical_score_is_preserved(self):
        d = pd.DataFrame({
            "Date": ["2000-03-31"], "Composite_Score": [73], "Fwd_1Y": [.15]
        })
        scored = historical_scores(d, lambda _: 20)
        self.assertEqual(scored["Historical_Composite_Score"].iloc[0], 73)


if __name__ == "__main__":
    unittest.main()
