import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('zotero_lit_under_test', ROOT / 'zotero/scripts/zotero.py')
z = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = z
spec.loader.exec_module(z)


def record(key, title, year='2024', venue='Sensors', tags=None, abstract=''):
    return {
        'key': key,
        'itemType': 'journalArticle',
        'title': title,
        'year': year,
        'venue': venue,
        'doi': '',
        'abstract': abstract,
        'url': '',
        'tags': tags or [],
        'collections': [],
        'methods': [],
        'datasets': [],
    }


class TermExtractionTests(unittest.TestCase):
    def test_extracts_methods_and_datasets_from_text(self):
        text = 'We train a Transformer with knowledge distillation on SEED-IV and DEAP.'
        self.assertIn('transformer', z.extract_terms(text)['methods'])
        self.assertIn('distillation', z.extract_terms(text)['methods'])
        self.assertIn('SEED', z.extract_terms(text)['datasets'])
        self.assertIn('DEAP', z.extract_terms(text)['datasets'])

    def test_backbone_is_not_counted_as_graph_network(self):
        methods = z.extract_terms('A convolutional backbone with graph attention')['methods']
        self.assertIn('gnn', methods)
        self.assertNotIn('cnn', methods)

    def test_no_terms_on_plain_prose(self):
        terms = z.extract_terms('We study how people read papers in a library.')
        self.assertEqual(terms['methods'], [])
        self.assertEqual(terms['datasets'], [])


class ClassifyPlanTests(unittest.TestCase):
    def test_plan_adds_only_missing_tags(self):
        rows = [record('A1', 'Transformer on SEED', tags=['auto:method:transformer'])]
        rows[0]['methods'] = ['transformer', 'cnn']
        rows[0]['datasets'] = ['SEED']
        plan = z.classify_plan(rows)[0]
        self.assertEqual(
            plan['addTags'], ['auto:method:cnn', 'auto:dataset:SEED']
        )

    def test_plan_can_group_by_year_and_venue(self):
        rows = [record('A2', 'Paper', year='2025', venue='Sensors')]
        plan = z.classify_plan(rows, by_method=False, by_dataset=False, by_year=True, by_venue=True)[0]
        self.assertEqual(plan['addTags'], ['auto:year:2025'])
        self.assertEqual(plan['collection'], 'Sensors')

    def test_plan_is_empty_when_nothing_to_add(self):
        rows = [record('A3', 'Paper', tags=['auto:year:2024'])]
        plan = z.classify_plan(rows, by_method=False, by_dataset=False, by_year=True)[0]
        self.assertEqual(plan['addTags'], [])


class LandscapeTests(unittest.TestCase):
    def records(self):
        first = record('L1', 'Transformer grasping', year='2024', venue='Sensors')
        first.update({'methods': ['transformer'], 'datasets': ['GraspNet']})
        second = record('L2', 'CNN grasping', year='2025', venue='Sensors')
        second.update({'methods': ['cnn'], 'datasets': ['GraspNet']})
        third = record('L3', 'Review', year='2025', venue='IEEE TNSRE')
        third.update({'methods': ['transformer'], 'datasets': []})
        return [first, second, third]

    def test_counts_years_venues_methods(self):
        payload = z.landscape_payload(self.records())
        self.assertEqual(payload['items'], 3)
        self.assertEqual(payload['years'], {'2024': 1, '2025': 2})
        self.assertEqual(payload['venues']['Sensors'], 2)
        self.assertEqual(payload['methods']['transformer'], 2)
        self.assertEqual(payload['datasets']['GraspNet'], 2)

    def test_markdown_renders_tables(self):
        text = z.landscape_markdown(z.landscape_payload(self.records()))
        self.assertIn('| 年份 | 篇数 |', text)
        self.assertIn('transformer', text)


class GapTests(unittest.TestCase):
    def group_records(self):
        first = record('G1', 'A', tags=['project:MI-EEG'])
        first.update({'methods': ['transformer'], 'datasets': ['SEED']})
        second = record('G2', 'B', tags=['project:MI-EEG'])
        second.update({'methods': ['transformer'], 'datasets': ['SEED']})
        third = record('G3', 'C', tags=['project:MI-EEG'])
        third.update({'methods': ['cnn'], 'datasets': ['DEAP']})
        fourth = record('G4', 'D', tags=['project:MI-EEG'])
        fourth.update({'methods': ['cnn'], 'datasets': ['DEAP']})
        return [first, second, third, fourth]

    def test_grouping_uses_project_prefix(self):
        rows = self.group_records()
        self.assertEqual(z.grouping_prefix(rows), 'project:')
        groups = z.group_records(rows, 'project:')
        self.assertEqual(list(groups), ['MI-EEG'])
        self.assertEqual(len(groups['MI-EEG']), 4)

    def test_empty_cells_stay_inside_the_group(self):
        rows = self.group_records()
        payload = z.gap_payload(rows, min_freq=2, prefix='project:')
        cells = payload['groups'][0]['emptyCells']
        pairs = {(cell['method'], cell['dataset']) for cell in cells}
        self.assertIn(('transformer', 'DEAP'), pairs)
        self.assertIn(('cnn', 'SEED'), pairs)
        self.assertNotIn(('transformer', 'SEED'), pairs)

    def test_cooled_tags_detected(self):
        rows = self.group_records()
        for row in rows:
            row['year'] = '2018'
        payload = z.gap_payload(rows, min_freq=2, prefix='project:')
        tags = [row['tag'] for row in payload['groups'][0]['cooledTags']]
        self.assertIn('project:MI-EEG', tags)


class DuplicateTests(unittest.TestCase):
    def test_groups_by_doi(self):
        first = record('D1', 'Alpha paper')
        first['doi'] = '10.1000/abc'
        second = record('D2', 'Alpha paper')
        second['doi'] = '10.1000/ABC'
        groups = z.duplicate_groups([first, second])
        self.assertEqual(len(groups), 1)
        self.assertEqual(sorted(groups[0]['keys']), ['D1', 'D2'])

    def test_groups_by_normalized_title(self):
        first = record('D3', 'A  Study: of Grasping!')
        second = record('D4', 'A study of grasping')
        groups = z.duplicate_groups([first, second])
        self.assertEqual(len(groups), 1)

    def test_distinct_titles_stay_separate(self):
        first = record('D5', 'Alpha paper')
        second = record('D6', 'Beta paper')
        self.assertEqual(z.duplicate_groups([first, second]), [])


class ScreenTests(unittest.TestCase):
    def test_tiers_follow_scores(self):
        self.assertEqual(z.tier_for(0.9), '核心')
        self.assertEqual(z.tier_for(0.5), '重要')
        self.assertEqual(z.tier_for(0.1), '参考')

    def test_markdown_uses_row_tier_and_note(self):
        rows = [
            {'title': 'One', 'score': 0.9, 'tier': '核心', 'year': '2024'},
            {'title': 'Two', 'score': 0.9, 'tier': '参考', 'year': '2024'},
        ]
        text = z.screen_markdown(rows, 'T', '相对排名')
        self.assertIn('共 2 篇，其中核心 1 篇。', text)
        self.assertIn('相对排名', text)


class ReaderFileTests(unittest.TestCase):
    def test_reads_utf16_and_utf8(self):
        with tempfile.TemporaryDirectory() as temp:
            utf16 = Path(temp) / 'a.json'
            utf16.write_bytes(json.dumps({'ok': True}).encode('utf-16'))
            self.assertEqual(json.loads(z.read_text_file(utf16)), {'ok': True})
            utf8 = Path(temp) / 'b.json'
            utf8.write_text('{"ok": false}', encoding='utf-8')
            self.assertEqual(json.loads(z.read_text_file(utf8)), {'ok': False})


class DigestTests(unittest.TestCase):
    def test_outline_keeps_known_headings(self):
        text = 'Abstract\nWe study grasping.\n\nI. INTRODUCTION\nRobots grasp.\n\nReferences\n[1] A. Author.'
        outline = z.text_outline(text)
        titles = [row[0] for row in outline]
        self.assertIn('Abstract', titles)
        self.assertIn('I. INTRODUCTION', titles)
        self.assertNotIn('References', titles)

    def test_slugify_and_cell_escape(self):
        self.assertEqual(z.slugify('Hello, World!'), 'hello-world')
        self.assertEqual(z.md_cell('a|b'), 'a\\|b')


class FrontierFileTests(unittest.TestCase):
    def test_add_list_remove_roundtrip(self):
        with tempfile.TemporaryDirectory() as temp:
            old = os.environ.get('ZOTERO_SKILL_CONFIG_DIR')
            os.environ['ZOTERO_SKILL_CONFIG_DIR'] = temp
            try:
                z.cmd_frontier(type('Args', (), {
                    'action': 'add', 'name': 'w1', 'query': ['grasping'],
                    'days': 30, 'max': 5, 'include_preprints': False,
                    'since_last': False, 'do_import': False, 'yes': False,
                    'mailto': '', 'out': None, 'json': True,
                })())
                saved = json.loads(Path(temp, 'frontier.json').read_text(encoding='utf-8'))
                self.assertIn('w1', saved['watches'])
                self.assertEqual(saved['watches']['w1']['queries'], ['grasping'])
            finally:
                if old is None:
                    os.environ.pop('ZOTERO_SKILL_CONFIG_DIR', None)
                else:
                    os.environ['ZOTERO_SKILL_CONFIG_DIR'] = old


if __name__ == '__main__':
    unittest.main()


class CollectionFolderTests(unittest.TestCase):
    def test_groups_by_dimension_and_path(self):
        records = [
            dict(record('K1', 'A study'), methods=['transformer', 'cnn']),
            dict(record('K2', 'B study'), methods=['cnn']),
        ]
        groups = z.collection_plan(records, ['method'], root='文献分类', group='none')
        by_value = {group['value']: group for group in groups}
        self.assertEqual(sorted(by_value), ['cnn', 'transformer'])
        self.assertEqual(by_value['cnn']['path'], ['文献分类', '方法', 'cnn'])
        self.assertEqual(by_value['cnn']['items'], ['K1', 'K2'])
        self.assertEqual(by_value['transformer']['items'], ['K1'])

    def test_unclassified_bucket_is_opt_in(self):
        records = [dict(record('K1', 'No terms'))]
        self.assertEqual(z.collection_plan(records, ['method']), [])
        groups = z.collection_plan(records, ['method'], include_unclassified=True)
        self.assertEqual(groups[0]['value'], '未标注')

    def test_type_dimension_marks_reviews(self):
        self.assertEqual(
            z.dimension_values(record('K1', 'A survey of EEG'), 'type'),
            ['期刊论文', '综述'],
        )
        self.assertEqual(
            z.dimension_values(record('K2', 'A new method'), 'type'), ['期刊论文']
        )
        odd = dict(record('K3', 'x'), itemType='software')
        self.assertEqual(z.dimension_values(odd, 'type'), ['其他'])

    def test_topic_dimension_reads_tags(self):
        rec = dict(
            record('K1', 'x', tags=['topic:edge-deployment', 'project:MI-EEG'])
        )
        self.assertEqual(z.dimension_values(rec, 'topic'), ['edge-deployment'])
        self.assertEqual(z.dimension_values(rec, 'project'), ['MI-EEG'])

    def test_year_and_venue_dimensions(self):
        rec = dict(record('K1', 'x', year='2023', venue='Sensors'))
        self.assertEqual(z.dimension_values(rec, 'year'), ['2023'])
        self.assertEqual(z.dimension_values(rec, 'venue'), ['Sensors'])
        bare = dict(record('K2', 'x', year='', venue=''))
        self.assertEqual(z.dimension_values(bare, 'year'), [])
        self.assertEqual(z.dimension_values(bare, 'venue'), [])

    def test_parse_dimensions_normalizes_and_rejects(self):
        self.assertEqual(
            z.parse_dimensions('Methods;datasets,year'), ['method', 'dataset', 'year']
        )
        self.assertEqual(z.parse_dimensions(''), [])
        with self.assertRaises(SystemExit):
            z.parse_dimensions('banana')

    def test_markdown_lists_folders_and_counts(self):
        records = [dict(record('K1', 'A study'), methods=['cnn'])]
        groups = z.collection_plan(records, ['method'], root='文献分类', group='none')
        text = z.collection_plan_markdown(groups, 1, root='文献分类', group='none')
        self.assertIn('文献分类 / 方法 / cnn', text)
        self.assertIn('1 个文件夹', text)

    def test_group_level_uses_project_and_rename(self):
        records = [
            dict(record('K1', 'A study', tags=['project:MI-EEG']), methods=['cnn']),
            dict(record('K2', 'B study', tags=['project:BIN-PICKING']), methods=['grasping']),
        ]
        groups = z.collection_plan(
            records, ['method'], rename={'MI-EEG': 'AI脑电解码', 'BIN-PICKING': '智能分拣'}
        )
        paths = sorted(group['path'] for group in groups)
        self.assertEqual(paths, [['AI脑电解码', '方法', 'cnn'], ['智能分拣', '方法', 'grasping']])

    def test_group_level_drops_when_nothing_tagged(self):
        records = [dict(record('K1', 'A study'), methods=['cnn'])]
        groups = z.collection_plan(records, ['method'])
        self.assertEqual(groups[0]['path'], ['方法', 'cnn'])

    def test_parse_renames(self):
        self.assertEqual(
            z.parse_renames('MI-EEG=AI脑电解码; BIN-PICKING=智能分拣'),
            {'MI-EEG': 'AI脑电解码', 'BIN-PICKING': '智能分拣'},
        )
        self.assertEqual(z.parse_renames(''), {})
        with self.assertRaises(SystemExit):
            z.parse_renames('broken')
