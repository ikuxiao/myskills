"""Optional real-network smoke test, with temporary storage and no deployment."""
from pathlib import Path
import argparse
import json
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.importer import Importer
from app.resources import ResourceManager


def main():
    parser = argparse.ArgumentParser(description='在临时目录验证公开 GitHub 导入（不执行内容、不部署）')
    parser.add_argument('--repo', default='https://github.com/openai/skills')
    parser.add_argument('--directory', default='skills/.system/skill-creator')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='adc-live-import-') as directory:
        manager = ResourceManager(Path(directory) / 'data')
        importer = Importer(manager)
        entry = importer.stage(args.repo, subdirectory=args.directory)
        assert len(importer.list_pending()) == 1
        assert not manager.list_resources()
        files = importer.files(entry['id'])
        assert 'SKILL.md' in files and entry['commit']
        # Reject after inspection: the smoke test never approves or enables a downloaded skill.
        importer.review(entry['id'], False, expected=entry['fingerprint'])
        assert not importer.list_pending() and not manager.list_resources()
        print(json.dumps({'ok': True, 'source': args.repo, 'directory': args.directory, 'name': entry['name'],
                          'commit': entry['commit'], 'files': len(files), 'licenses': entry['licenses'],
                          'risk_findings': len(entry['findings']), 'review': 'rejected after smoke test', 'deployed': False}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
