from concurrent.futures import ThreadPoolExecutor
from threading import Event
import traceback
from . import model
from . import validation as v
from .preparation import prepare
from .registration import align_set
from .exporting import result_bundle, project_bundle, json_bytes


class Jobs:
    def __init__(self, store, assets, results):
        self.store = store
        self.assets = assets
        self.results = results
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='stacklight-worker')
        self.events = {}
        self.futures = {}
        for path in store.jobs.glob('*.json'):
            item = store.read(path)
            if item['status'] in ('running', 'queued', 'cancelling'):
                item.update(status='interrupted', error='服务重启，当前任务中断，已完成结果保留', finished_at=v.timestamp())
                self.save(item)

    def save(self, job):
        job['updated_at'] = v.timestamp()
        self.store.write(self.store.jobs / (job['id'] + '.json'), job)

    def get(self, key):
        v.identifier(key)
        with self.store.lock:
            result = self.store.read(self.store.jobs / (key + '.json'))
        if result is None:
            raise v.Invalid('任务不存在')
        return result

    def list(self, project_id=None):
        with self.store.lock:
            result = [self.store.read(path) for path in self.store.jobs.glob('*.json')]
        if project_id:
            result = [item for item in result if item['project_id'] == project_id]
        return sorted(result, key=lambda row: row['created_at'], reverse=True)

    def start(self, project_id, body):
        kind = v.choice(body.get('kind', 'process'), '任务类型', ['process', 'align', 'export', 'project', 'batch', 'sweep'])
        with self.store.lock:
            active = [job for job in self.list() if job['status'] in ('running', 'queued', 'cancelling')]
            if len(active) >= 5:
                raise v.Invalid('最多同时排队5个任务')
            project = self.store.get(project_id)
            if 'revision' in body and project['revision'] != v.revision(body):
                raise v.Conflict('工程已更新，请刷新')
            if kind != 'project':
                model.enabled_frames(project)
            entry = {'id': model.uid(), 'project_id': project_id, 'project_name': project['name'], 'source_revision': project['revision'], 'source_signature': model.signature(project), 'kind': kind, 'parameters': body, 'status': 'queued', 'progress': 0, 'stage': '等待计算', 'created_at': v.timestamp(), 'updated_at': v.timestamp(), 'finished_at': None, 'error': None, 'details': None, 'result_ids': [], 'filename': None, 'bytes': 0, 'mimetype': 'application/zip'}
            self.save(entry)
            event = Event()
            self.events[entry['id']] = event
            self.futures[entry['id']] = self.pool.submit(self.run, entry['id'], project, event)
        return self.get(entry['id'])

    def progress(self, job_id, fraction, message):
        with self.store.lock:
            job = self.get(job_id)
            job['progress'] = round(min(.99, max(0, fraction)) * 100, 1)
            job['stage'] = message
            self.save(job)

    def append_result(self, job_id, result_id):
        with self.store.lock:
            job = self.get(job_id)
            job['result_ids'].append(result_id)
            self.save(job)

    def run(self, job_id, project, event):
        try:
            with self.store.lock:
                job = self.get(job_id)
                job.update(status='running', stage='准备数据')
                self.save(job)
            if event.is_set():
                raise InterruptedError('开始前取消')
            callback = lambda fraction, message: self.progress(job_id, fraction, message)
            data = None
            details = None
            filename = None
            if job['kind'] == 'align':
                prepared = prepare(project, self.assets, False, callback, event.is_set)
                details = align_set(prepared, project['settings']['alignment'], callback, event.is_set)
                data = json_bytes(details)
                filename = 'alignment.json'
            elif job['kind'] == 'project':
                data = project_bundle(project, self.assets)
                filename = 'stacklight-editable-project.zip'
            elif job['kind'] in ('batch', 'sweep'):
                from .batch import run_batch
                data, details = run_batch(project, self, job, event, callback)
                filename = 'stacklight-' + job['kind'] + '.zip'
            else:
                meta = self.results.run(project, callback, event.is_set, activate=job['kind'] == 'process')
                self.append_result(job_id, meta['id'])
                details = {key: value for key, value in meta.items() if key != 'project'}
                if job['kind'] == 'export':
                    fields, _ = self.results.fields(project['id'], meta['id'], False)
                    data, details = result_bundle(fields, meta, job['parameters'])
                    filename = 'stacklight-composite.zip'
            if event.is_set():
                raise InterruptedError('当前阶段结束后取消，完整历史结果保留')
            with self.store.lock:
                job = self.get(job_id)
                if data is not None:
                    temporary = self.store.jobs / (job_id + '.tmp')
                    temporary.write_bytes(data)
                    temporary.replace(self.store.jobs / (job_id + '.bin'))
                    job.update(filename=filename, bytes=len(data))
                    if job['kind'] == 'align':
                        job['mimetype'] = 'application/json'
                job.update(status='completed', progress=100, stage='完成', details=details, finished_at=v.timestamp())
                self.save(job)
        except Exception as error:
            with self.store.lock:
                job = self.get(job_id)
                job.update(status='cancelled' if isinstance(error, InterruptedError) else 'failed', error=str(error), stage='取消' if isinstance(error, InterruptedError) else '失败', finished_at=v.timestamp())
                self.save(job)

    def apply_alignment(self, job_id, revision):
        job = self.get(job_id)
        if job['status'] != 'completed' or job['kind'] != 'align':
            raise v.Invalid('请选择已完成配准任务')
        key = job['project_id']
        with self.store.edit(key, revision, '应用自动配准结果') as project:
            if model.signature(project) != job['source_signature']:
                raise v.Conflict('照片或设置已改变，请重新配准')
            for entry in job['details']['frames']:
                model.find_frame(project, entry['frame_id'])['matrix'] = entry['matrix']
        return self.store.get(key)

    def cancel(self, job_id):
        with self.store.lock:
            job = self.get(job_id)
            if job['status'] not in ('queued', 'running', 'cancelling'):
                raise v.Invalid('任务已结束')
            self.events[job_id].set()
            job.update(status='cancelling', stage='等待当前计算阶段结束')
            self.save(job)
        return self.get(job_id)

    def retry(self, job_id):
        job = self.get(job_id)
        if job['status'] not in ('failed', 'cancelled', 'interrupted'):
            raise v.Invalid('仅未完成任务可以重试')
        options = dict(job['parameters'])
        options.pop('revision', None)
        return self.start(job['project_id'], options)

    def download(self, job_id):
        job = self.get(job_id)
        if job['status'] != 'completed' or not job['filename']:
            raise v.Invalid('任务没有可下载文件')
        path = self.store.jobs / (job_id + '.bin')
        if not path.exists():
            raise v.Invalid('任务输出文件缺失')
        return path, job
