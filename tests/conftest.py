import pytest


@pytest.fixture
def quiz():
    return {
        'title': 'Quiz Toán 7 - Bài 12: Tiết 36 - Tổng các góc trong một tam giác',
        'description': '10 câu trắc nghiệm kiểm tra tổng các góc trong tam giác.',
        'questions': [
            {'prompt': f'Câu hỏi {i + 1}?', 'explanation': 'Tổng ba góc bằng 180°.',
             'options': [{'text': f'Phương án {j + 1}', 'isCorrect': j == 0} for j in range(4)]}
            for i in range(10)
        ],
    }
