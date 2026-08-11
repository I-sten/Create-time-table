import json
from pathlib import Path
p = Path('school_data.json')
with p.open('r', encoding='utf-8') as f:
    data = json.load(f)

days = len(data['school']['days'])
periods = len(data['school']['periods'])
print('days', days, 'periods', periods, 'slots per class', days*periods)

teacher_count = {}
class_totals = []
all_total = 0
for cls in data['classes']:
    total = sum(int(s['sessions_per_week']) for s in cls['subjects'])
    class_totals.append((cls['id'], cls['name'], total))
    all_total += total
    for s in cls['subjects']:
        teacher_count[s['teacher']] = teacher_count.get(s['teacher'], 0) + int(s['sessions_per_week'])

print('total lessons', all_total)
print('classes:')
for c in class_totals:
    print(' ', c)
print('teacher totals:')
for t, c in sorted(teacher_count.items()):
    print(' ', t, c)
print('rules:', data.get('rules'))
