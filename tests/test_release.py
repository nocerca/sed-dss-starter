import subprocess

import pytest

from scripts.check_release import check_release


def stage(root, name, content):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')
    subprocess.run(['git', 'add', '--', name], cwd=root, check=True, capture_output=True)
    return path


@pytest.fixture
def repository(tmp_path):
    subprocess.run(['git', 'init', '--quiet'], cwd=tmp_path, check=True, capture_output=True)
    return tmp_path


def test_clean_index_excludes_untracked_local_secret(repository):
    (repository / '.env').write_text('APP_API_KEY=synthetic-local-only-secret', encoding='utf-8')
    stage(repository, 'README.md', 'Project documentation')
    assert check_release(repository) == 1


@pytest.mark.parametrize('path', ['.env', 'examples/original.docx', 'backups/private.dump', '.vscode/settings.json'])
def test_private_artifacts_cannot_be_published(repository, path):
    stage(repository, path, 'synthetic data')
    with pytest.raises(ValueError, match='Local-only'):
        check_release(repository)


def test_scan_uses_staged_content_even_if_working_file_was_cleaned(repository):
    secret = 'synthetic-configured-private-key'
    (repository / '.env').write_text('APP_API_KEY=' + secret, encoding='utf-8')
    file = stage(repository, 'config.txt', secret)
    file.write_text('clean working file', encoding='utf-8')
    with pytest.raises(ValueError, match='Credential'):
        check_release(repository)


def test_github_tokens_are_rejected_without_reading_environment(repository):
    stage(repository, 'config.txt', 'ghp_' + 'A' * 36)
    with pytest.raises(ValueError, match='Credential'):
        check_release(repository)
