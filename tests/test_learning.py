import tempfile
import unittest
from pathlib import Path
from discord_archive.learning import train, predict, save_model, load_model


class LearningTests(unittest.TestCase):
    def test_cold_start_and_single_class_require_review(self):
        self.assertTrue(predict('수학', {}, None).review_required)
        model = train([('수학 공부', '10')], 'v1')
        self.assertEqual(predict('수학 공부', {}, model).channel_id, '10')
        self.assertTrue(predict('수학 공부', {}, model).review_required)

    def test_corrections_and_new_categories_change_recommendation(self):
        model = train([('numpy python 회귀분석', '10'), ('식품 영양 강의', '20')], 'v1')
        self.assertEqual(predict('numpy python 회귀분석', {}, model).channel_id, '10')
        revised = train([('numpy python 회귀분석', '30'), ('식품 영양 강의', '20')], 'v2')
        self.assertEqual(predict('numpy python 회귀분석', {}, revised).channel_id, '30')
        self.assertTrue(predict('완전히 새로운 주제', {}, revised).review_required)

    def test_repeated_identical_examples_do_not_unlock_automation(self):
        model = train([('수학', '10')] * 100 + [('영양', '20')] * 100, 'v1')
        self.assertTrue(predict('수학', {}, model).review_required)

    def test_json_roundtrip_and_corrupt_model_fallback(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'model.json'
            save_model(train([('Python 코드', '10'), ('식품 영양', '20')], 'v1'), p)
            self.assertEqual(predict('Python 코드', {}, load_model(p)).channel_id, '10')
            p.write_text('broken', encoding='utf-8')
            self.assertIsNone(load_model(p))

