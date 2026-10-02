from pathlib import Path
import subprocess

import pytest


def api():
    from quiz_generator import pptx_merge
    return pptx_merge


def completed(returncode=0, stdout='', stderr=''):
    return subprocess.CompletedProcess(['node'], returncode, stdout, stderr)


def test_adapter_returns_typed_slide_counts(tmp_path, monkeypatch):
    part1 = tmp_path / 'part 1.pptx'
    part2 = tmp_path / 'part 2.pptx'
    output = tmp_path / 'merged.pptx'
    worker = tmp_path / 'worker.mjs'
    calls = []

    def fake_run(arguments, **options):
        calls.append((arguments, options))
        output.write_bytes(b'pptx')
        return completed(
            stdout='{"ok":true,"part1Slides":2,"part2Slides":3,"outputSlides":5}'
        )

    monkeypatch.setattr(subprocess, 'run', fake_run)
    merger = api().NodePptxMerger(
        node_binary='node-custom', worker_path=worker, timeout=17.5
    )

    result = merger.merge(part1, part2, output)

    assert result == api().MergeResult(2, 3, 5)
    assert calls == [([
        'node-custom', str(worker),
        '--part1', str(part1),
        '--part2', str(part2),
        '--output', str(output),
    ], {
        'capture_output': True,
        'text': True,
        'timeout': 17.5,
        'shell': False,
        'check': False,
    })]


def test_source_failure_and_infrastructure_failure_are_distinct(tmp_path, monkeypatch):
    secret = 's3cr3t-process-output'
    responses = iter([
        completed(
            returncode=1,
            stdout='{"ok":false,"kind":"source","error":"Slide không hợp lệ"}',
            stderr=secret,
        ),
        completed(returncode=1, stdout='', stderr=secret),
    ])
    monkeypatch.setattr(subprocess, 'run', lambda *args, **kwargs: next(responses))
    merger = api().NodePptxMerger(worker_path=tmp_path / 'worker.mjs')

    with pytest.raises(api().SourceMergeError, match='Slide không hợp lệ') as source:
        merger.merge(tmp_path / '1.pptx', tmp_path / '2.pptx', tmp_path / 'a.pptx')
    with pytest.raises(api().MergeInfrastructureError) as infrastructure:
        merger.merge(tmp_path / '1.pptx', tmp_path / '2.pptx', tmp_path / 'b.pptx')

    assert secret not in str(source.value)
    assert secret not in str(infrastructure.value)


@pytest.mark.parametrize('stdout', [
    '{"ok":true,"part1Slides":1,"part2Slides":1,"outputSlides":2}\nextra',
    'not-json',
    '{"ok":true,"part1Slides":1,"part2Slides":1,"outputSlides":99}',
])
def test_success_with_malformed_or_extra_stdout_is_infrastructure_failure(
    tmp_path, monkeypatch, stdout
):
    output = tmp_path / 'merged.pptx'

    def fake_run(*args, **kwargs):
        output.write_bytes(b'pptx')
        return completed(stdout=stdout)

    monkeypatch.setattr(subprocess, 'run', fake_run)

    with pytest.raises(api().MergeInfrastructureError):
        api().NodePptxMerger(worker_path=tmp_path / 'worker.mjs').merge(
            tmp_path / '1.pptx', tmp_path / '2.pptx', output
        )


def test_timeout_and_missing_node_are_infrastructure_failures(tmp_path, monkeypatch):
    exceptions = iter([
        subprocess.TimeoutExpired(['node'], timeout=1, output='secret'),
        FileNotFoundError('node missing'),
    ])

    def fail(*args, **kwargs):
        raise next(exceptions)

    monkeypatch.setattr(subprocess, 'run', fail)
    merger = api().NodePptxMerger(worker_path=tmp_path / 'worker.mjs', timeout=1)

    with pytest.raises(api().MergeInfrastructureError) as timeout:
        merger.merge(tmp_path / '1.pptx', tmp_path / '2.pptx', tmp_path / 'a.pptx')
    with pytest.raises(api().MergeInfrastructureError) as missing:
        merger.merge(tmp_path / '1.pptx', tmp_path / '2.pptx', tmp_path / 'b.pptx')
    assert 'secret' not in str(timeout.value)
    assert 'node missing' not in str(missing.value)


def paired_plan(tmp_path):
    planning = __import__('quiz_generator.planning', fromlist=['planning'])
    input_root = tmp_path / 'input'
    members = []
    for part in (1, 2):
        relative = f'Toan7/Bài 12/T36_{part}.pptx'
        member_path = input_root / relative
        member_path.parent.mkdir(parents=True, exist_ok=True)
        member_path.write_bytes(f'part-{part}'.encode())
        members.append(planning.SourceFile(member_path, relative, relative))
    return planning.plan_documents(members, tmp_path / 'merged')


class RecordingMerger:
    def __init__(self, *, failure=None):
        self.calls = []
        self.failure = failure

    def merge(self, part1, part2, output):
        self.calls.append((part1, part2, output))
        output.write_bytes(b'temporary-pptx')
        if self.failure:
            raise self.failure
        return api().MergeResult(1, 1, 2)


def test_existing_target_is_reused_without_worker(tmp_path):
    plan = paired_plan(tmp_path)
    destination = plan.documents[0].source_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b'existing')
    merger = RecordingMerger()
    events = []

    result = api().PptxMaterializer(merger).materialize(plan, events.append)

    assert merger.calls == []
    assert result.documents[0].source_path == destination
    assert result.documents[0].action == 'reuse'
    assert destination.read_bytes() == b'existing'
    assert events == [{
        'event': 'reused',
        'state_key': plan.documents[0].state_key,
        'warning': '',
        'members': [
            member.original_path for member in plan.documents[0].members
        ],
        'destination': str(destination),
    }]


def test_source_error_falls_back_to_two_standalones(tmp_path):
    plan = paired_plan(tmp_path)
    merger = RecordingMerger(failure=api().SourceMergeError('unsupported slide'))
    events = []

    result = api().PptxMaterializer(merger).materialize(plan, events.append)

    assert [document.kind for document in result.documents] == ['standalone', 'standalone']
    assert [document.source_path for document in result.documents] == [
        member.path for member in plan.documents[0].members
    ]
    assert all('unsupported slide' in document.warning for document in result.documents)
    assert events[0]['event'] == 'merging'
    assert events[0]['state_key'] == plan.documents[0].state_key
    assert events[0]['warning'] == ''
    assert events[1]['event'] == 'merge_fallback'
    assert events[1]['state_key'] == plan.documents[0].state_key
    assert 'unsupported slide' in events[1]['warning']
    assert not plan.documents[0].source_path.exists()
    assert list(plan.documents[0].source_path.parent.glob('*.tmp-*.pptx')) == []


def test_infrastructure_error_does_not_fallback(tmp_path):
    plan = paired_plan(tmp_path)
    merger = RecordingMerger(failure=api().MergeInfrastructureError('node unavailable'))
    events = []

    with pytest.raises(api().MergeInfrastructureError, match='node unavailable'):
        api().PptxMaterializer(merger).materialize(plan, events.append)

    assert [event['event'] for event in events] == ['merging']
    assert not plan.documents[0].source_path.exists()
    assert list(plan.documents[0].source_path.parent.glob('*.tmp-*.pptx')) == []


def test_temporary_output_is_atomic_and_cleaned(tmp_path):
    plan = paired_plan(tmp_path)
    destination = plan.documents[0].source_path

    class AtomicMerger(RecordingMerger):
        def merge(self, part1, part2, output):
            assert output.parent == destination.parent
            assert output != destination
            assert output.name.endswith('.pptx')
            assert '.tmp-' in output.name
            assert not destination.exists()
            return super().merge(part1, part2, output)

    merger = AtomicMerger()
    events = []

    result = api().PptxMaterializer(merger).materialize(plan, events.append)

    assert destination.read_bytes() == b'temporary-pptx'
    assert result.documents[0].source_path == destination
    assert list(destination.parent.glob('*.tmp-*.pptx')) == []
    assert [event['event'] for event in events] == ['merging', 'merged']
    assert all(event['state_key'] == plan.documents[0].state_key for event in events)
    assert all(event['warning'] == '' for event in events)


def test_materializer_events_include_members_destination_and_slide_counts(tmp_path):
    plan = paired_plan(tmp_path)
    events = []

    api().PptxMaterializer(RecordingMerger()).materialize(plan, events.append)

    expected_members = [
        member.original_path for member in plan.documents[0].members
    ]
    assert events[0]['members'] == expected_members
    assert events[0]['destination'] == str(plan.documents[0].source_path)
    assert events[1]['members'] == expected_members
    assert events[1]['destination'] == str(plan.documents[0].source_path)
    assert events[1]['slide_counts'] == {
        'part1': 1,
        'part2': 1,
        'output': 2,
    }


def test_resolved_output_cannot_escape_merged_root(tmp_path):
    from dataclasses import replace

    plan = paired_plan(tmp_path)
    outside = tmp_path / 'outside' / 'escaped.pptx'
    escaped = replace(plan.documents[0], source_path=outside)
    unsafe_plan = replace(plan, documents=(escaped,))
    merger = RecordingMerger()

    with pytest.raises(ValueError, match='ngoài thư mục'):
        api().PptxMaterializer(merger).materialize(unsafe_plan)

    assert merger.calls == []
    assert not outside.exists()


def test_concurrent_materializers_create_once_and_waiter_reuses(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    import threading

    plan = paired_plan(tmp_path)
    entered = threading.Event()
    release = threading.Event()

    class SlowMerger(RecordingMerger):
        def merge(self, part1, part2, output):
            self.calls.append((part1, part2, output))
            entered.set()
            assert release.wait(timeout=5)
            output.write_bytes(b'complete')
            return api().MergeResult(1, 1, 2)

    merger = SlowMerger()
    first_events = []
    second_events = []
    materializer = api().PptxMaterializer(merger)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(materializer.materialize, plan, first_events.append)
        assert entered.wait(timeout=5)
        second = executor.submit(materializer.materialize, plan, second_events.append)
        release.set()
        first_result = first.result(timeout=5)
        second_result = second.result(timeout=5)

    assert len(merger.calls) == 1
    assert plan.documents[0].source_path.read_bytes() == b'complete'
    assert first_result.documents[0].action == 'merge'
    assert second_result.documents[0].action == 'reuse'
    assert [event['event'] for event in first_events] == ['merging', 'merged']
    assert [event['event'] for event in second_events] == ['reused']
