import datetime
from types import SimpleNamespace

from django.template.loader import get_template
from django.test import SimpleTestCase

from views.year_in_review import _build_slides


def _empty_data():
    return {
        'preg_records': [],
        'prenatals': [],
        'feelings': [],
        'baby_records': [],
        'milestones': [],
        'cares': [],
    }


class YearInReviewSlideTests(SimpleTestCase):
    """_build_slides 是純函式，不碰資料庫，用假物件驗證卡片組成邏輯。"""

    today = datetime.date(2026, 9, 22)

    def test_no_records_yields_cover_and_empty_only(self):
        slides = _build_slides(
            2026, _empty_data(), today=self.today, subject_name='小寶',
            preg_case=None, active_baby=None,
        )
        self.assertEqual([s['kind'] for s in slides], ['cover', 'empty'])
        self.assertEqual(slides[0]['title'], '2026 年度回顧')

    def test_full_year_builds_expected_slides_in_order(self):
        data = _empty_data()
        data['preg_records'] = [
            SimpleNamespace(check_date=datetime.date(2026, 3, 2), record='a', weight=50),
            SimpleNamespace(check_date=datetime.date(2026, 3, 9), record='b', weight=51),
        ]
        data['prenatals'] = [
            SimpleNamespace(photo='scan.jpg', pregnancyrecord=data['preg_records'][0]),
        ]
        data['feelings'] = [
            SimpleNamespace(feeling=SimpleNamespace(feeling_name='快樂'), pregnancyrecord=data['preg_records'][0]),
            SimpleNamespace(feeling=SimpleNamespace(feeling_name='快樂'), pregnancyrecord=data['preg_records'][1]),
            SimpleNamespace(feeling=SimpleNamespace(feeling_name='累'), pregnancyrecord=data['preg_records'][1]),
        ]
        baby = SimpleNamespace(name='小寶', birthdaytime=datetime.datetime(2026, 5, 1, 8, 0))
        data['baby_records'] = [
            SimpleNamespace(date=datetime.date(2026, 5, 1), weight=3.2, height=50.0, photo='b1.jpg', record=''),
            SimpleNamespace(date=datetime.date(2026, 8, 1), weight=6.5, height=62.0, photo='', record=''),
        ]
        data['milestones'] = [
            SimpleNamespace(
                babygrowthmap_id=1,
                babygrowthmap=SimpleNamespace(growthrecord='第一次微笑', timecourse=2),
                babyrecord=data['baby_records'][1],
            ),
        ]
        data['cares'] = [
            SimpleNamespace(recordtime=datetime.datetime(2026, 3, 5, 9, 0), state=True),
        ]

        slides = _build_slides(
            2026, data, today=self.today, subject_name='小寶',
            preg_case=None, active_baby=baby,
        )
        kinds = [s['kind'] for s in slides]
        self.assertEqual(
            kinds,
            ['cover', 'number', 'number', 'month', 'photos', 'growth',
             'milestones', 'mood', 'facts', 'finale'],
        )

        by_kind = {}
        for s in slides:
            by_kind.setdefault(s['kind'], []).append(s)

        # 陪伴天數以寶寶生日為起點，只算到今天
        days_slide = by_kind['number'][0]
        self.assertEqual(days_slide['number'], (self.today - datetime.date(2026, 5, 1)).days + 1)

        # 紀錄總數 = 2 孕期 + 3 心情 + 2 寶寶 + 1 待辦
        records_slide = by_kind['number'][1]
        self.assertEqual(records_slide['number'], 8)
        self.assertEqual({c['label']: c['count'] for c in records_slide['chips']},
                         {'孕期紀錄': 2, '心情': 3, '寶寶紀錄': 2, '待辦': 1})

        # 3 月有 2 筆孕期 + 1 筆待辦，是最勤勞的月份
        self.assertEqual(by_kind['month'][0]['number'], 3)
        self.assertIn('3 月', by_kind['month'][0]['title'])

        # 照片：1 張寶寶照 + 1 張超音波，超音波在前（日期較早）
        photos = by_kind['photos'][0]
        self.assertEqual(photos['number'], 2)
        self.assertEqual([p['label'] for p in photos['photos']], ['超音波', '寶寶紀錄'])
        self.assertEqual(photos['photos'][0]['url'], '/media/scan.jpg')

        growth = by_kind['growth'][0]
        self.assertEqual({m['label']: m['delta'] for m in growth['metrics']}, {'體重': 3.3, '身高': 12.0})

        self.assertEqual(by_kind['milestones'][0]['items'][0]['title'], '第一次微笑')

        mood = by_kind['mood'][0]
        self.assertIn('快樂', mood['title'])
        self.assertEqual(mood['number'], 2)
        self.assertEqual(mood['moods'][0]['pct'], 67)

        facts = {f['label']: f['number'] for f in by_kind['facts'][0]['facts']}
        self.assertEqual(facts, {'次產檢': 1, '件待辦完成': 1})

        summary = {s['label']: s['number'] for s in by_kind['finale'][0]['summary']}
        self.assertEqual(summary, {'筆紀錄': 8, '張照片': 2, '個里程碑': 1})

    def test_days_slide_skipped_when_anchor_is_after_year(self):
        data = _empty_data()
        data['cares'] = [SimpleNamespace(recordtime=datetime.datetime(2025, 12, 31, 9, 0), state=False)]
        baby = SimpleNamespace(name='小寶', birthdaytime=datetime.datetime(2026, 1, 1))
        slides = _build_slides(
            2025, data, today=self.today, subject_name='小寶',
            preg_case=None, active_baby=baby,
        )
        kinds = [s['kind'] for s in slides]
        # 2025 年寶寶還沒出生，不應出現陪伴天數；待辦未完成也不出現「默默完成的事」
        self.assertEqual(kinds, ['cover', 'number', 'month', 'finale'])
        self.assertEqual(slides[1]['unit'], '筆紀錄')

    def test_template_compiles(self):
        get_template('history/v3_year_in_review.html')
