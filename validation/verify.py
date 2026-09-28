import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import xml.etree.ElementTree as ET

meta = json.loads((Path(__file__).parent / 'case.json').read_text())
work = Path(sys.argv[1]).resolve()
evidence = Path(sys.argv[2]).resolve()
evidence.mkdir(parents=True, exist_ok=True)
laser = meta['repo'].endswith('/lasertagger')
python = sys.executable
summary = {}
os.environ['PYTHONPATH'] = str(work)
os.environ['TEST_TMPDIR'] = str(evidence / 'test-tmp')
Path(os.environ['TEST_TMPDIR']).mkdir(exist_ok=True)


def run(label, args, expected=0, cwd=work):
    with (evidence / (label + '.log')).open('w') as log:
        result = subprocess.run(args, cwd=cwd, stdout=log, stderr=subprocess.STDOUT,
                                env=os.environ.copy(), timeout=180)
    output = (evidence / (label + '.log')).read_text(errors='replace')
    print(label, result.returncode, output[-600:], flush=True)
    assert result.returncode == expected, (label, result.returncode, output[-10000:])
    return output


def test(label, files, expected=0, cwd=work, cover=False):
    args = list(files) + ['-q', '--tb=short', '--junitxml=' + str(evidence / (label + '.xml'))]
    if cover:
        args += ['--cov=.', '--cov-branch', '--cov-report=json:' + str(evidence / 'coverage.json')]
    if laser:
        code = "from absl import flags; import pytest,sys; flags.FLAGS(['tests']); raise SystemExit(pytest.main(sys.argv[1:]))"
        command = [python, '-c', code] + args
    else:
        command = [python, '-m', 'pytest'] + args
    run(label, command, expected=expected, cwd=cwd)
    doc = ET.parse(evidence / (label + '.xml'))
    suites = list(doc.getroot().iter('testsuite'))
    counts = [sum(int(s.get(key, 0)) for s in suites) for key in ('tests', 'failures', 'errors', 'skipped')]
    summary[label] = counts
    print(label, counts, flush=True)
    return counts


assert subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=work, text=True).strip() == meta['head']
run('dependencies', [python, '-m', 'pip', 'freeze'])
run('dependency-check', [python, '-m', 'pip', 'check'])
source = work / meta['source']
fixed = source.read_bytes()
original = subprocess.check_output(['git', 'show', meta['base'] + ':' + meta['source']], cwd=work)
focused = [meta['test']]
existing = ['utils_test.py', 'tagging_test.py', 'tagging_converter_test.py'] if laser else []
expected_count = 21 if laser else 27
expected_failures = 10 if laser else 16
try:
    assert test('fixed-focused', focused, cover=True) == [expected_count, 0, 0, 0]
    if laser:
        assert test('fixed-suite', focused + existing) == [45, 0, 0, 2]
    source.write_bytes(original)
    assert test('original-focused', focused, expected=1) == [expected_count, expected_failures, 0, 0]
    if laser:
        assert test('original-existing', existing) == [24, 0, 0, 2]
finally:
    source.write_bytes(fixed)
assert test('restored-focused', focused) == [expected_count, 0, 0, 0]
run('source-restored', ['git', 'diff', '--exit-code'])
run('test-lint', [python, '-m', 'ruff', 'check', '--isolated', '--select', 'E,F', '--ignore', 'E501', meta['test']])
run('source-correctness-lint', [python, '-m', 'ruff', 'check', '--isolated', '--select', 'E9,F63,F7,F82', meta['source']])
if laser:
    text = run('test-format', [python, '-m', 'yapf', '--style={based_on_style: google, indent_width: 2}', '-d', meta['test']])
    assert not text.strip(), text
else:
    run('test-format', [python, '-m', 'pyink', '--pyink-indentation', '2', '--line-length', '80', '--target-version', 'py311', '--check', meta['test']])
run('patch-check', ['git', 'diff', '--check', meta['base']])
with tempfile.TemporaryDirectory() as temporary:
    exported = Path(temporary)
    archive = exported / 'source.tar'
    run('archive', ['git', 'archive', '-o', str(archive), meta['head']])
    # The archive contains only this repository's already-inspected source commit.
    with tarfile.open(archive) as content:
        for member in content.getmembers():
            destination = (exported / member.name).resolve()
            assert destination.is_relative_to(exported.resolve())
        content.extractall(exported)
    os.environ['PYTHONPATH'] = str(exported)
    module = 'utils' if laser else 'scaling_geoloc.utils.geo'
    code = "import importlib; from pathlib import Path; m=importlib.import_module(%r); p=Path(m.__file__).resolve(); print(p); assert p.is_relative_to(Path(%r).resolve())" % (module, str(exported))
    run('exported-import', [python, '-c', code], cwd=exported)
    counts = test('exported-suite', focused + existing, cwd=exported)
    assert counts == ([45, 0, 0, 2] if laser else [27, 0, 0, 0])
(evidence / 'summary.json').write_text(json.dumps(summary, indent=2))
print('EXACT SOURCE VALIDATED', meta['head'], flush=True)
