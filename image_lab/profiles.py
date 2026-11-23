from copy import deepcopy
from . import model
from . import validation as v
from .settings import all_settings, merge_settings


BUILTINS = {
    'balanced': {'name': '自然曝光融合', 'settings': {'mode': 'fusion', 'fusion': {'contrast': 1, 'saturation': .5, 'exposedness': 1}, 'finishing': {'contrast': 1.04, 'saturation': 1.03}}},
    'architecture': {'name': '建筑窗景HDR', 'settings': {'mode': 'hdr', 'tone': {'operator': 'reinhard', 'key': .22, 'white': 4}, 'finishing': {'clarity': .15}}},
    'macro': {'name': '微距景深堆栈', 'settings': {'mode': 'focus', 'focus': {'metric': 'laplacian', 'radius': 2, 'power': 4, 'method': 'pyramid'}, 'finishing': {'sharpen': .25}}},
    'night': {'name': '夜景时域降噪', 'settings': {'mode': 'denoise', 'denoise': {'method': 'sigma', 'sigma': 1.8, 'linear': True}, 'finishing': {'exposure': .3}}},
    'panorama': {'name': '平面多幅拼接', 'settings': {'mode': 'panorama', 'panorama': {'gain': True, 'blend': 'multiband', 'crop': 'largest_rectangle'}}},
    'soft': {'name': '柔和摄影后期', 'settings': {'finishing': {'contrast': .95, 'saturation': .85, 'shadows': .15, 'highlights': -.1}}},
}


class Profiles:
    def __init__(self, store):
        self.store = store

    def list(self):
        result = [{'id': key, 'name': data['name'], 'builtin': True, 'description': '内置处理起点'} for key, data in BUILTINS.items()]
        for path in self.store.profiles.glob('*.json'):
            item = self.store.read(path)
            result.append({key: item[key] for key in ['id', 'name', 'description', 'created_at']} | {'builtin': False})
        return result

    def get(self, profile_id):
        if profile_id in BUILTINS:
            return deepcopy(BUILTINS[profile_id])
        v.identifier(profile_id)
        result = self.store.read(self.store.profiles / (profile_id + '.json'))
        if result is None:
            raise v.Invalid('处理方案不存在')
        return result

    def save(self, project_id, body):
        project = self.store.get(project_id)
        name = v.text(body.get('name', project['name']), '方案名称', 120, False)
        description = v.text(body.get('description', ''), '方案说明', 1000)
        settings = deepcopy(project['settings'])
        settings['calibration']['dark'] = None
        settings['calibration']['flat'] = None
        settings['calibration']['enabled'] = False
        entry = {'id': model.uid(), 'name': name, 'description': description, 'created_at': v.timestamp(), 'settings': settings}
        with self.store.lock:
            self.store.write(self.store.profiles / (entry['id'] + '.json'), entry)
        return entry

    def apply(self, key, revision, profile_id):
        profile = self.get(profile_id)
        with self.store.edit(key, revision, '应用处理方案：' + profile['name']) as project:
            project['settings'] = merge_settings(project['settings'], profile['settings'])
        return self.store.get(key)

    def export(self, profile_id):
        from .exporting import json_bytes
        return json_bytes({'schema': 'stacklight-profile-v1', **self.get(profile_id)})

    def import_profile(self, body):
        body = v.object_value(body, '处理方案')
        if body.get('schema') != 'stacklight-profile-v1':
            raise v.Invalid('处理方案格式不支持')
        settings = all_settings(body.get('settings'))
        settings['calibration']['dark'] = None
        settings['calibration']['flat'] = None
        settings['calibration']['enabled'] = False
        result = {'id': model.uid(), 'name': v.text(body.get('name'), '方案名称', 120, False), 'description': v.text(body.get('description', ''), '说明', 1000), 'created_at': v.timestamp(), 'settings': settings}
        self.store.write(self.store.profiles / (result['id'] + '.json'), result)
        return result
