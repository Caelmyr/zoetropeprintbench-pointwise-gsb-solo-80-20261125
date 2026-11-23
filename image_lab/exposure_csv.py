import csv
import io
from . import validation as v
from . import model


def safe_cell(value):
    text = str(value)
    if text.startswith(('=', '+', '-', '@', '\t', '\r')):
        return "'" + text
    return text


def export_csv(project):
    stream = io.StringIO(newline='')
    writer = csv.writer(stream)
    writer.writerow(['frame_id', 'name', 'exposure', 'ev', 'weight', 'enabled', 'rating'])
    for frame in project['frames']:
        writer.writerow([frame['id'], safe_cell(frame['name']), frame['exposure'], frame['ev'], frame['weight'], int(frame['enabled']), frame['rating']])
    return ('\ufeff' + stream.getvalue()).encode('utf8')


def parse_csv(text, project):
    if isinstance(text, str):
        text = text.replace('\r\n', '\n').replace('\r', '\n')
    text = v.text(text, '曝光CSV', 64000, False).lstrip('\ufeff')
    stream = io.StringIO(text)
    reader = csv.DictReader(stream)
    if reader.fieldnames is None or 'frame_id' not in reader.fieldnames:
        raise v.Invalid('CSV需要frame_id列')
    result = []
    seen = set()
    for row in reader:
        frame_id = v.identifier(row.get('frame_id'))
        model.find_frame(project, frame_id)
        if frame_id in seen:
            raise v.Invalid('CSV帧编号重复')
        seen.add(frame_id)
        values = {'id': frame_id}
        for field, limits in [('exposure', (.000001, 3600)), ('ev', (-12, 12)), ('weight', (0, 8))]:
            if row.get(field, '').strip():
                values[field] = v.number(row[field], field, *limits)
        if row.get('enabled', '').strip():
            values['enabled'] = bool(v.integer(row['enabled'], 'enabled', 0, 1))
        if row.get('rating', '').strip():
            values['rating'] = v.integer(row['rating'], 'rating', 0, 5)
        result.append(values)
        if len(result) > 24:
            raise v.Invalid('CSV帧数超过24')
    if not result:
        raise v.Invalid('CSV没有照片记录')
    return result


def import_csv(store, key, revision, text):
    parsed = parse_csv(text, store.get(key))
    with store.edit(key, revision, '批量导入曝光参数CSV') as project:
        for entry in parsed:
            current = model.find_frame(project, entry['id'])
            for field, value in entry.items():
                if field != 'id':
                    current[field] = value
    return store.get(key)
