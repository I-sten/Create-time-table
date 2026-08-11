import json
from pathlib import Path
from ortools.sat.python import cp_model

with Path('school_data.json').open('r', encoding='utf-8') as f:
    data = json.load(f)


def normalize_days(days):
    if isinstance(days, list):
        return [d['name'] if isinstance(d, dict) else str(d) for d in days]
    return []

def normalize_periods(periods):
    if isinstance(periods, list):
        out=[]
        for p in periods:
            if isinstance(p, dict):
                out.append({'number': int(p['number']), 'name': p.get('name', p['number'])})
            else:
                out.append({'number': int(p), 'name': str(p)})
        return out
    return []


days = normalize_days(data['school']['days'])
periods = normalize_periods(data['school']['periods'])
nd = len(days)
np = len(periods)

lessons=[]
for cls in data['classes']:
    for info in cls['subjects']:
        for n in range(int(info['sessions_per_week'])):
            lessons.append({'class_id': cls['id'], 'subject': info['subject'], 'teacher': info['teacher']})

class_groups = {}
teacher_groups = {}
for i,l in enumerate(lessons):
    class_groups.setdefault(l['class_id'], []).append(i)
    teacher_groups.setdefault(l['teacher'], []).append(i)

print('lessons', len(lessons), 'days', nd, 'periods', np)

model = cp_model.CpModel()
x = {(i,d,p): model.NewBoolVar(f'x_{i}_{d}_{p}') for i in range(len(lessons)) for d in range(nd) for p in range(np)}
for i in range(len(lessons)):
    model.Add(sum(x[i,d,p] for d in range(nd) for p in range(np)) == 1)
for groups in (class_groups, teacher_groups):
    for inds in groups.values():
        for d in range(nd):
            for p in range(np):
                model.Add(sum(x[i,d,p] for i in inds) <= 1)
solver = cp_model.CpSolver()
solver.parameters.max_time_in_seconds = 30
print('base status', solver.Solve(model))

# now add subject daily max
subject_groups = {}
for i,l in enumerate(lessons):
    key = (l['class_id'], l['subject'])
    subject_groups.setdefault(key, []).append(i)
for key, inds in subject_groups.items():
    max_day = 1
    for cls in data['classes']:
        if cls['id'] == key[0]:
            for s in cls['subjects']:
                if s['subject'] == key[1]:
                    max_day = int(s.get('max_sessions_per_day', s['sessions_per_week']))
    for d in range(nd):
        model.Add(sum(x[i,d,p] for i in inds for p in range(np)) <= max_day)
solver = cp_model.CpSolver(); solver.parameters.max_time_in_seconds=30
print('with subject max per day status', solver.Solve(model))

# add contiguous subject constraint
for key, inds in subject_groups.items():
    for d in range(nd):
        occupied = [model.NewBoolVar(f'occ_{key}_{d}_{p}') for p in range(np)]
        for p in range(np):
            model.AddMaxEquality(occupied[p], [x[i,d,p] for i in inds])
        for p in range(1,np-1):
            model.Add(occupied[p-1] + occupied[p+1] - 1 <= occupied[p])
solver = cp_model.CpSolver(); solver.parameters.max_time_in_seconds=30
print('with contiguity status', solver.Solve(model))
