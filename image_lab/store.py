import json
import os
import shutil
from pathlib import Path
from threading import RLock
from contextlib import contextmanager
from copy import deepcopy
from . import model
from . import validation as v
from .settings import merge_settings


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.projects = self.root / 'projects'
        self.jobs = self.root / 'jobs'
        self.assets = self.root / 'assets'
        self.profiles = self.root / 'profiles'
        self.lock = RLock()
        for path in [self.root, self.projects, self.jobs, self.assets, self.profiles]:
            path.mkdir(parents=True, exist_ok=True)

    def read(self, path, default=None):
        if not path.exists():
            return deepcopy(default)
        try:
            return json.loads(path.read_text(encoding='utf8'))
        except (OSError, json.JSONDecodeError) as error:
            raise v.Invalid('文件读取失败：' + path.name) from error

    def write(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + '.' + model.uid() + '.tmp')
        try:
            with temporary.open('w', encoding='utf8') as stream:
                json.dump(data, stream, ensure_ascii=False, allow_nan=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def directory(self, key):
        v.identifier(key)
        directory = self.projects / key
        if not (directory / 'project.json').exists():
            raise v.Invalid('工程不存在')
        return directory

    def get(self, key):
        with self.lock:
            return self.read(self.directory(key) / 'project.json')

    def create(self, name):
        name = v.text(name, '工程名称', 120, False)
        current = model.project(name)
        with self.lock:
            directory = self.projects / current['id']
            for folder in ['history', 'snapshots', 'results']:
                (directory / folder).mkdir(parents=True)
            self.write(directory / 'project.json', current)
        return current

    def list(self, query='', archived=False):
        with self.lock:
            result = []
            for path in self.projects.glob('*/project.json'):
                current = self.read(path)
                if current['archived'] and not archived:
                    continue
                searchable = current['name'] + ' ' + current['notes'] + ' '.join(current['tags'])
                if query.casefold() not in searchable.casefold():
                    continue
                result.append(model.summary(current))
        return sorted(result, key=lambda item: item['updated_at'], reverse=True)

    @contextmanager
    def edit(self, key, revision, operation):
        with self.lock:
            original = self.get(key)
            if original['revision'] != revision:
                raise v.Conflict('工程已更新，请刷新后再试')
            working = deepcopy(original)
            yield working
            model.validate(working)
            entry_id = model.uid()
            history = {'id': entry_id, 'operation': operation, 'time': v.timestamp(), 'before': model.snapshot(original), 'after': model.snapshot(working)}
            directory = self.directory(key)
            self.write(directory / 'history' / (entry_id + '.json'), history)
            working['revision'] = revision + 1
            working['updated_at'] = v.timestamp()
            working['undo'] = (original['undo'] + [entry_id])[-100:]
            working['redo'] = []
            if model.signature(original) != model.signature(working):
                working['current_result'] = None
            self.write(directory / 'project.json', working)

    def update(self, key, revision, body):
        allowed = {'revision', 'name', 'notes', 'tags', 'archived', 'reference', 'settings'}
        if set(body) - allowed:
            raise v.Invalid('存在未知工程字段')
        with self.edit(key, revision, '修改工程设置') as current:
            for field in ['name', 'notes', 'tags', 'archived', 'reference']:
                if field in body:
                    current[field] = body[field]
            if 'settings' in body:
                current['settings'] = merge_settings(current['settings'], body['settings'])
        return self.get(key)

    def history(self, key):
        current = self.get(key)
        result = []
        for entry_id in reversed(current['undo']):
            entry = self.read(self.directory(key) / 'history' / (entry_id + '.json'))
            result.append({field: entry[field] for field in ['id', 'operation', 'time']})
        return result

    def undo(self, key, revision, redo=False):
        with self.lock:
            original = self.get(key)
            if original['revision'] != revision:
                raise v.Conflict('修订已改变')
            source = original['redo'] if redo else original['undo']
            if not source:
                raise v.Invalid('没有可重做记录' if redo else '没有可撤销记录')
            entry = self.read(self.directory(key) / 'history' / (source[-1] + '.json'))
            restored = deepcopy(entry['after' if redo else 'before'])
            restored['undo'] = original['undo'] + [source[-1]] if redo else original['undo'][:-1]
            restored['redo'] = original['redo'][:-1] if redo else original['redo'] + [source[-1]]
            restored['revision'] = revision + 1
            restored['updated_at'] = v.timestamp()
            restored['current_result'] = None
            model.validate(restored)
            self.write(self.directory(key) / 'project.json', restored)
        return self.get(key)

    def duplicate(self, key):
        source = self.get(key)
        created = self.create(source['name'][:100] + ' · 副本')
        with self.lock:
            copied = model.snapshot(source)
            copied['id'] = created['id']
            copied['name'] = created['name']
            copied['created_at'] = created['created_at']
            copied.update(revision=0, updated_at=v.timestamp(), undo=[], redo=[], current_result=None, archived=False)
            self.write(self.directory(created['id']) / 'project.json', copied)
        return self.get(created['id'])

    def activate(self, key, result_id, signature):
        with self.lock:
            project = self.get(key)
            if model.signature(project) != signature:
                return False
            project['current_result'] = result_id
            self.write(self.directory(key) / 'project.json', project)
        return True

    def snapshot_create(self, key, body):
        current = self.get(key)
        if 'revision' in body and current['revision'] != v.revision(body):
            raise v.Conflict('修订已改变')
        directory = self.directory(key) / 'snapshots'
        if len(list(directory.glob('*.json'))) >= 100:
            raise v.Invalid('最多100个命名版本')
        item = {'id': model.uid(), 'name': v.text(body.get('name'), '版本名称', 100, False), 'time': v.timestamp(), 'source_revision': current['revision'], 'project': model.snapshot(current)}
        with self.lock:
            self.write(directory / (item['id'] + '.json'), item)
        return {k: value for k, value in item.items() if k != 'project'}

    def snapshots(self, key):
        directory = self.directory(key) / 'snapshots'
        with self.lock:
            items = [self.read(path) for path in directory.glob('*.json')]
        return sorted([{k: val for k, val in item.items() if k != 'project'} for item in items], key=lambda i: i['time'], reverse=True)

    def snapshot_restore(self, key, revision, snapshot_id):
        v.identifier(snapshot_id)
        entry = self.read(self.directory(key) / 'snapshots' / (snapshot_id + '.json'))
        if entry is None:
            raise v.Invalid('版本不存在')
        with self.edit(key, revision, '恢复命名版本') as project:
            for field, value in entry['project'].items():
                if field not in ('id', 'created_at'):
                    project[field] = deepcopy(value)
        return self.get(key)

    def storage(self):
        result = {}
        for path in [self.projects, self.assets, self.jobs, self.profiles]:
            files = [p for p in path.rglob('*') if p.is_file()]
            result[path.name] = {'files': len(files), 'bytes': sum(p.stat().st_size for p in files)}
        usage = shutil.disk_usage(self.root)
        return {'categories': result, 'free_bytes': usage.free, 'note': '素材不可变且可能被历史引用，不自动清理。'}
