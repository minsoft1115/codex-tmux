"""Incremental Codex JSONL reader and native tmux renderer (stdlib only)."""
from datetime import datetime
import json
import math
import os
from pathlib import Path


def number(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) and value >= 0 else None
    except OverflowError:
        return None


def context_percent(payload):
    info = payload.get('info')
    if not isinstance(info, dict):
        return None
    usage = info.get('last_token_usage')
    window = number(info.get('model_context_window'))
    used = number(usage.get('total_tokens')) if isinstance(usage, dict) else None
    return number(used / window * 100) if used is not None and window else None


class LogReader:
    """Cache each file's summaries; read only complete, newly appended lines."""
    def __init__(self, home):
        self.home = Path(home)
        self.files = {}

    def read(self, sid=None, transcript=None):
        paths = set((self.home / 'sessions').rglob('*.jsonl'))
        if transcript:
            paths.add(Path(transcript))
        for path in self.files.keys() - paths:
            del self.files[path]
        for path in sorted(paths):
            self._update(path)
        context, latest, limits = None, None, {}
        for path, cache in self.files.items():
            if sid and cache['sid'] == sid and cache['context'] is not None:
                stamp, value = cache['context']
                if latest is None or stamp > latest:
                    latest, context = stamp, value
            for minutes, entry in cache['limits'].items():
                if minutes not in limits or entry[0] > limits[minutes][0]:
                    limits[minutes] = entry
        return {'context': context, 'limits': {key: entry[1] for key, entry in limits.items()}}

    def _update(self, path):
        try:
            stat = path.stat()
            identity = (stat.st_dev, stat.st_ino)
            cache = self.files.get(path)
            if (cache is None or cache['identity'] != identity or stat.st_size < cache['offset']
                    or (stat.st_size == cache['size'] and stat.st_mtime_ns != cache['mtime'])):
                cache = {'identity': identity, 'offset': 0, 'size': -1, 'mtime': None,
                         'sid': None, 'context': None, 'limits': {}}
                self.files[path] = cache
            if (cache['size'], cache['mtime']) == (stat.st_size, stat.st_mtime_ns):
                return
            with path.open('rb') as stream:
                stream.seek(cache['offset'])
                while True:
                    line = stream.readline()
                    if not line or not line.endswith(b'\n'):
                        break
                    cache['offset'] = stream.tell()
                    try:
                        record = json.loads(line)
                    except (ValueError, UnicodeError):
                        continue
                    if not isinstance(record, dict):
                        continue
                    payload = record.get('payload')
                    if not isinstance(payload, dict):
                        continue
                    if record.get('type') == 'session_meta':
                        cache['sid'] = payload.get('id')
                    if record.get('type') != 'event_msg' or payload.get('type') != 'token_count':
                        continue
                    try:
                        date = datetime.fromisoformat(record['timestamp'].replace('Z', '+00:00'))
                        stamp = date.timestamp() if date.tzinfo else 0
                    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
                        # Undated events cannot outrank timestamped events.
                        stamp = 0
                    order = (stamp, str(path), cache['offset'])
                    value = context_percent(payload)
                    if value is not None and (cache['context'] is None or order > cache['context'][0]):
                        cache['context'] = order, value
                    rates = payload.get('rate_limits')
                    if not isinstance(rates, dict) or rates.get('limit_id') not in (None, 'codex'):
                        continue
                    for name in ('primary', 'secondary'):
                        entry = rates.get(name)
                        if not isinstance(entry, dict):
                            continue
                        minutes = number(entry.get('window_minutes'))
                        percent = number(entry.get('used_percent'))
                        if minutes not in (300, 10080) or percent is None:
                            continue
                        if minutes not in cache['limits'] or order > cache['limits'][minutes][0]:
                            cache['limits'][minutes] = order, percent
            cache.update(size=stat.st_size, mtime=stat.st_mtime_ns)
        except FileNotFoundError:
            self.files.pop(path, None)


def render_status(data, sid, columns, color=True, bar_width=10, lang='en'):
    """Keep three segmented gauges visible, coloring only their filled cells."""
    columns = max(0, columns)
    values = [data['context'], data['limits'].get(300), data['limits'].get(10080)]
    labels = ['ctx', '5h', '7d']
    na = 'N/D' if lang in ('es', 'pt', 'it') else 'N/A'
    texts = [f'{min(v, 9999):.0f}%' if v is not None else na for v in values]
    if values[0] is None:
        texts[0] = 'wait' if sid else 'prompt'
    identity = sid[:8] if sid else 'Codex'
    # Prefer keeping all three segmented gauges over the session ID and spacing.
    layouts = [(' ' + identity + ' | ', ' | ', ': '), (' ', ' | ', ': '), ('', ' ', ':')]
    for prefix, separator, colon in layouts:
        base = prefix + separator.join(label + colon + text for label, text in zip(labels, texts))
        width = min(max(0, bar_width), max(0, (columns - len(base)) // 3 - 1))
        if width < 2:
            continue
        cells = []
        for label, text, value in zip(labels, texts, values):
            filled = round(width * min(value, 100) / 100) if value is not None else 0
            used = '▓' * filled
            empty = '░' * (width - filled)
            if color and filled:
                shade = 'red' if value >= 90 else 'yellow' if value >= 70 else 'green'
                used = '#[fg=' + shade + ']' + used + '#[fg=default]'
            cells.append(label + colon + used + empty + ' ' + text)
        return prefix + separator.join(cells)
    base = ' ' + identity + ' | ' + ' | '.join(f'{label}: {text}' for label, text in zip(labels, texts))
    if len(base) <= columns:
        return base
    compact = ' '.join(f'{label}:{text}' for label, text in zip(labels, texts))
    return compact[:columns]


def display_options():
    try:
        width = max(0, min(40, int(os.environ.get('CODEX_TMUX_BAR_WIDTH', '10'))))
    except ValueError:
        width = 10
    return {'color': 'NO_COLOR' not in os.environ, 'bar_width': width,
            'lang': os.environ.get('USAGE_BAR_LANG', 'en')}
