import unittest


class BulkUploadFormatTests(unittest.TestCase):
    def test_builds_uploader_path_and_adds_stable_orders(self):
        from quiz_generator.upload_format import prepare_bulk_upload

        quiz = {
            'title': 'Quiz Toán 7 - Bài 12: Tiết 36 - Tổng các góc trong một tam giác',
            'description': 'Bài kiểm tra.',
            'questions': [
                {
                    'prompt': 'Tổng ba góc bằng bao nhiêu?',
                    'explanation': 'Tổng bằng 180°.',
                    'options': [
                        {'text': '180°', 'isCorrect': True},
                        {'text': '90°', 'isCorrect': False},
                    ],
                },
            ],
        }

        relative_path, payload = prepare_bulk_upload(quiz)

        self.assertEqual(
            relative_path,
            'Toan7/Bài 12_Tổng các góc trong một tam giác/'
            'T36_Tổng các góc trong một tam giác/quiz.json',
        )
        self.assertEqual(payload['type'], 'LESSON')
        self.assertEqual(payload['questions'][0]['order'], 1)
        self.assertEqual(
            [option['order'] for option in payload['questions'][0]['options']],
            [1, 2],
        )
        self.assertNotIn('type', quiz)
        self.assertNotIn('order', quiz['questions'][0])

    def test_builds_special_lesson_path_without_lesson_number(self):
        from quiz_generator.upload_format import bulk_upload_identity, prepare_bulk_upload

        quiz = {
            'title': (
                'Quiz Toán 7 - Luyện tập chung 1 _ chương II: '
                'Tiết 22 - Luyện tập chung chương II'
            ),
            'description': 'Bài kiểm tra.',
            'questions': [],
        }

        relative_path, _ = prepare_bulk_upload(quiz)

        self.assertEqual(
            relative_path,
            'Toan7/Luyện tập chung 1 _ chương II/'
            'T22_Luyện tập chung chương II/quiz.json',
        )
        self.assertEqual(
            bulk_upload_identity(quiz),
            '701|luyen-tap-chung-1-chuong-ii|22',
        )

    def test_builds_special_lesson_path_when_name_starts_with_bai(self):
        from quiz_generator.upload_format import prepare_bulk_upload

        relative_path, _ = prepare_bulk_upload({
            'title': (
                'Quiz Toán 7 - Bài tập cuối chương II: '
                'Tiết 24 - Bài tập cuối chương II'
            ),
            'description': 'Bài kiểm tra.',
            'questions': [],
        })

        self.assertEqual(
            relative_path,
            'Toan7/Bài tập cuối chương II/T24_Bài tập cuối chương II/quiz.json',
        )

    def test_accepts_standard_vietnamese_spelling_for_physics(self):
        from quiz_generator.upload_format import prepare_bulk_upload

        quiz = {
            'title': 'Quiz Vật lí 10 - Bài 3: Tiết 5 - Chuyển động thẳng',
            'description': 'Bài kiểm tra.',
            'questions': [],
        }

        relative_path, _ = prepare_bulk_upload(quiz)

        self.assertEqual(
            relative_path,
            'VatLy10/Bài 3_Chuyển động thẳng/T5_Chuyển động thẳng/quiz.json',
        )

    def test_accepts_standard_vietnamese_spelling_for_geography(self):
        from quiz_generator.upload_format import prepare_bulk_upload

        quiz = {
            'title': 'Quiz Địa lí 7 - Bài 2: Tiết 4 - Đặc điểm dân cư',
            'description': 'Bài kiểm tra.',
            'questions': [],
        }

        relative_path, _ = prepare_bulk_upload(quiz)

        self.assertEqual(
            relative_path,
            'DiaLy7/Bài 2_Đặc điểm dân cư/T4_Đặc điểm dân cư/quiz.json',
        )

    def test_rejects_subject_grade_pairs_missing_from_uploader(self):
        from quiz_generator.upload_format import prepare_bulk_upload

        for title in (
            'Quiz Khoa học tự nhiên 10 - Bài 1: Tiết 1 - Mở đầu',
            'Quiz Giáo dục kinh tế và pháp luật 7 - Bài 1: Tiết 1 - Mở đầu',
        ):
            with self.subTest(title=title), self.assertRaisesRegex(ValueError, 'không hỗ trợ'):
                prepare_bulk_upload({'title': title, 'description': 'x', 'questions': []})

    def test_uses_course_folder_names_from_quiz_lam_lai(self):
        from quiz_generator.upload_format import prepare_bulk_upload

        cases = {
            'Quiz Tiếng Anh 10 - Bài 1: Tiết 3 - Family Life': 'Anh10',
            'Quiz Ngữ văn 12 - Bài 2: Tiết 20 - Tây Tiến': 'Van12',
            'Quiz Lịch sử 10 - Bài 5: Tiết 9 - Văn minh': 'Su10',
            'Quiz Tin học 8 - Bài 1: Tiết 2 - Máy tính': 'Tin8',
            'Quiz Giáo dục công dân 7 - Bài 1: Tiết 1 - Tự hào': 'GDCD7',
            'Quiz Giáo dục kinh tế và pháp luật 10 - Bài 1: Tiết 1 - Nền kinh tế': 'GDKTPL10',
        }

        for title, course_folder in cases.items():
            with self.subTest(title=title):
                relative_path, _ = prepare_bulk_upload({
                    'title': title,
                    'description': 'x',
                    'questions': [],
                })
                self.assertEqual(relative_path.split('/', 1)[0], course_folder)

    def test_separates_lesson_and_period_names_like_reference_tree(self):
        from quiz_generator.upload_format import prepare_bulk_upload

        relative_path, _ = prepare_bulk_upload({
            'title': (
                'Quiz Tiếng Anh 10 - Bài 1: Tiết 3: Getting Started - '
                'Family Life - Family Life'
            ),
            'description': 'x',
            'questions': [],
        })

        self.assertEqual(
            relative_path,
            'Anh10/Bài 1_Family Life/T3_Getting Started - Family Life/quiz.json',
        )

    def test_identity_uses_the_same_ascii_slug_rules_as_node(self):
        from quiz_generator.upload_format import bulk_upload_identity

        def quiz(name):
            return {
                'title': f'Quiz Toán 7 - Bài 12: Tiết 36 - {name}',
                'description': 'x',
                'questions': [],
            }

        self.assertEqual(
            bulk_upload_identity(quiz('Straße')),
            bulk_upload_identity(quiz('Straøe')),
        )


if __name__ == '__main__':
    unittest.main()
