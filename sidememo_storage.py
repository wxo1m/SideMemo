"""Crash-safe JSON storage and validated, isolated backup imports."""
import copy
import json
import math
import os
from pathlib import Path
import shutil
import tempfile
import uuid
import zipfile

MAX_JSON = 16 * 1024 * 1024
MAX_BACKUP = 64 * 1024 * 1024
RECOVERY_NOTICES = []


def validate_data(value, defaults):
    if not isinstance(value, dict) or not isinstance(value.get('settings'), dict):
        raise ValueError('Invalid settings')
    notes = value.get('notes')
    if not isinstance(notes, list) or not 1 <= len(notes) <= 1000:
        raise ValueError('Invalid notes')
    result = copy.deepcopy(value)
    settings = copy.deepcopy(defaults['settings'])
    settings.update(value['settings'])
    # Validate against the application defaults before any UI or filesystem change.
    for key, default in defaults['settings'].items():
        actual = settings[key]
        if isinstance(default, bool):
            valid = isinstance(actual, bool)
        elif isinstance(default, (int, float)):
            valid = isinstance(actual, (int, float)) and not isinstance(actual, bool) and math.isfinite(actual)
            if type(default) is int:
                valid = valid and type(actual) is int
        else:
            valid = isinstance(actual, type(default))
        if not valid:
            raise ValueError('Invalid setting type: ' + key)
    for key, low, high in [('opacity', 40, 100), ('delay', 0, 3600), ('sync_interval', 15, 3600)]:
        if key in settings and not low <= settings[key] <= high:
            raise ValueError('Invalid setting range: ' + key)
    if settings.get('y') is not None and type(settings['y']) is not int:
        raise ValueError('Invalid position')
    result['settings'] = settings
    ids = set()
    for index, note in enumerate(result['notes']):
        if not isinstance(note, dict):
            raise ValueError('Invalid note')
        note.setdefault('id', uuid.uuid4().hex)
        for key, default in [('title', 'NOTE'), ('html', ''), ('font', '맑은 고딕'), ('theme', '노랑'), ('attachment', '')]:
            note.setdefault(key, default)
            if not isinstance(note[key], str):
                raise ValueError('Invalid note field: ' + key)
        if not isinstance(note['id'], str) or not note['id'] or note['id'] in ids:
            raise ValueError('Invalid note ID')
        ids.add(note['id'])
        size = note.setdefault('font_size', 16)
        if type(size) is not int or not 8 <= size <= 72:
            raise ValueError('Invalid font size')
        colors = note.get('custom_color')
        if colors is not None and (not isinstance(colors, list) or len(colors) != 2 or not all(isinstance(c, str) for c in colors)):
            raise ValueError('Invalid colors')
        note['tab_slot'] = f'{index + 1}번 칸'
    if not isinstance(result.get('tombstones', {}), dict):
        raise ValueError('Invalid tombstones')
    if any(value is not True and not isinstance(value, str) for value in result.get('tombstones', {}).values()):
        raise ValueError('Invalid deletion timestamp')
    return result


def _replace_bytes(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def atomic_json(path, value, keep_backup=True):
    path = Path(path)
    content = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8')
    if keep_backup and path.exists():
        _replace_bytes(path.with_suffix(path.suffix + '.bak'), path.read_bytes())
    _replace_bytes(path, content)


def read_data(path, defaults):
    path = Path(path)
    if not path.exists():
        return copy.deepcopy(defaults)
    try:
        if path.stat().st_size > MAX_JSON:
            raise ValueError('File too large')
        return validate_data(json.loads(path.read_text(encoding='utf-8')), defaults)
    except (ValueError, UnicodeError, TypeError):
        # Preserve exact damaged bytes; never silently overwrite the only copy.
        preserved = path.with_name(path.name + '.corrupt-' + uuid.uuid4().hex)
        shutil.copyfile(path, preserved)
        backup = path.with_suffix(path.suffix + '.bak')
        recovered = copy.deepcopy(defaults)
        restored = False
        if backup.exists():
            try:
                if backup.stat().st_size <= MAX_JSON:
                    recovered = validate_data(json.loads(backup.read_text(encoding='utf-8')), defaults)
                    restored = True
            except (ValueError, UnicodeError, TypeError):
                pass
        atomic_json(path, recovered, keep_backup=False)
        RECOVERY_NOTICES.append(f'손상된 파일을 보관했습니다: {preserved}\n' + ('이전 저장본을 복구했습니다.' if restored else '복구 가능한 저장본이 없어 기본 메모로 시작합니다.'))
        return recovered


def restore_backup(path, current, defaults, data_file, attachment_dir):
    """Validate everything first; unique attachment paths prevent overwrites."""
    path = Path(path)
    attachments = {}
    if path.suffix.lower() == '.zip':
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if len(infos) > 1001 or sum(i.file_size for i in infos) > MAX_BACKUP:
                raise ValueError('Backup too large')
            if len({i.filename for i in infos}) != len(infos):
                raise ValueError('Duplicate entries')
            for info in infos:
                name = info.filename
                if name == 'sidememo.json':
                    if info.file_size > MAX_JSON:
                        raise ValueError('JSON too large')
                elif name.startswith('attachments/') and not info.is_dir():
                    leaf = name[len('attachments/'):]
                    if not leaf or any(c in leaf for c in '/\\:') or leaf in ('.', '..'):
                        raise ValueError('Invalid attachment path')
                    if (info.external_attr >> 16) & 0o170000 == 0o120000:
                        raise ValueError('Symlink not allowed')
                    attachments[leaf] = archive.read(info)
                elif not info.is_dir():
                    raise ValueError('Unexpected entry')
            value = json.loads(archive.read('sidememo.json').decode('utf-8'))
    else:
        if path.stat().st_size > MAX_JSON:
            raise ValueError('JSON too large')
        value = json.loads(path.read_text(encoding='utf-8'))
    candidate = validate_data(value, defaults)
    # Backups import content, not credentials, account ownership or sync bookkeeping.
    candidate['settings'] = copy.deepcopy(current['settings'])
    candidate['account_scope'] = current.get('account_scope', 'local')
    candidate['tombstones'] = copy.deepcopy(current.get('tombstones', {}))
    for note in current['notes']:
        candidate['tombstones'][note['id']] = True
    for note in candidate['notes']:
        note['id'] = uuid.uuid4().hex
        note.pop('synced_at', None)
        ref = note.get('attachment', '')
        if ref:
            leaf = ref.replace('\\', '/').rsplit('/', 1)[-1]
            if leaf not in attachments:
                raise ValueError('Backup attachment is missing')
            note['attachment'] = leaf
    Path(attachment_dir).mkdir(parents=True, exist_ok=True)
    # Keep the unique folder on failure too; no original file can be overwritten.
    # Unreferenced staged files are safe to clean manually if a disk error occurs.
    dest = Path(tempfile.mkdtemp(prefix='restore-', dir=attachment_dir))
    for name, content in attachments.items():
        (dest / name).write_bytes(content)
    for note in candidate['notes']:
        if note['attachment']:
            note['attachment'] = str(dest / note['attachment'])
    atomic_json(data_file, candidate)
    return candidate


def write_backup(path, data):
    """Use distinct archive names even when different images share a basename."""
    snapshot = copy.deepcopy(data)
    files = {}
    for note in snapshot['notes']:
        ref = note.get('attachment')
        if ref:
            source = Path(ref).resolve(strict=True)
            if source not in files:
                files[source] = f'{len(files)}-{source.name}'
            note['attachment'] = files[source]
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=path.parent)
    os.close(fd)
    try:
        with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('sidememo.json', json.dumps(snapshot, ensure_ascii=False, allow_nan=False))
            for source, name in files.items():
                archive.write(source, 'attachments/' + name)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
